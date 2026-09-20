"""打氣球 /balloon

每按一次 Pump，倍率提升、爆破機率變高。
任何時候可以 Cash Out 領回，爆掉則歸零。

倍率設計：mult_n = EV_TARGET / 累積存活機率，
所以無論玩家在哪一次 pump 後 Cash Out，EV 都恰為 EV_TARGET ≈ 1.08。
"""

from __future__ import annotations

import random

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
    add_item_lines_field,
    place_bet_or_error,
    selected_items,
    settle_bet_with_items,
)
from database import db


BASE_POP_CHANCE = 0.10
POP_INC_PER_PUMP = 0.05
MAX_POP_CHANCE = 0.85


def pop_chance(pump_number: int) -> float:
    return min(MAX_POP_CHANCE, BASE_POP_CHANCE + POP_INC_PER_PUMP * (pump_number - 1))


def balloon_color(pumps: int) -> str:
    if pumps < 2:
        return "🔵"
    if pumps < 4:
        return "🟢"
    if pumps < 6:
        return "🟡"
    if pumps < 8:
        return "🟠"
    return "🔴"


class PumpButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="💨 Pump", style=discord.ButtonStyle.primary, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: "BalloonView" = self.view  # type: ignore[assignment]
        if interaction.user.id != view.player_id:
            await interaction.response.send_message(
                "這不是你的氣球！", ephemeral=True
            )
            return
        if view.finished:
            await try_defer(interaction)
            return

        view.pumps += 1
        chance = pop_chance(view.pumps)
        if random.random() < chance:
            await view.burst(interaction)
            return

        view.survival *= (1 - chance)
        view.multiplier = config.EV_TARGET / view.survival
        embed = view.build_embed()
        await safe_view_edit(interaction, embed=embed, view=view, message=view.message)


