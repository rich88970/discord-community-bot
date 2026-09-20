"""踩地雷 /mines

20 格（4x5）格盤，可選 3 / 7 / 12 顆地雷。
每翻開一格安全格，倍率提升；可隨時 Cash Out 領走獎金。
踩到地雷則全部歸零。

EV 設計：以組合學公式乘上 EV_TARGET，玩家任何時候 Cash Out 期望值都是 1.08。
"""

from __future__ import annotations

import math
import random
from typing import List, Set

import discord
from discord import app_commands
from discord.ext import commands

import config
from cogs._rematch import (
    RematchView,
    refund_failed_start,
    safe_view_edit,
    send_error,
    send_initial,
    try_defer,
)
from cogs.gamble_items import (
    DEFUSE,
    add_item_lines_field,
    place_bet_or_error,
    selected_items,
    settle_bet_with_items,
)
from database import db


GRID_SIZE = 20
ROWS = 4
COLS = 5


def fair_multiplier(mines: int, revealed: int) -> float:
    if revealed == 0:
        return 1.0
    safe = GRID_SIZE - mines
    num = math.comb(GRID_SIZE, revealed)
    den = math.comb(safe, revealed)
    return (num / den) * config.EV_TARGET


class MineButton(discord.ui.Button):
    def __init__(self, index: int):
        row = index // COLS
        super().__init__(
            label="\u2063",
            style=discord.ButtonStyle.secondary,
            row=row,
            custom_id=f"mine_{index}",
        )
        self.index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        view: "MinesView" = self.view  # type: ignore[assignment]
        if interaction.user.id != view.player_id:
            await interaction.response.send_message(
                "這不是你的牌局！", ephemeral=True
            )
            return
        if view.finished:
            await try_defer(interaction)
            return

        if self.index in view.mines and view.defuse_charges > 0:
            view.defuse_charges -= 1
            view.mines.remove(self.index)
            view.revealed.add(self.index)
            view.status_note = "拆彈券生效：本次踩到炸彈已免疫。"
            self.style = discord.ButtonStyle.success
            self.label = "🧯"
            self.disabled = True
            view.update_multiplier()
            if len(view.revealed) == GRID_SIZE - view.mine_count:
                await view.cash_out(interaction, perfect=True)
                return
            embed = view.build_embed()
            await safe_view_edit(interaction, embed=embed, view=view, message=view.message)
            return

        if self.index in view.mines:
            await view.lose(interaction, self.index)
            return

        view.revealed.add(self.index)
        view.status_note = ""
        self.style = discord.ButtonStyle.success
        self.label = "💎"
        self.disabled = True

        view.update_multiplier()
        if len(view.revealed) == GRID_SIZE - view.mine_count:
            await view.cash_out(interaction, perfect=True)
            return

        embed = view.build_embed()
        await safe_view_edit(interaction, embed=embed, view=view, message=view.message)


class CashOutButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="💰 Cash Out",
            style=discord.ButtonStyle.primary,
            row=4,
            custom_id="mines_cashout",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: "MinesView" = self.view  # type: ignore[assignment]
        if interaction.user.id != view.player_id:
            await interaction.response.send_message(
                "這不是你的牌局！", ephemeral=True
            )
            return
        if view.finished:
            await try_defer(interaction)
            return
        if not view.revealed:
            await interaction.response.send_message(
                "至少要先翻開一格才能提領！", ephemeral=True
            )
            return
        await view.cash_out(interaction)


