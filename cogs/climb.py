"""爬塔遊戲 /climb

每層 4 格，通常是 1 格炸彈、3 格獎勵。
每層有 6% 機率變成寶藏層，沒有炸彈，並多 1 格比鑽石更好的獎勵。
玩家每層選一格，踩到獎勵就累積獎金並往上一層，踩到炸彈則本局歸零。
可在通過至少一層後提現。
"""

from __future__ import annotations

import random
from typing import Any, Dict, List

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


LUCKY_FLOOR_CHANCE = 0.06
NORMAL_SAFE_CHANCE = 0.75
SAFE_CHANCE = LUCKY_FLOOR_CHANCE + (1 - LUCKY_FLOOR_CHANCE) * NORMAL_SAFE_CHANCE
COLS = 4

MODES: Dict[str, Dict[str, Any]] = {
    "easy": {
        "name": "簡單",
        "floors": 8,
        "color": 0x2ECC71,
        "reward_factors": (0.75, 1.0, 1.25),
    },
    "normal": {
        "name": "普通",
        "floors": 10,
        "color": 0x3498DB,
        "reward_factors": (0.65, 1.0, 1.35),
    },
    "hard": {
        "name": "困難",
        "floors": 12,
        "color": 0xE67E22,
        "reward_factors": (0.55, 1.0, 1.45),
    },
}

REWARD_EMOJIS = ("🪙", "💰", "💎")
SUPER_REWARD_EMOJI = "🏆"
BOMB_EMOJI = "💣"
HIDDEN_EMOJI = "⬛"
ICON_SCALE = 1
SUPER_REWARD_FACTOR = 1.75


def _fair_target_after_floor(bet: int, floor: int) -> float:
    if floor <= 0:
        return float(bet)
    return float(bet) * config.EV_TARGET / (SAFE_CHANCE ** floor)


def _floor_reward_amounts(bet: int, floor: int, mode: Dict[str, Any]) -> List[int]:
    previous = _fair_target_after_floor(bet, floor - 1)
    target = _fair_target_after_floor(bet, floor)
    average_gain = max(1.0, target - previous)
    return [
        max(1, int(round(average_gain * float(factor))))
        for factor in mode["reward_factors"]
    ]


def _super_reward_amount(rewards: List[int]) -> int:
    best = max(rewards) if rewards else 1
    return max(best + 1, int(round(best * SUPER_REWARD_FACTOR)))


class ClimbPickButton(discord.ui.Button):
    def __init__(self, index: int):
        super().__init__(
            label=str(index + 1),
            style=discord.ButtonStyle.primary,
            row=0,
            custom_id=f"climb_pick_{index}",
        )
        self.index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        view: "ClimbView" = self.view  # type: ignore[assignment]
        await view.pick(interaction, self.index)


class ClimbCashOutButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="💰 提現",
            style=discord.ButtonStyle.success,
            row=1,
            custom_id="climb_cashout",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: "ClimbView" = self.view  # type: ignore[assignment]
        await view.cash_out(interaction)


