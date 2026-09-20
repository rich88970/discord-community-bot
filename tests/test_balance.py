"""Economy regression checks using synthetic accounts and no network calls."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta
from itertools import product
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from test_offline import setUpModule, tearDownModule


def interaction():
    return SimpleNamespace(
        user=SimpleNamespace(id=1),
        response=SimpleNamespace(is_done=lambda: True, defer=AsyncMock(), send_message=AsyncMock(), edit_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


def baccarat_probabilities():
    """Exhaust the actual deal/third-card code with weighted hand totals."""
    from cogs import baccarat
    cards = [("10" if n == 0 else "A" if n == 1 else str(n), "S") for n in range(10)]
    weights = [4] + [1] * 9
    totals = Counter()
    for a, b in product(range(10), repeat=2):
        totals[(a + b) % 10] += weights[a] * weights[b]
    results = Counter()
    for p, b, third, fourth in product(range(10), repeat=4):
        draws = iter([cards[0], cards[p], cards[0], cards[b], cards[third], cards[fourth]])
        with patch.object(baccarat, "deal_card", new=lambda: next(draws)):
            winner = baccarat.play_baccarat()[-1]
        results[winner] += totals[p] * totals[b] * weights[third] * weights[fourth]
    total_weight = sum(results.values())
    return {key: value / total_weight for key, value in results.items()}


class BalanceMath(unittest.TestCase):
    def test_dice_and_roulette_returns_including_large_bets(self):
        import config
        from cogs import dice, roulette
        from cogs.gamble_items import calculate_payout
        outcomes = Counter()
        for a, b, c in product(range(1, 7), repeat=3):
            outcomes["triple" if a == b == c else "big" if a + b + c >= 11 else "small"] += 1
        self.assertEqual(outcomes, {"big": 105, "small": 105, "triple": 6})
        for bet in list(range(config.MIN_GAMBLE_BET, 501)) + [10**6, 10**12]:
            for chance, multiplier in [(105 / 216, dice.BIG_SMALL_PAYOUT), (6 / 216, dice.TRIPLE_PAYOUT),
                                       (18 / 37, roulette.EVEN_MONEY_PAYOUT), (1 / 37, roulette.NUMBER_PAYOUT)]:
                self.assertLessEqual(chance * calculate_payout(bet, multiplier) / bet, config.EV_TARGET + 1e-12)

    def test_baccarat_return_includes_tie_refunds(self):
        from cogs import baccarat
        from cogs.gamble_items import calculate_payout
        probabilities = baccarat_probabilities()
        for side, multiplier in [("player", baccarat.PLAYER_PAYOUT), ("banker", baccarat.BANKER_PAYOUT), ("tie", baccarat.TIE_PAYOUT)]:
            push = probabilities["tie"] if side != "tie" else 0
            self.assertAlmostEqual(probabilities[side] * multiplier + push, 0.99)
            for bet in range(10, 501):
                self.assertLess(probabilities[side] * calculate_payout(bet, multiplier) + push * bet, bet)

    def test_slot_exhaustive_return_and_purchased_items(self):
        import config
        from cogs import slot
        from cogs.gamble_items import calculate_payout, apply_settlement_items
        total = sum(w for _, w, _ in slot.SYMBOLS)
        events = []
        for a, b, c in product(slot.SYMBOLS, repeat=3):
            multiplier = slot.lookup_payout(a[0]) if a[0] == b[0] == c[0] else slot.PAIR_PAYOUT if a[0] == b[0] else 0
            events.append((a[1] * b[1] * c[1] / total ** 3, multiplier))
        self.assertAlmostEqual(sum(p * m for p, m in events), config.EV_TARGET)
        for bet in [10, 50, 100, 250, 500]:
            for items in [[], ["insurance"], ["bonus"], ["insurance", "bonus"]]:
                expected = sum(p * apply_settlement_items(bet, calculate_payout(bet, m), 0, items)[0] for p, m in events)
                cost = sum(config.SHOP_ITEMS[item]["price"] for item in items)
                self.assertLess(expected, bet + cost)

    def test_balloon_and_mines_fixed_cashout_returns(self):
        import math
        import config
        from cogs.balloon import pop_chance
        from cogs.minesweeper import fair_multiplier, GRID_SIZE
        from cogs.gamble_items import calculate_payout
        for bet in [10, 100, 500]:
            survival = 1.0
            for step in range(1, 30):
                survival *= 1 - pop_chance(step)
                self.assertLessEqual(survival * calculate_payout(bet, config.EV_TARGET / survival), bet * config.EV_TARGET + 1e-9)
            for mines in [3, 7, 12]:
                for safe in range(1, GRID_SIZE - mines + 1):
                    survival = math.comb(GRID_SIZE - mines, safe) / math.comb(GRID_SIZE, safe)
                    self.assertLessEqual(survival * calculate_payout(bet, fair_multiplier(mines, safe)), bet * config.EV_TARGET + 1e-9)

    def test_climb_every_step_accounts_for_treasure_and_current_winnings(self):
        import config
        from cogs import climb
        # A conditional expectation below the current winnings at every state
        # also covers changing the cashout strategy after observing past tiers.
        for mode in climb.MODES.values():
            for floor in range(1, mode["floors"] + 1):
                target = config.EV_TARGET if floor == 1 else config.CLIMB_CONTINUE_RETURN
                for current in list(range(10, 5001)) + [10**6, 10**9]:
                    rewards = climb._floor_reward_amounts(current, floor, mode)
                    normal = sum(current + gain for gain in rewards) / 4
                    treasure = normal + (current + climb._super_reward_amount(rewards)) / 4
                    expected = (1 - climb.LUCKY_FLOOR_CHANCE) * normal + climb.LUCKY_FLOOR_CHANCE * treasure
                    self.assertLessEqual(expected, current * target + max(1e-9, current * 1e-12))

    def test_crash_has_initial_busts_and_unrounded_tail(self):
        import config
        from cogs import crash
        with patch.object(crash.random, "random", return_value=0):
            self.assertLess(crash.random_crash_point(), 1)
        # Deterministic midpoint grid checks the distribution, not a flaky RNG run.
        samples = 10000
        points = []
        for i in range(samples):
            with patch.object(crash.random, "random", return_value=(i + 0.5) / samples):
                points.append(crash.random_crash_point())
        for target in [1, 1.01, 1.15, 2, 5, 10]:
            returned = sum(p >= target for p in points) / samples * target
            self.assertAlmostEqual(returned, config.EV_TARGET, delta=target / samples)

    def test_insurance_partial_loss_and_uncapped_bonus(self):
        import discord
        import config
        from cogs.gamble_items import apply_settlement_items, add_item_lines_field
        for payout in [0, 1, 250, 499]:
            adjusted, profit, lines = apply_settlement_items(500, payout, payout - 500, ["insurance"])
            self.assertLess(adjusted, 500)
            self.assertLess(profit, 0)
            embed = discord.Embed()
            add_item_lines_field(embed, lines)
            self.assertEqual(len(embed.fields), 1)
        payout, profit, _ = apply_settlement_items(500, 5000, 4500, ["bonus"])
        self.assertEqual(payout, 6125)
        self.assertEqual(profit, payout - 500)


class BalanceFlows(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from database import Database
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "synthetic.json")
        self.db = Database(self.path)

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def test_claim_has_no_daily_limit_but_keeps_slot_cooldown(self):
        import config
        from cogs import economy
        from database import Database
        now = datetime(2026, 1, 1, tzinfo=economy.LOCAL_TZ)
        initial = await self.db.get_balance(1)
        cog = economy.Economy(None)
        # Old daily-limit counters must not block any claim.
        await self.db.mutate_user(1, lambda user: user.update(claim_count=999, claim_count_date="2026-01-01"))
        with patch.object(economy, "db", self.db):
            with patch.object(economy, "local_now", return_value=now):
                await asyncio.gather(*(cog.claim.callback(cog, interaction()) for _ in range(3)))
            for slot in range(1, 96):
                with patch.object(economy, "local_now", return_value=now + timedelta(minutes=15 * slot)):
                    await cog.claim.callback(cog, interaction())
        expected = initial + 96 * config.CLAIM_AMOUNT
        self.assertEqual(await self.db.get_balance(1), expected)
        reloaded = Database(self.path)
        with patch.object(economy, "db", reloaded):
            with patch.object(economy, "local_now", return_value=now + timedelta(hours=23, minutes=59)):
                await cog.claim.callback(cog, interaction())
                self.assertEqual(await reloaded.get_balance(1), expected)
            with patch.object(economy, "local_now", return_value=now + timedelta(days=1)):
                await cog.claim.callback(cog, interaction())
                await cog.daily.callback(cog, interaction())
                await cog.daily.callback(cog, interaction())
        self.assertEqual(await reloaded.get_balance(1), expected + config.CLAIM_AMOUNT + config.DAILY_AMOUNT)

    async def test_bank_interest_is_one_percent_without_a_cap(self):
        for principal in [99, 1000, 2000, 10000, 10**12, 10**30 + 999]:
            user = {"bank_balance": principal, "balance": 0, "bank_interest_period": 94}
            with patch.object(self.db, "_current_bank_interest_period", return_value=100):
                self.db._apply_bank_interest(user)
                expected = (principal // 100) * 6
                self.assertEqual(user["balance"], expected)
                self.db._apply_bank_interest(user)
                self.assertEqual(user["balance"], expected)
                self.assertEqual(user["bank_balance"], principal)

    async def test_game_minimum_and_unlimited_rematch(self):
        from cogs import gamble_items, _rematch
        initial = await self.db.get_balance(1)
        for amount in [-100, 0, 9]:
            self.assertIsNone(await self.db.place_bet(1, amount, "test"))
            with patch.object(gamble_items, "db", self.db):
                self.assertIsNone(await gamble_items.place_bet_or_error(interaction(), amount, "test", [], is_followup=False))
        self.assertEqual(await self.db.get_balance(1), initial)
        await self.db.set_balance(1, 10**12)
        callback = AsyncMock()
        view = _rematch.RematchView(1, 10**9, callback)
        with patch.object(_rematch, "db", self.db):
            await view._do_replay(interaction(), 2)
        callback.assert_awaited_once()
        self.assertEqual(callback.call_args.args[1], 2 * 10**9)
        view.stop()
        self.assertIsNotNone(await self.db.place_bet(1, 2 * 10**9, "test"))

    async def test_settlement_duplicate_or_after_refund_does_not_mint_money(self):
        initial = await self.db.get_balance(1)
        pending = await self.db.place_bet(1, 100, "test")
        results = await asyncio.gather(*(self.db.settle_bet(1, pending, 100, 100, payout=200) for _ in range(3)))
        self.assertEqual(results, [initial + 100] * 3)
        stats = await self.db.get_stats(1)
        self.assertEqual(stats["wagered"], 100)
        pending = await self.db.place_bet(1, 100, "test")
        await self.db.cancel_pending_bet(1, pending, 100)
        await self.db.settle_bet(1, pending, 100, 900, payout=1000)
        self.assertEqual(await self.db.get_balance(1), initial + 100)

    async def test_database_pays_full_large_prize(self):
        initial = await self.db.get_balance(1)
        pending = await self.db.place_bet(1, 100, "test")
        await self.db.settle_bet(1, pending, 100, 10**30 - 100, payout=10**30)
        self.assertEqual(await self.db.get_balance(1), initial - 100 + 10**30)
        stats = await self.db.get_stats(1)
        self.assertEqual(stats["profit"], 10**30 - 100)

    async def test_climb_preview_does_not_reveal_safe_floors(self):
        from cogs.climb import ClimbView
        view = ClimbView(None, 1, 100, "easy", "synthetic")
        before = view._next_floor_preview()
        view.floors[0] = [{"kind": "reward", "amount": 99999}] * 4
        self.assertEqual(view._next_floor_preview(), before)
        view.stop()

    async def test_mines_defuse_does_not_raise_multiplier_or_allow_repeat_cell(self):
        from cogs import minesweeper as mines
        view = mines.MinesView(None, 1, 100, 3, "synthetic", ["defuse"])
        view.mines = {0, 1, 2}
        with patch.object(mines, "safe_view_edit", new_callable=AsyncMock):
            await view.children[0].callback(interaction())
            self.assertEqual(view.multiplier, 1.0)
            self.assertEqual(view.defused_count, 1)
            await view.children[3].callback(interaction())
            self.assertAlmostEqual(view.multiplier, 0.99 * 19 / 17)
            previous = view.multiplier
            await view.children[3].callback(interaction())
            self.assertEqual(view.multiplier, previous)
        view.stop()

    async def test_mines_without_items_matches_combinatorial_formula(self):
        import config
        from cogs.minesweeper import MinesView, fair_multiplier
        for mine_count in [3, 7, 12]:
            view = MinesView(None, 1, 100, mine_count, "synthetic")
            for safe in range(1, 21 - mine_count):
                view.revealed.add(safe)
                view.update_multiplier()
                self.assertAlmostEqual(view.multiplier, fair_multiplier(mine_count, safe))
            view.stop()

    async def test_crash_immediate_bust_and_duplicate_cashout(self):
        from cogs import crash
        view = crash.CrashView(None, 1, 100, 1.01, "synthetic")
        view.crash_point = 0.98
        with patch.object(crash, "settle_bet_with_items", new_callable=AsyncMock, return_value=(900, 0, -100, [])) as settle:
            await view._loop()
            self.assertTrue(view.finished)
            self.assertEqual(settle.call_args.args[3], 0)
            await view.cash_out(interaction())
            settle.assert_awaited_once()

    async def test_investment_has_no_budget_cap_and_maximum_five_times_leverage(self):
        from cogs import invest
        await self.db.set_balance(1, 10**12)
        cog = invest.Invest(None)
        quote = SimpleNamespace(symbol="TEST", name="Synthetic asset", price=100.0, currency="USD", market="test", price_time=100)
        with patch.object(invest, "db", self.db), patch.object(cog, "_quote_or_reply", new_callable=AsyncMock, return_value=quote) as request:
            for _ in range(2):
                await cog.buy.callback(cog, interaction(), "TEST", 10**9, 5.0)
            quote.symbol = "TEST2"
            await cog.buy.callback(cog, interaction(), "TEST2", 10**9, 1.0)
            self.assertEqual(await self.db.get_balance(1), 10**12 - 3 * 10**9)
            self.assertEqual(len((await self.db.get_user_data(1))["invest_positions"]), 2)
            request.reset_mock()
            await cog.buy.callback(cog, interaction(), "TEST", 99, 1.0)
            await cog.buy.callback(cog, interaction(), "TEST", 100, 5.01)
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