class MinesView(discord.ui.View):
    def __init__(
        self,
        cog: "Mines",
        player_id: int,
        bet: int,
        mine_count: int,
        pending_bet_id: str,
        item_ids: list[str] | None = None,
    ):
        super().__init__(timeout=300)
        self.cog = cog
        self.player_id = player_id
        self.bet = bet
        self.mine_count = mine_count
        self.pending_bet_id = pending_bet_id
        self.item_ids = list(item_ids or [])
        self.defuse_charges = 1 if DEFUSE in self.item_ids else 0
        self.status_note = ""
        self.mines: Set[int] = set(random.sample(range(GRID_SIZE), mine_count))
        self.revealed: Set[int] = set()
        self.multiplier = 1.0
        self.finished = False
        self.message: discord.Message | None = None

        for i in range(GRID_SIZE):
            self.add_item(MineButton(i))
        self.add_item(CashOutButton())

    def update_multiplier(self) -> None:
        self.multiplier = fair_multiplier(self.mine_count, len(self.revealed))

    def build_embed(self) -> discord.Embed:
        potential = int(self.bet * self.multiplier)
        embed = discord.Embed(
            title="💣 踩地雷",
            description=(
                f"下注：**{self.bet:,}**　地雷數：**{self.mine_count}**\n"
                f"已翻開：**{len(self.revealed)}** / {GRID_SIZE - self.mine_count}\n"
                f"目前倍率：**x{self.multiplier:.2f}**\n"
                f"領回金額：**{potential:,}**"
            ),
            color=config.INFO_COLOR,
        )
        embed.set_footer(text="點擊格子翻開；隨時可 Cash Out 領走")
        return embed

    def _make_rematch(self) -> RematchView:
        bet = self.bet
        mines = self.mine_count
        return RematchView(
            self.player_id,
            bet,
            lambda inter, amt: self.cog._play_round(
                inter, amt, mines, is_followup=True
            ),
        )

    async def on_timeout(self) -> None:
        if self.finished:
            return
        self.finished = True
        for child in self.children:
            child.disabled = True

        item_lines: list[str] = []
        if not self.revealed:
            await db.cancel_pending_bet(self.player_id, self.pending_bet_id, self.bet)
            new_balance = await db.get_balance(self.player_id)
            embed = discord.Embed(
                title="⏳ 踩地雷已逾時",
                description=(
                    f"你尚未翻開格子，下注 **{self.bet:,}** 已自動退回。\n"
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
            grid_text = self._render_grid_text(reveal_mines=True)
            embed = discord.Embed(
                title="⏳ 踩地雷逾時，自動提領",
                description=(
                    f"{grid_text}\n\n"
                    f"倍率：**x{self.multiplier:.2f}**\n"
                    f"領回：**{payout:,}**（淨利 **{profit:+,}**）\n"
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

    async def lose(self, interaction: discord.Interaction, exploded: int) -> None:
        self.finished = True
        new_balance, payout, profit, item_lines = await settle_bet_with_items(
            self.player_id,
            self.pending_bet_id,
            self.bet,
            0,
            -self.bet,
            self.item_ids,
        )

        # 揭示地雷位置（純文字版，不再保留按鈕）
        grid_text = self._render_grid_text(reveal_mines=True, hit=exploded)

        embed = discord.Embed(
            title="💥 BOOM！踩到地雷",
            description=(
                f"{grid_text}\n\n"
                f"你輸了 **{self.bet:,}** 代幣\n"
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

    async def cash_out(
        self, interaction: discord.Interaction, perfect: bool = False
    ) -> None:
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

        grid_text = self._render_grid_text(reveal_mines=True)
        title = "🎉 完美通關！" if perfect else "💰 Cash Out"
        embed = discord.Embed(
            title=title,
            description=(
                f"{grid_text}\n\n"
                f"倍率：**x{self.multiplier:.2f}**\n"
                f"領回：**{payout:,}**（淨利 **{profit:+,}**）\n"
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

    def _render_grid_text(self, reveal_mines: bool, hit: int = -1) -> str:
        """以純文字 emoji 顯示最終格盤（取代失效按鈕）。"""
        cells = []
        for i in range(GRID_SIZE):
            if i == hit:
                cells.append("💥")
            elif i in self.mines and reveal_mines:
                cells.append("💣")
            elif i in self.revealed:
                cells.append("💎")
            else:
                cells.append("⬛")
        rows = []
        for r in range(ROWS):
            rows.append(" ".join(cells[r * COLS:(r + 1) * COLS]))
        return "\n".join(rows)


class Mines(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="mines", description="踩地雷小遊戲")
    @app_commands.describe(
        amount="下注金額",
        mines="地雷數量（3 / 7 / 12）",
        defuse="使用拆彈券（下注金額需小於等於 2.5 億）",
    )
    @app_commands.choices(
        mines=[
            app_commands.Choice(name="3 顆地雷（簡單）", value=3),
            app_commands.Choice(name="7 顆地雷（普通）", value=7),
            app_commands.Choice(name="12 顆地雷（困難）", value=12),
        ]
    )
    async def mines(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 10, 1_000_000_000],
        mines: app_commands.Choice[int],
        insurance: bool = False,
        defuse: bool = False,
        bonus: bool = False,
    ) -> None:
        await self._play_round(
            interaction,
            amount,
            mines.value,
            item_ids=selected_items(
                insurance=insurance,
                defuse=defuse,
                bonus=bonus,
                allow_defuse=True,
            ),
            is_followup=False,
        )

    async def _play_round(
        self,
        interaction: discord.Interaction,
        amount: int,
        mine_count: int,
        *,
        item_ids: list[str] | None = None,
        is_followup: bool,
    ) -> None:
        if not await try_defer(interaction):
            return
        bet_result = await place_bet_or_error(
            interaction, amount, "mines", item_ids, is_followup=is_followup
        )
        if bet_result is None:
            return

        pending_bet_id, used_items = bet_result

        try:
            view = MinesView(
                self, interaction.user.id, amount, mine_count, pending_bet_id, used_items
            )
            embed = view.build_embed()
            message = await send_initial(
                interaction, embed, view=view, is_followup=is_followup
            )
            view.message = message
        except discord.DiscordException:
            await refund_failed_start(interaction, amount, pending_bet_id=pending_bet_id)
            return
        except Exception:
            await refund_failed_start(interaction, amount, pending_bet_id=pending_bet_id)
            raise


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Mines(bot))
