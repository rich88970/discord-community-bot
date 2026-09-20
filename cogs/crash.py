"""Crash 倍率崩盤 /crash

下注後倍率每 1.5 秒上升一次，玩家須在崩盤前按 Cash Out 領走。
玩家也可以在指令中指定 auto_target 自動兌現。

注意：「雙倍再來」按鈕只會把下注金額加倍，auto_target 不會跟著變。
"""

from __future__ import annotations

import asyncio
import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import visuals
from cogs._rematch import (
    RematchView,
    refund_failed_start,
    safe_view_edit,
    send_error,
    send_initial,
    try_defer,
)
from cogs.gamble_items import (
    calculate_payout,
    add_item_lines_field,
    place_bet_or_error,
    selected_items,
    settle_bet_with_items,
)
from database import db


TICK_INTERVAL = 1.5
MULT_PER_TICK = 0.15


def random_crash_point() -> float:
    r = random.random()
    if r >= 1.0:
        r = 0.999999
    return config.EV_TARGET / (1.0 - r)


class CrashCashOut(discord.ui.Button):
    def __init__(self):
        super().__init__(label="💰 Cash Out", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: "CrashView" = self.view  # type: ignore[assignment]
        if interaction.user.id != view.player_id:
            await interaction.response.send_message(
                "這不是你的局！", ephemeral=True
            )
            return
        if view.finished:
            await try_defer(interaction)
            return
        await view.cash_out(interaction)


class CrashView(discord.ui.View):
    def __init__(
        self,
        cog: "Crash",
        player_id: int,
        bet: int,
        auto_target: Optional[float],
        pending_bet_id: str,
        item_ids: list[str] | None = None,
    ):
        super().__init__(timeout=300)
        self.cog = cog
        self.player_id = player_id
        self.bet = bet
        self.pending_bet_id = pending_bet_id
        self.item_ids = list(item_ids or [])
        self.auto_target = auto_target
        self.crash_point = random_crash_point()
        self.multiplier = 1.00
        self.finished = False
        self.message: Optional[discord.Message] = None
        self._task: Optional[asyncio.Task] = None
        self.add_item(CrashCashOut())

    def build_embed(self, status: str = "📈 上升中…") -> discord.Embed:
        potential = calculate_payout(self.bet, self.multiplier)
        meter = visuals.crash_meter(self.multiplier, width=20)
        rocket = visuals.crash_rocket(self.multiplier)
        desc = (
            f"```\n{rocket}\n```\n"
            f"{meter}　**x{self.multiplier:.2f}**\n\n"
            f"下注：**{self.bet:,}**　領回：**{potential:,}**"
        )
        if self.auto_target:
            desc += f"\n自動兌現：**x{self.auto_target:.2f}**"
        embed = discord.Embed(
            title=f"🚀 Crash {status}",
            description=desc,
            color=config.INFO_COLOR,
        )
        return embed

    def _make_rematch(self) -> RematchView:
        # auto_target 沿用本局，但下注金額會根據按鈕被乘 1 或 2
        auto_target = self.auto_target
        return RematchView(
            self.player_id,
            self.bet,
            lambda inter, amt: self.cog._play_round(
                inter, amt, auto_target, is_followup=True
            ),
        )

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def on_timeout(self) -> None:
        if self.finished:
            return
        self.finished = True
        for child in self.children:
            child.disabled = True
        if self._task is not None:
            self._task.cancel()

        item_lines: list[str] = []
        if self.multiplier >= self.crash_point:
            new_balance, payout, profit, item_lines = await settle_bet_with_items(
                self.player_id,
                self.pending_bet_id,
                self.bet,
                0,
                -self.bet,
                self.item_ids,
            )
            embed = discord.Embed(
                title="⏳ Crash 逾時後已崩盤",
                description=(
                    f"崩盤點：**x{self.crash_point:.2f}**\n"
                    f"輸了 **{self.bet:,}**\n"
                    f"目前餘額：**{new_balance:,}**"
                ),
                color=config.LOSE_COLOR,
            )
        else:
            payout = calculate_payout(self.bet, self.multiplier)
            profit = payout - self.bet
            new_balance, payout, profit, item_lines = await settle_bet_with_items(
                self.player_id,
                self.pending_bet_id,
                self.bet,
                payout,
                profit,
                self.item_ids,
            )
            embed = discord.Embed(
                title=f"⏳ Crash 逾時，自動兌現 x{self.multiplier:.2f}",
                description=(
                    f"領回 **{payout:,}**（淨利 **{profit:+,}**）\n"
                    f"本局崩盤點：**x{self.crash_point:.2f}**\n"
                    f"目前餘額：**{new_balance:,}**"
                ),
                color=config.WIN_COLOR if profit > 0 else config.INFO_COLOR,
            )
        add_item_lines_field(embed, item_lines)
        if self.message is not None:
            try:
                await self.message.edit(embed=embed, view=self._make_rematch())
            except discord.HTTPException:
                pass
        self.stop()

    async def _loop(self) -> None:
        try:
            if self.crash_point < 1.0:
                await self.bust()
                return
            while not self.finished:
                await asyncio.sleep(TICK_INTERVAL)
                if self.finished:
                    return
                self.multiplier = round(self.multiplier + MULT_PER_TICK, 2)

                if self.auto_target and self.multiplier >= self.auto_target:
                    self.multiplier = self.auto_target
                    if self.multiplier <= self.crash_point:
                        await self._auto_cashout()
                        return

                if self.multiplier >= self.crash_point:
                    await self.bust()
                    return

                if self.message is not None:
                    try:
                        await self.message.edit(embed=self.build_embed())
                    except discord.HTTPException:
                        pass
        except asyncio.CancelledError:
            pass

    async def cash_out(self, interaction: discord.Interaction) -> None:
        if self.finished:
            await try_defer(interaction)
            return
        if self.multiplier > self.crash_point:
            await self.bust()
            await try_defer(interaction)
            return
        self.finished = True
        payout = calculate_payout(self.bet, self.multiplier)
        profit = payout - self.bet
        new_balance, payout, profit, item_lines = await settle_bet_with_items(
            self.player_id,
            self.pending_bet_id,
            self.bet,
            payout,
            profit,
            self.item_ids,
        )
        meter = visuals.crash_meter(self.multiplier, width=20)
        embed = discord.Embed(
            title=f"💰 Cash Out at x{self.multiplier:.2f}",
            description=(
                f"{meter}\n\n"
                f"領回 **{payout:,}**（淨利 **{profit:+,}**）\n"
                f"本局崩盤點：**x{self.crash_point:.2f}**\n"
                f"目前餘額：**{new_balance:,}**"
            ),
            color=config.WIN_COLOR,
        )
        add_item_lines_field(embed, item_lines)
        await safe_view_edit(
            interaction,
            embed=embed,
            view=self._make_rematch(),
            message=self.message,
        )
        self.stop()

    async def _auto_cashout(self) -> None:
        if self.finished:
            return
        self.finished = True
        payout = calculate_payout(self.bet, self.multiplier)
        profit = payout - self.bet
        new_balance, payout, profit, item_lines = await settle_bet_with_items(
            self.player_id,
            self.pending_bet_id,
            self.bet,
            payout,
            profit,
            self.item_ids,
        )
        meter = visuals.crash_meter(self.multiplier, width=20)
        embed = discord.Embed(
            title=f"🤖 自動兌現 x{self.multiplier:.2f}",
            description=(
                f"{meter}\n\n"
                f"領回 **{payout:,}**（淨利 **{profit:+,}**）\n"
                f"本局崩盤點：**x{self.crash_point:.2f}**\n"
                f"目前餘額：**{new_balance:,}**"
            ),
            color=config.WIN_COLOR,
        )
        add_item_lines_field(embed, item_lines)
        if self.message is not None:
            try:
                await self.message.edit(embed=embed, view=self._make_rematch())
            except discord.HTTPException:
                pass
        self.stop()

    async def bust(self) -> None:
        if self.finished:
            return
        self.finished = True
        new_balance, payout, profit, item_lines = await settle_bet_with_items(
            self.player_id,
            self.pending_bet_id,
            self.bet,
            0,
            -self.bet,
            self.item_ids,
        )
        meter = visuals.crash_meter(self.crash_point, width=20)
        embed = discord.Embed(
            title=f"💥 崩盤於 x{self.crash_point:.2f}",
            description=(
                f"{meter}\n\n"
                f"輸了 **{self.bet:,}**\n"
                f"目前餘額：**{new_balance:,}**"
            ),
            color=config.LOSE_COLOR,
        )
        add_item_lines_field(embed, item_lines)
        if self.message is not None:
            try:
                await self.message.edit(embed=embed, view=self._make_rematch())
            except discord.HTTPException:
                pass
        self.stop()


class Crash(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="crash", description="Crash 倍率崩盤遊戲")
    @app_commands.describe(
        amount=f"下注 {config.MIN_GAMBLE_BET}–{config.MAX_GAMBLE_BET}；每局含道具最多領回 {config.MAX_GAMBLE_PAYOUT:,}",
        auto_target="（選填）自動兌現倍率，例如 2.0",
    )
    async def crash(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, config.MIN_GAMBLE_BET, config.MAX_GAMBLE_BET],
        auto_target: app_commands.Range[float, 1.01, 1000.0] | None = None,
        insurance: bool = False,
        bonus: bool = False,
    ) -> None:
        await self._play_round(
            interaction,
            amount,
            auto_target,
            item_ids=selected_items(insurance=insurance, bonus=bonus),
            is_followup=False,
        )

    async def _play_round(
        self,
        interaction: discord.Interaction,
        amount: int,
        auto_target: Optional[float],
        *,
        item_ids: list[str] | None = None,
        is_followup: bool,
    ) -> None:
        if not await try_defer(interaction):
            return
        bet_result = await place_bet_or_error(
            interaction, amount, "crash", item_ids, is_followup=is_followup
        )
        if bet_result is None:
            return
        pending_bet_id, used_items = bet_result

        try:
            view = CrashView(
                self, interaction.user.id, amount, auto_target, pending_bet_id, used_items
            )
            message = await send_initial(
                interaction,
                view.build_embed("起飛！"),
                view=view,
                is_followup=True,
            )
            view.message = message
            view.start()
        except discord.DiscordException:
            await refund_failed_start(interaction, amount, pending_bet_id=pending_bet_id)
            return
        except Exception:
            await refund_failed_start(interaction, amount, pending_bet_id=pending_bet_id)
            raise


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Crash(bot))
