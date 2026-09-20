"""Offline contract arithmetic, atomic settlement and liquidation checks."""
from __future__ import annotations

import asyncio
from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from test_offline import setUpModule, tearDownModule
from test_balance import interaction


def quote(price=100, timestamp=100, symbol="TEST"):
    return SimpleNamespace(symbol=symbol, name="Synthetic asset", price=price,
                           currency="USD", market="test", price_time=timestamp,
                           fetched_at=timestamp, provider="Synthetic")


def position(side="long"):
    return {"id": "synthetic", "symbol": "TEST", "name": "Synthetic asset",
            "currency": "USD", "market": "test", "side": side, "margin": 1000,
            "leverage": 5.0, "quantity": "50", "entry_price": "100",
            "entry_fee": "2.75", "fee_rate": "0.00055", "maintenance_rate": "0.005",
            "last_price_time": 100}


class InvestmentMath(unittest.TestCase):
    def test_long_short_profit_fees_and_net_equity(self):
        from investment import value_position
        for side, price, gross, exit_fee, equity in [
            ("long", 110, 500, "3.025", "1494.225"),
            ("long", 90, -500, "2.475", "494.775"),
            ("short", 90, 500, "2.475", "1494.775"),
            ("short", 110, -500, "3.025", "494.225"),
        ]:
            result = value_position(position(side), price)
            self.assertEqual(result.gross_pnl, Decimal(gross))
            self.assertEqual(result.exit_fee, Decimal(exit_fee))
            self.assertEqual(result.equity, Decimal(equity))
            self.assertEqual(result.net_pnl, Decimal(equity) - 1000)

    def test_leverage_not_multiplied_into_existing_quantity_twice(self):
        from investment import value_position
        record = position()
        before = value_position(record, 110)
        record["leverage"] = 1.0
        after = value_position(record, 110)
        self.assertEqual(before.gross_pnl, after.gross_pnl)

    def test_budget_covers_margin_and_opening_fee_exactly(self):
        from investment import open_position, decimal
        user = {"balance": 10**12}
        result = open_position(user, quote(), 10**9, 5, "long", 100)
        self.assertTrue(result["ok"])
        notional = decimal(result["notional"])
        fee = decimal(result["entry_fee"])
        self.assertLess(abs(notional / 5 + fee - 10**9), Decimal("0.000001"))
        self.assertEqual(user["balance"], 10**12 - 10**9)

    def test_liquidation_before_zero_equity_and_gap_loss_is_isolated(self):
        from investment import value_position
        # A mark at 80.4 breaches maintenance while some margin remains.
        long = value_position(position("long"), "80.4")
        short = value_position(position("short"), "119.4")
        for value in [long, short]:
            self.assertTrue(value.liquidated)
            self.assertGreater(value.equity, 0)
            self.assertLessEqual(value.equity, value.maintenance)
        self.assertFalse(value_position(position("long"), 81).liquidated)
        self.assertFalse(value_position(position("short"), 119).liquidated)
        self.assertEqual(value_position(position("long"), 50).equity, 0)
        self.assertEqual(value_position(position("short"), 200).equity, 0)

    def test_invalid_prices_never_produce_payouts(self):
        from investment import value_position
        for bad in [0, -1, "NaN", "Infinity", "-Infinity"]:
            with self.assertRaises(ValueError):
                value_position(position(), bad)

    def test_legacy_positions_keep_quantity_and_do_not_gain_retroactive_opening_fees(self):
        from investment import value_position
        old = position()
        old.pop("entry_fee")
        old.pop("fee_rate")
        old.pop("maintenance_rate")
        old["entry_price"] = 100.0
        old["quantity"] = 50.0
        result = value_position(old, 110)
        self.assertEqual(result.entry_fee, 0)
        self.assertEqual(result.gross_pnl, 500)
        self.assertEqual(result.equity, Decimal("1496.975"))