class ClimbView(discord.ui.View):
    def __init__(
        self,
        cog: "Climb",
        player_id: int,
        bet: int,
        mode_id: str,
        pending_bet_id: str,
        item_ids: list[str] | None = None,
    ):
        super().__init__(timeout=300)
        self.cog = cog
        self.player_id = player_id
        self.bet = int(bet)
        self.pending_bet_id = pending_bet_id
        self.item_ids = list(item_ids or [])
        self.defuse_charges = 1 if DEFUSE in self.item_ids else 0
        self.mode_id = mode_id
        self.mode = MODES[mode_id]
        self.max_floors = int(self.mode["floors"])
        self.current_floor = 0
        self.payout = int(bet)
        self.finished = False
        self.processing = False
        self.message: discord.Message | None = None
        self.floors = [self._generate_floor(floor) for floor in range(1, self.max_floors + 1)]
        self.picks: List[int | None] = [None] * self.max_floors

        for index in range(COLS):
            self.add_item(ClimbPickButton(index))
        self.add_item(ClimbCashOutButton())
        self._sync_buttons()

    def _generate_floor(self, floor: int) -> List[Dict[str, Any]]:
        rewards = _floor_reward_amounts(self.bet, floor, self.mode)
        is_lucky_floor = random.random() < LUCKY_FLOOR_CHANCE
        cells = [] if is_lucky_floor else [{"kind": "bomb", "emoji": BOMB_EMOJI, "amount": 0}]
        cells.extend(
            {
                "kind": "reward",
                "tier": tier,
                "emoji": REWARD_EMOJIS[tier],
                "amount": amount,
                "lucky": False,
            }
            for tier, amount in enumerate(rewards)
        )
        if is_lucky_floor:
            cells.append(
                {
                    "kind": "reward",
                    "tier": "super",
                    "emoji": SUPER_REWARD_EMOJI,
                    "amount": _super_reward_amount(rewards),
                    "lucky": True,
                }
            )
        random.shuffle(cells)
        return cells

    def _sync_buttons(self) -> None:
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            if child.custom_id == "climb_cashout":
                child.disabled = self.finished or self.current_floor <= 0
            else:
                child.disabled = self.finished

    def _make_rematch(self) -> RematchView:
        return RematchView(
            self.player_id,
            self.bet,
            lambda inter, amt: self.cog._play_round(
                inter,
                amt,
                self.mode_id,
                is_followup=True,
            ),
        )

    async def on_timeout(self) -> None:
        if self.finished:
            return
        self.finished = True
        self.processing = False
        for child in self.children:
            child.disabled = True

        item_lines: list[str] = []
        if self.current_floor <= 0:
            await db.cancel_pending_bet(self.player_id, self.pending_bet_id, self.bet)
            new_balance = await db.get_balance(self.player_id)
            embed = self.build_embed(
                status=(
                    f"本局逾時且尚未通過樓層，下注 **{self.bet:,}** 已自動退回。"
                ),
                color=config.INFO_COLOR,
            )
            embed.add_field(
                name="結算",
                value=f"退款：**{self.bet:,}**\n目前餘額：**{new_balance:,}**",
                inline=False,
            )
        else:
            profit = self.payout - self.bet
            new_balance, payout, profit, item_lines = await settle_bet_with_items(
                self.player_id,
                self.pending_bet_id,
                self.bet,
                self.payout,
                profit,
                self.item_ids,
            )
            embed = self.build_embed(
                status="本局逾時，已依目前進度自動提領。",
                reveal_all=True,
                color=config.WIN_COLOR if profit > 0 else config.INFO_COLOR,
            )
            embed.add_field(
                name="結算",
                value=(
                    f"領回：**{payout:,}**\n"
                    f"淨利：**{profit:+,}**\n"
                    f"目前餘額：**{new_balance:,}**"
                ),
                inline=False,
            )
        add_item_lines_field(embed, item_lines)
        if self.message is not None:
            try:
                await self.message.edit(embed=embed, view=self._make_rematch())
            except discord.HTTPException:
                pass
        self.stop()

    async def _defer_if_needed(self, interaction: discord.Interaction) -> bool:
        await try_defer(interaction)
        return True

    async def _edit_message(
        self,
        interaction: discord.Interaction,
        *,
        embed: discord.Embed,
        view: discord.ui.View,
    ) -> None:
        await safe_view_edit(interaction, embed=embed, view=view, message=self.message)

    def _big_icon(self, emoji: str) -> str:
        return emoji * ICON_SCALE

    def _render_layout(self, *, reveal_all: bool = False, hit_floor: int | None = None) -> str:
        rows: List[str] = []
        for floor_index, cells in enumerate(self.floors):
            picked = self.picks[floor_index]
            revealed = reveal_all or picked is not None or floor_index == hit_floor
            row_cells = []
            for col, cell in enumerate(cells):
                if revealed:
                    if picked == col and cell["kind"] == "bomb":
                        row_cells.append(self._big_icon("💥"))
                    else:
                        row_cells.append(self._big_icon(str(cell["emoji"])))
                elif floor_index == self.current_floor:
                    row_cells.append(self._big_icon("❔"))
                else:
                    row_cells.append(self._big_icon(HIDDEN_EMOJI))
            rows.append(f"`{floor_index + 1:02d}F` " + " ".join(row_cells))
        return "\n".join(rows)

    def _next_floor_preview(self) -> str:
        if self.finished or self.current_floor >= self.max_floors:
            return "無"
        rewards = sorted(
            int(cell["amount"])
            for cell in self.floors[self.current_floor]
            if cell["kind"] == "reward"
        )
        return " / ".join(f"+{amount:,}" for amount in rewards)

    def build_embed(
        self,
        *,
        status: str = "選擇一格繼續往上爬，或提現收下目前獎勵。",
        reveal_all: bool = False,
        hit_floor: int | None = None,
        color: int | None = None,
    ) -> discord.Embed:
        profit = self.payout - self.bet
        embed = discord.Embed(
            title=f"🗼 爬塔遊戲 - 🟢 {self.mode['name']}模式",
            description=status,
            color=int(color if color is not None else self.mode["color"]),
        )
        embed.add_field(
            name="📊 遊戲結果",
            value=(
                f"目前獎勵：**{self.payout:,}** 🪙\n"
                f"淨收益：**{profit:+,}** 🪙\n"
                f"到達層級：**{self.current_floor}/{self.max_floors}**\n"
                f"下一層獎勵：**{self._next_floor_preview()}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="🏗️ 塔層布局",
            value=self._render_layout(reveal_all=reveal_all, hit_floor=hit_floor),
            inline=False,
        )
        embed.set_footer(
            text=(
                f"下注：{self.bet:,} | 選 1-4。每層 6% 變寶藏層：無炸彈，"
                f"{SUPER_REWARD_EMOJI} 比鑽石更高獎勵。"
            )
        )
        return embed

    async def pick(self, interaction: discord.Interaction, index: int) -> None:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("這不是你的爬塔遊戲！", ephemeral=True)
            return
        if self.finished:
            await try_defer(interaction)
            return
        if self.processing:
            await try_defer(interaction)
            return
        if self.current_floor >= self.max_floors:
            await self.cash_out(interaction, perfect=True)
            return
        self.processing = True
        if not await self._defer_if_needed(interaction):
            self.processing = False
            return

        try:
            floor_index = self.current_floor
            cell = self.floors[floor_index][index]
            self.picks[floor_index] = index

            if cell["kind"] == "bomb" and self.defuse_charges > 0:
                self.defuse_charges -= 1
                self.current_floor += 1
                if self.current_floor >= self.max_floors:
                    await self.cash_out(interaction, perfect=True)
                    return
                self._sync_buttons()
                embed = self.build_embed(
                    status=f"拆彈券生效：你在 **{floor_index + 1}F** 踩到炸彈，但免疫這次效果。"
                )
                await self._edit_message(interaction, embed=embed, view=self)
                return

            if cell["kind"] == "bomb":
                await self.lose(interaction, floor_index)
                return

            self.current_floor += 1
            self.payout += int(cell["amount"])
            if self.current_floor >= self.max_floors:
                await self.cash_out(interaction, perfect=True)
                return

            self._sync_buttons()
            bonus_text = " 這層是寶藏層，沒有炸彈！" if cell.get("lucky") else ""
            embed = self.build_embed(
                status=(
                    f"你在 **{floor_index + 1}F** 找到 {cell['emoji']}，"
                    f"獎勵 **+{int(cell['amount']):,}**。{bonus_text}"
                )
            )
            await self._edit_message(interaction, embed=embed, view=self)
        finally:
            if not self.finished:
                self.processing = False

    async def lose(self, interaction: discord.Interaction, floor_index: int) -> None:
        self.finished = True
        self._sync_buttons()
        if not await self._defer_if_needed(interaction):
            self.finished = False
            self._sync_buttons()
            return
        new_balance, payout, profit, item_lines = await settle_bet_with_items(
            self.player_id,
            self.pending_bet_id,
            self.bet,
            0,
            -self.bet,
            self.item_ids,
        )
        embed = self.build_embed(
            status=f"💥 你在 **{floor_index + 1}F** 踩到炸彈，本局獎勵歸零。",
            reveal_all=True,
            hit_floor=floor_index,
            color=config.LOSE_COLOR,
        )
        embed.add_field(
            name="結算",
            value=f"損失：**-{self.bet:,}** 🪙\n目前餘額：**{new_balance:,}**",
            inline=False,
        )
        add_item_lines_field(embed, item_lines)
        await self._edit_message(interaction, embed=embed, view=self._make_rematch())
        self.stop()

    async def cash_out(
        self,
        interaction: discord.Interaction,
        *,
        perfect: bool = False,
    ) -> None:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("這不是你的爬塔遊戲！", ephemeral=True)
            return
        if self.finished:
            await try_defer(interaction)
            return
        if self.processing and not perfect:
            await try_defer(interaction)
            return
        if self.current_floor <= 0 and not perfect:
            await interaction.response.send_message("至少通過 1 層才能提現！", ephemeral=True)
            return

        self.processing = True
        self.finished = True
        self._sync_buttons()
        if not await self._defer_if_needed(interaction):
            self.processing = False
            self.finished = False
            self._sync_buttons()
            return
        profit = self.payout - self.bet
        new_balance, payout, profit, item_lines = await settle_bet_with_items(
            self.player_id,
            self.pending_bet_id,
            self.bet,
            self.payout,
            profit,
            self.item_ids,
        )
        title = "🎉 完整通關！" if perfect else "💰 成功提現！"
        embed = self.build_embed(
            status=f"{title} 你成功收下了目前獎勵。",
            reveal_all=True,
            color=config.WIN_COLOR,
        )
        embed.add_field(
            name="結算",
            value=(
                f"領回：**{payout:,}** 🪙\n"
                f"淨收益：**{profit:+,}** 🪙\n"
                f"目前餘額：**{new_balance:,}**"
            ),
            inline=False,
        )
        add_item_lines_field(embed, item_lines)
        await self._edit_message(interaction, embed=embed, view=self._make_rematch())
        self.stop()


class Climb(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="climb", description="爬塔遊戲：每層選一格，6% 出現無炸彈寶藏層")
    @app_commands.describe(
        amount="下注金額",
        mode="模式",
        defuse="使用拆彈券（下注金額需小於等於 2.5 億）",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="簡單 8 層", value="easy"),
            app_commands.Choice(name="普通 10 層", value="normal"),
            app_commands.Choice(name="困難 12 層", value="hard"),
        ]
    )
    async def climb(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 10, 1_000_000_000],
        mode: app_commands.Choice[str] | None = None,
        insurance: bool = False,
        defuse: bool = False,
        bonus: bool = False,
    ) -> None:
        await self._play_round(
            interaction,
            amount,
            (mode.value if mode else "easy"),
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
        mode_id: str,
        *,
        item_ids: list[str] | None = None,
        is_followup: bool,
    ) -> None:
        if not await try_defer(interaction):
            return
        if mode_id not in MODES:
            await send_error(interaction, "不支援的爬塔模式。", is_followup=is_followup)
            return
        bet_result = await place_bet_or_error(
            interaction, amount, "climb", item_ids, is_followup=is_followup
        )
        if bet_result is None:
            return

        pending_bet_id, used_items = bet_result
        try:
            view = ClimbView(self, interaction.user.id, amount, mode_id, pending_bet_id, used_items)
            embed = view.build_embed(status="從 **1F** 開始，選擇 1-4 其中一格。")
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
    await bot.add_cog(Climb(bot))
