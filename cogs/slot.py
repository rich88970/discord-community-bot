"""拉霸 /slot

3 連線拉霸機，三圈相同有大獎，前兩圈相同也有小獎。
動畫：三個輪盤先一起轉動，再一個一個停下。

權重與賠率經過調校，整體 EV ≈ 1.08（玩家略佔優勢）。
"""

from __future__ import annotations

import asyncio
import random
from typing import List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

import config
import visuals
from cogs._rematch import RematchView, refund_failed_start, send_error, send_initial, try_defer
from cogs.gamble_items import (
    add_item_lines_field,
    place_bet_or_error,
    selected_items,
    settle_bet_with_items,
)
from database import db


SYMBOLS: List[Tuple[str, int, float]] = [
    ("🍒", 8, 10),
    ("🍋", 7, 15),
    ("🍊", 6, 25),
    ("🍇", 5, 40),
    ("🔔", 4, 70),
    ("⭐", 3, 150),
    ("💎", 2, 400),
    ("7️⃣", 1, 1000),
]

PAIR_PAYOUT = 2.81

SPIN_FRAMES = 3
SPIN_DELAY = 0.45
STOP_DELAY = 0.6


def spin_reel() -> str:
    population = [s for s, _, _ in SYMBOLS]
    weights = [w for _, w, _ in SYMBOLS]
    return random.choices(population, weights=weights, k=1)[0]


def _all_symbols() -> List[str]:
    return [s for s, _, _ in SYMBOLS]


def lookup_payout(symbol: str) -> float:
    for s, _, p in SYMBOLS:
        if s == symbol:
            return p
    return 0.0


def _build_embed(
    title: str,
    bet_amount: int,
    reels: List[Optional[str]],
    color: int,
    label: Optional[str] = None,
    result_line: Optional[str] = None,
    new_balance: Optional[int] = None,
) -> discord.Embed:
    embed = discord.Embed(title=f"🎰 拉霸機 — {title}", color=color)
    spinning_mask = [r is None for r in reels]
    display_reels = [r if r else random.choice(_all_symbols()) for r in reels]
    embed.add_field(
        name="連線",
        value=visuals.render_slot(display_reels, spinning_mask),
        inline=False,
    )
    embed.add_field(name="下注", value=f"**{bet_amount:,}**", inline=True)
    if label:
        embed.add_field(name="結果", value=label, inline=False)
    if result_line:
        embed.add_field(name="輸贏", value=result_line, inline=False)
    if new_balance is not None:
        embed.set_footer(text=f"目前餘額：{new_balance:,}")
    return embed


class Slot(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="slot", description="拉霸機，三連線中大獎")
    @app_commands.describe(amount="下注金額")
    async def slot(
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
            interaction, amount, "slot", item_ids, is_followup=is_followup
        )
        if bet_result is None:
            return

        pending_bet_id, used_items = bet_result

        try:
            reels_final = [spin_reel() for _ in range(3)]

            reels: List[Optional[str]] = [None, None, None]
            embed = _build_embed("旋轉中…", amount, reels, config.INFO_COLOR)
            message = await send_initial(interaction, embed, is_followup=is_followup)
        except discord.DiscordException:
            await refund_failed_start(interaction, amount, pending_bet_id=pending_bet_id)
            return
        except Exception:
            await refund_failed_start(interaction, amount, pending_bet_id=pending_bet_id)
            raise
        round_settled = False

        async def safe_edit(
            new_embed: discord.Embed,
            new_view: discord.ui.View | None = None,
        ) -> None:
            nonlocal message
            try:
                await message.edit(embed=new_embed, view=new_view)
            except discord.HTTPException:
                try:
                    message = await send_initial(
                        interaction,
                        new_embed,
                        view=new_view,
                        is_followup=True,
                    )
                except Exception:
                    return
            except Exception:
                return

        for i in range(3):
            for _ in range(SPIN_FRAMES):
                await asyncio.sleep(SPIN_DELAY)
                await safe_edit(
                    _build_embed(
                        f"旋轉中…（{i + 1}/3）", amount, reels, config.INFO_COLOR
                    )
                )
            reels[i] = reels_final[i]
            await safe_edit(
                _build_embed(
                    f"第 {i + 1} 圈停下", amount, reels, config.INFO_COLOR
                )
            )
            await asyncio.sleep(STOP_DELAY)

        if reels_final[0] == reels_final[1] == reels_final[2]:
            mult = lookup_payout(reels_final[0])
            label = f"💎 三連線 {reels_final[0]}！倍率 x{mult:.0f}"
        elif reels_final[0] == reels_final[1]:
            mult = PAIR_PAYOUT
            label = f"✨ 兩連線！倍率 x{mult:.1f}"
        else:
            mult = 0.0
            label = "😢 沒有中獎"

        payout = int(round(amount * mult))
        profit = payout - amount

        new_balance, payout, profit, item_lines = await settle_bet_with_items(
            interaction.user.id,
            pending_bet_id,
            amount,
            payout,
            profit,
            used_items,
        )
        round_settled = True

        color = config.WIN_COLOR if profit > 0 else config.LOSE_COLOR
        title = "勝！" if profit > 0 else "敗"

        await asyncio.sleep(0.4)

        rematch = RematchView(
            interaction.user.id,
            amount,
            lambda inter, amt: self._play_round(inter, amt, is_followup=True),
        )
        embed = _build_embed(
            title,
            amount,
            reels_final,
            color,
            label=label,
            result_line=(
                f"領回：**{payout:,}**　淨利：**{profit:+,}**"
            ),
            new_balance=new_balance,
        )
        add_item_lines_field(embed, item_lines)
        await safe_edit(embed, rematch)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Slot(bot))