class InvestmentFlows(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from database import Database
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.directory.name) / "synthetic-investment.json"))
        await self.db.set_balance(1, 10000)

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def open(self, amount=1000, mark=None, leverage=5, side="long"):
        from investment import open_position
        return await self.db.mutate_user(1, lambda user: open_position(user, mark or quote(), amount, leverage, side, 100))

    async def test_add_to_position_uses_quantity_weighted_average_and_accumulates_fees(self):
        from investment import decimal
        first = await self.open()
        second = await self.open(mark=quote(200, 200))
        current = second["position"]
        self.assertTrue(second["merged"])
        self.assertEqual(first["position"]["id"], current["id"])
        self.assertEqual(current["margin"], 2000)
        self.assertAlmostEqual(float(decimal(current["entry_price"])), 133.3333333333333)
        with localcontext() as context:
            context.prec = 64
            self.assertEqual(decimal(current["entry_fee"]), 2 * decimal(first["entry_fee"]))

    async def test_close_prices_current_state_after_concurrent_add_and_pays_only_once(self):
        from investment import close_position, value_position
        opened = await self.open()
        old_id = opened["position"]["id"]
        # A sell command may have read its snapshot before this add completed.
        added = await self.open(mark=quote(110, 101))
        expected = int(value_position(added["position"], 120).equity)
        results = await asyncio.gather(*(
            self.db.mutate_user(1, lambda user: close_position(user, old_id, quote(120, 102), 102))
            for _ in range(3)
        ))
        self.assertEqual(sum(r["ok"] for r in results), 1)
        self.assertEqual(await self.db.get_balance(1), 8000 + expected)
        data = await self.db.get_user_data(1)
        self.assertFalse(data["invest_positions"])
        self.assertEqual(len(data["invest_transactions"]), 3)

    async def test_background_liquidation_is_persistent_and_cannot_recover_later(self):
        from cogs import invest
        from database import Database
        await self.open()
        cog = invest.Invest(None)
        with patch.object(invest, "db", self.db), patch.object(cog.prices, "quote", new_callable=AsyncMock, return_value=quote(70, 101)):
            self.assertEqual(await cog.check_liquidations(), 1)
            self.assertEqual(await cog.check_liquidations(), 0)
        reloaded = Database(self.db.path)
        self.assertFalse((await reloaded.get_user_data(1))["invest_positions"])
        self.assertEqual(await reloaded.get_balance(1), 9000)
        with patch.object(invest, "db", reloaded), patch.object(cog.prices, "quote", new_callable=AsyncMock, return_value=quote(150, 102)):
            self.assertEqual(await cog.check_liquidations(), 0)
        history = (await reloaded.get_user_data(1))["invest_transactions"]
        self.assertEqual(history[-1]["type"], "liquidation")
        self.assertEqual(history[-1]["pnl"], -1000)

    async def test_quote_failure_leaves_positions_and_wallet_untouched(self):
        from cogs import invest
        await self.open()
        before = await self.db.get_user_data(1)
        cog = invest.Invest(None)
        with patch.object(invest, "db", self.db), patch.object(cog.prices, "quote", new_callable=AsyncMock, side_effect=OSError("offline")):
            self.assertEqual(await cog.check_liquidations(), 0)
        self.assertEqual(await self.db.get_user_data(1), before)

    async def test_old_quote_cannot_liquidate_or_close_newer_state(self):
        from investment import close_position, liquidate_user
        result = await self.open(mark=quote(100, 200))
        pid = result["position"]["id"]
        closed = await self.db.mutate_user(1, lambda user: liquidate_user(user, quote(1, 199), 201))
        self.assertEqual(closed, [])
        result = await self.db.mutate_user(1, lambda user: close_position(user, pid, quote(1, 199), 201))
        self.assertFalse(result["ok"])
        self.assertEqual(await self.db.get_balance(1), 9000)

    async def test_bank_and_game_funds_not_used_as_cross_margin(self):
        from investment import liquidate_user
        await self.db.mutate_user(1, lambda user: user.update(bank_balance=10**9))
        await self.open()
        await self.db.mutate_user(1, lambda user: liquidate_user(user, quote(1, 101), 101))
        user = await self.db.get_user_data(1)
        self.assertEqual(user["bank_balance"], 10**9)
        self.assertEqual(user["balance"], 9000)

    async def test_portfolio_and_sell_render_decimal_positions_without_network(self):
        from cogs import invest
        await self.open()
        cog = invest.Invest(None)
        request = interaction()
        request.user.display_name = "Synthetic player"
        with patch.object(invest, "db", self.db), patch.object(cog.prices, "quote", new_callable=AsyncMock, return_value=quote(110, 101)):
            await cog.portfolio.callback(cog, request)
            embed = request.followup.send.call_args.kwargs["embed"]
            self.assertIn("費後估值", embed.description)
            self.assertLess(len(embed), 6000)
            pid = next(iter((await self.db.get_user_data(1))["invest_positions"]))
            await cog.sell.callback(cog, request, pid)
            self.assertIn("淨損益", request.followup.send.call_args.kwargs["embed"].description)
            await cog.transactions.callback(cog, request, 20)
            self.assertIn("平倉費", request.followup.send.call_args.kwargs["embed"].description)

    async def test_discord_amount_fields_have_no_application_maximum(self):
        import discord
        from discord.ext import commands
        from bot import COG_MODULES
        client = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        async with client:
            for module in COG_MODULES:
                await client.load_extension(module)
            commands_by_name = {cmd.qualified_name: cmd for cmd in client.tree.walk_commands()}
            for name in ["baccarat", "mines", "dice", "balloon", "climb", "crash", "slot", "roulette", "invest buy", "bank deposit", "bank withdraw"]:
                params = commands_by_name[name].parameters
                amount = next(p for p in params if p.name == "amount")
                self.assertIsNone(amount.max_value, name)
            leverage = next(p for p in commands_by_name["invest buy"].parameters if p.name == "leverage")
            self.assertEqual(leverage.max_value, 5)


if __name__ == "__main__":
    unittest.main()
