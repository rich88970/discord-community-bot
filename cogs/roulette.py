"""輪盤 /roulette：0–36 均勻抽號，0 使所有平注落敗。
含本金賠率依 EV_TARGET 計算。
"""

from __future__ import annotations

import asyncio
import random
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import visuals
from cogs._rematch import RematchView, refund_failed_start, send_error, send_initial, try_defer
from cogs.gamble_items import (
    calculate_payout,
    add_item_lines_field,
    place_bet_or_error,
    selected_items,
    settle_bet_with_items,
)
from database import db


RED_NUMBERS = visuals.RED_NUMBERS

SPIN_DELAYS = [0.25, 0.25, 0.30, 0.35, 0.40, 0.50, 0.65, 0.80, 1.00]
EVEN_MONEY_PAYOUT = config.EV_TARGET * 37 / 18
NUMBER_PAYOUT = config.EV_TARGET * 37


def number_color(n: int) -> str:
    if n == 0:
        return "🟢 綠"
    if n in RED_NUMBERS:
        return "🔴 紅"
    return "⚫ 黑"


def _build_embed(
    title: str,
    bet_label: str,
    amount: int,
    current: int,
    color: int,
    result: Optional[str] = None,
    extra_info: Optional[str] = None,
    new_balance: Optional[int] = None,
) -> discord.Embed:
    embed = discord.Embed(title=f"🎡 輪盤 — {title}", color=color)
    embed.add_field(
        name="你的下注",
        value=f"**{bet_label}**　金額：**{amount:,}**",
        inline=False,
    )

    nums = [(current + offset - 3) % 37 for offset in range(7)]
    strip = visuals.render_roulette_strip(nums)
    main_label = (
        f"{visuals.number_color_emoji(current)} **{current}** {number_color(current)}"
    )
    embed.add_field(
        name="輪盤",
        value=f"{strip}\n\n## {main_label}",
        inline=False,
    )

    if extra_info:
        embed.add_field(name="提示", value=extra_info, inline=False)
    if result:
        embed.add_field(name="結果", value=result, inline=False)
    if new_balance is not None:
        embed.set_footer(text=f"目前餘額：{new_balance:,}")
    return embed