class BalloonCashOut(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="💰 Cash Out", style=discord.ButtonStyle.success, row=0
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: "BalloonView" = self.view  # type: ignore[assignment]
        if interaction.user.id != view.player_id:
            await interaction.response.send_message(
                "這不是你的氣球！", ephemeral=True
            )
            return
        if view.finished:
            await try_defer(interaction)
            return
        if view.pumps == 0:
            await interaction.response.send_message(
                "至少要打一次氣才能領！", ephemeral=True
            )
            return
        await view.cash_out(interaction)


class BalloonView(discord.ui.View):
    def __init__(
        self,
        cog: "Balloon",
        player_id: int,
        bet: int,
        pending_bet_id: str,
        item_ids: list[str] | None = None,
    ):
        super().__init__(timeout=300)
        self.cog = cog
        self.player_id = player_id
        self.bet = bet
        self.pending_bet_id = pending_bet_id
        self.item_ids = list(item_ids or [])
        self.pumps = 0
        self.multiplier = 1.0
        self.survival = 1.0
        self.finished = False
        self.message: discord.Message | None = None
        self.add_item(PumpButton())
        self.add_item(BalloonCashOut())

    def build_embed(self) -> discord.Embed:
        next_chance = pop_chance(self.pumps + 1) * 100
        potential = int(self.bet * self.multiplier)
        danger = self.pumps >= 6
        balloon_art = visuals.render_balloon(self.pumps, danger=danger)
        color_dot = balloon_color(self.pumps)
        embed = discord.Embed(
            title=f"🎈 打氣球 {color_dot}",
            description=(
                f"```\n{balloon_art}\n```\n"
                f"下注：**{self.bet:,}**　已打氣：**{self.pumps}** 次\n"
                f"目前倍率：**x{self.multiplier:.2f}**　領回：**{potential:,}**\n"
                f"下一次爆破機率：**{next_chance:.1f}%**"
            ),
            color=config.INFO_COLOR,
        )
        return embed

    def _make_rematch(self) -> RematchView:
        return RematchView(
            self.player_id,
            self.bet,
            lambda inter, amt: self.cog._play_round(inter, amt, is_followup=True),
        )

    async def on_timeout(self) -> None:
        if self.finished:
            return
        self.finished = True
        for child in self.children:
            child.disabled = True

        item_lines: list[str] = []
        if self.pumps <= 0:
            await db.cancel_pending_bet(self.player_id, self.pending_bet_id, self.bet)
            new_balance = await db.get_balance(self.player_id)
            embed = discord.Embed(
                title="⏳ 氣球局已逾時",
                description=(
                    f"你尚未打氣，下注 **{self.bet:,}** 已自動退回。\n"
                    f"目前餘額：**{new_balance:,}**"
                ),
                color=config.INFO_COLOR,
            )
        else:
            payout = int(self.bet * self.multiplier)
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
                title="⏳ 氣球局逾時，自動提領",
                description=(
                    f"打氣 **{self.pumps}** 次，倍率 **x{self.multiplier:.2f}**\n"
                    f"領回 **{payout:,}**（淨利 **{profit:+,}**）\n"
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

    async def burst(self, interaction: discord.Interaction) -> None:
        self.finished = True
        new_balance, payout, profit, item_lines = await settle_bet_with_items(
            self.player_id,
            self.pending_bet_id,
            self.bet,
            0,
            -self.bet,
            self.item_ids,
        )
        pop_art = visuals.render_balloon_pop()
        embed = discord.Embed(
            title="💥 氣球爆了！",
            description=(
                f"```\n{pop_art}\n```\n"
                f"在第 **{self.pumps}** 次打氣時爆掉，輸了 **{self.bet:,}**\n"
                f"目前餘額：**{new_balance:,}**"
            ),
            color=config.LOSE_COLOR,
        )
        add_item_lines_field(embed, item_lines)
        await safe_view_edit(
            interaction,
            embed=embed,
            view=self._make_rematch(),
            message=self.message,
        )
        self.stop()

    async def cash_out(self, interaction: discord.Interaction) -> None:
        self.finished = True
        payout = int(self.bet * self.multiplier)
        profit = payout - self.bet
        new_balance, payout, profit, item_lines = await settle_bet_with_items(
            self.player_id,
            self.pending_bet_id,
            self.bet,
            payout,
            profit,
            self.item_ids,
        )
        balloon_art = visuals.render_balloon(self.pumps)
        embed = discord.Embed(
            title="💰 成功領回",
            description=(
                f"```\n{balloon_art}\n```\n"
                f"打氣 **{self.pumps}** 次，倍率 **x{self.multiplier:.2f}**\n"
                f"領回 **{payout:,}**（淨利 **{profit:+,}**）\n"
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


class Balloon(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="balloon", description="打氣球小遊戲")
    @app_commands.describe(amount="下注金額")
    async def balloon(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 10, 1_000_000_000],
        insurance: bool = False,
        bonus: bool = False,
    ) -> None:
        await self._play_round(
            interaction,
            amount,
            item_ids=selected_items(insurance=insurance, bonus=bonus),
            is_followup=False,
        )

    async def _play_round(
        self,
        interaction: discord.Interaction,
        amount: int,
        *,
        item_ids: list[str] | None = None,
        is_followup: bool,
    ) -> None:
        if not await try_defer(interaction):
            return
        bet_result = await place_bet_or_error(
            interaction, amount, "balloon", item_ids, is_followup=is_followup
        )
        if bet_result is None:
            return
        pending_bet_id, used_items = bet_result
        try:
            view = BalloonView(self, interaction.user.id, amount, pending_bet_id, used_items)
            message = await send_initial(
                interaction, view.build_embed(), view=view, is_followup=is_followup
            )
            view.message = message
        except discord.DiscordException:
            await refund_failed_start(interaction, amount, pending_bet_id=pending_bet_id)
            return
        except Exception:
            await refund_failed_start(interaction, amount, pending_bet_id=pending_bet_id)
            raise


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Balloon(bot))