class Roulette(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="roulette", description="輪盤，押紅黑單雙或指定號碼")
    @app_commands.describe(
        bet="下注類型",
        amount=f"下注 {config.MIN_GAMBLE_BET}–{config.MAX_GAMBLE_BET}；每局含道具最多領回 {config.MAX_GAMBLE_PAYOUT:,}",
        number="當 bet=number 時請填 0–36",
    )
    @app_commands.choices(
        bet=[
            app_commands.Choice(name=f"紅 Red (含本金 {EVEN_MONEY_PAYOUT:.2f}x)", value="red"),
            app_commands.Choice(name=f"黑 Black (含本金 {EVEN_MONEY_PAYOUT:.2f}x)", value="black"),
            app_commands.Choice(name=f"單 Odd (含本金 {EVEN_MONEY_PAYOUT:.2f}x)", value="odd"),
            app_commands.Choice(name=f"雙 Even (含本金 {EVEN_MONEY_PAYOUT:.2f}x)", value="even"),
            app_commands.Choice(name=f"號 Number 0–36 (含本金 {NUMBER_PAYOUT:.2f}x)", value="number"),
        ]
    )
    async def roulette(
        self,
        interaction: discord.Interaction,
        bet: app_commands.Choice[str],
        amount: app_commands.Range[int, config.MIN_GAMBLE_BET, config.MAX_GAMBLE_BET],
        number: app_commands.Range[int, 0, 36] | None = None,
        insurance: bool = False,
        bonus: bool = False,
    ) -> None:
        if bet.value == "number" and number is None:
            await interaction.response.send_message(
                "押號碼時請填寫 number（0–36）", ephemeral=True
            )
            return

        bet_label = bet.name
        if bet.value == "number":
            bet_label = f"號碼 {number}"

        await self._play_round(
            interaction,
            bet.value,
            bet_label,
            amount,
            number,
            item_ids=selected_items(insurance=insurance, bonus=bonus),
            is_followup=False,
        )

    async def _play_round(
        self,
        interaction: discord.Interaction,
        bet_value: str,
        bet_label: str,
        amount: int,
        number: Optional[int],
        *,
        item_ids: list[str] | None = None,
        is_followup: bool,
    ) -> None:
        if not await try_defer(interaction):
            return
        bet_result = await place_bet_or_error(
            interaction, amount, "roulette", item_ids, is_followup=is_followup
        )
        if bet_result is None:
            return

        pending_bet_id, used_items = bet_result

        try:
            result_num = random.randint(0, 36)

            steps_total = len(SPIN_DELAYS)
            path: List[int] = []
            cur = random.randint(0, 36)
            for _ in range(steps_total - 1):
                path.append(cur)
                cur = (cur + random.randint(1, 4)) % 37
            path.append(result_num)

            embed = _build_embed(
                "球在轉…",
                bet_label,
                amount,
                path[0],
                config.INFO_COLOR,
                extra_info="🎯 押注已下，等待開獎…",
            )
            message = await send_initial(interaction, embed, is_followup=is_followup)
        except discord.DiscordException:
            await refund_failed_start(interaction, amount, pending_bet_id=pending_bet_id)
            return
        except Exception:
            await refund_failed_start(interaction, amount, pending_bet_id=pending_bet_id)
            raise
        round_settled = False
        
        async def safe_edit(new_embed: discord.Embed, new_view: discord.ui.View | None = None):
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

        for i in range(1, steps_total):
            await asyncio.sleep(SPIN_DELAYS[i])
            await safe_edit(
                _build_embed(
                    "球在轉…",
                    bet_label,
                    amount,
                    path[i],
                    config.INFO_COLOR,
                )
            )

        win = False
        payout = 0
        if bet_value == "number":
            if number == result_num:
                win = True
                payout = calculate_payout(amount, NUMBER_PAYOUT)
        elif bet_value == "red":
            if result_num in RED_NUMBERS:
                win = True
                payout = calculate_payout(amount, EVEN_MONEY_PAYOUT)
        elif bet_value == "black":
            if result_num != 0 and result_num not in RED_NUMBERS:
                win = True
                payout = calculate_payout(amount, EVEN_MONEY_PAYOUT)
        elif bet_value == "odd":
            if result_num != 0 and result_num % 2 == 1:
                win = True
                payout = calculate_payout(amount, EVEN_MONEY_PAYOUT)
        elif bet_value == "even":
            if result_num != 0 and result_num % 2 == 0:
                win = True
                payout = calculate_payout(amount, EVEN_MONEY_PAYOUT)

        if win:
            profit = payout - amount
            color = config.WIN_COLOR
            result_text = f"🎉 中獎！領回 **{payout:,}**（淨利 **{profit:+,}**）"
            title = "勝！"
        else:
            profit = -amount
            color = config.LOSE_COLOR
            result_text = f"😢 沒中，輸了 **{amount:,}**"
            title = "敗"

        new_balance, payout, profit, item_lines = await settle_bet_with_items(
            interaction.user.id,
            pending_bet_id,
            amount,
            payout,
            profit,
            used_items,
        )
        round_settled = True

        await asyncio.sleep(0.5)

        rematch = RematchView(
            interaction.user.id,
            amount,
            lambda inter, amt: self._play_round(
                inter, bet_value, bet_label, amt, number, is_followup=True
            ),
        )
        embed = _build_embed(
            title,
            bet_label,
            amount,
            result_num,
            color,
            result=result_text,
            new_balance=new_balance,
        )
        add_item_lines_field(embed, item_lines)
        await safe_edit(embed, rematch)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Roulette(bot))
