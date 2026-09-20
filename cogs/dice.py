"""擲骰子 /dice

3 顆骰子，玩家可押：
  - big   (大 11–17，賠 2.22x)
  - small (小 4–10，賠 2.22x)
  - triple(圍骰，三顆同點，賠 38.88x)

機率：大/小 各 105/216 ≈ 0.4861，圍骰 6/216 ≈ 0.0278。
EV ≈ 1.08（玩家略佔優勢）。圍骰時莊家通殺大小。
"""

from __future__ import annotations

import asyncio
import random
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
import game_images
import visuals
from cogs._rematch import RematchView, refund_failed_start, send_error, send_initial, try_defer
from cogs.gamble_items import (
    add_item_lines_field,
    place_bet_or_error,
    selected_items,
    settle_bet_with_items,
)
from database import db


ROLL_FRAMES = 3
FRAME_DELAY = 0.2
STOP_DELAY = 0.3
BIG_SMALL_PAYOUT = 2.22
TRIPLE_PAYOUT = 38.88


def _render_dice_block(faces: List[Optional[int]]) -> str:
    arts = []
    for f in faces:
        arts.append(visuals.render_die(f) if f else visuals.rolling_die())
    return "```\n" + visuals.join_dice(arts) + "\n```"


def _build_embed(
    title: str,
    bet_label: str,
    amount: int,
    faces: List[Optional[int]],
    color: int,
    result: Optional[str] = None,
    new_balance: Optional[int] = None,
    total: Optional[int] = None,
) -> tuple[discord.Embed, discord.File]:
    embed = discord.Embed(title=f"🎲 擲骰子 — {title}", color=color)
    embed.add_field(
        name="你的下注",
        value=f"**{bet_label}**　金額：**{amount:,}**",
        inline=False,
    )
    embed.add_field(name="開出", value=_render_dice_block(faces), inline=False)
    if total is not None:
        revealed = [f for f in faces if f is not None]
        is_triple = len(revealed) == 3 and revealed[0] == revealed[1] == revealed[2]
        suffix = "（圍骰！）" if is_triple else ""
        embed.add_field(name="總點數", value=f"**{total}**{suffix}", inline=False)
    if result:
        embed.add_field(name="結果", value=result, inline=False)
    if new_balance is not None:
        embed.set_footer(text=f"目前餘額：{new_balance:,}")
    file = game_images.image_file(game_images.render_dice_table(faces), "dice")
    embed.set_image(url=f"attachment://{file.filename}")
    return embed, file


class Dice(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="dice", description="擲三顆骰子押大/小/圍骰")
    @app_commands.describe(side="押注方", amount="下注金額")
    @app_commands.choices(
        side=[
            app_commands.Choice(name="大 Big 11–17 (賠 2.22x)", value="big"),
            app_commands.Choice(name="小 Small 4–10 (賠 2.22x)", value="small"),
            app_commands.Choice(name="圍骰 Triple (賠 38.88x)", value="triple"),
        ]
    )
    async def dice(
        self,
        interaction: discord.Interaction,
        side: app_commands.Choice[str],
        amount: app_commands.Range[int, 10, 1_000_000_000],
        insurance: bool = False,
        bonus: bool = False,
    ) -> None:
        await self._play_round(
            interaction,
            side.value,
            side.name,
            amount,
            item_ids=selected_items(insurance=insurance, bonus=bonus),
            is_followup=False,
        )

    async def _play_round(
        self,
        interaction: discord.Interaction,
        side_value: str,
        side_label: str,
        amount: int,
        *,
        item_ids: list[str] | None = None,
        is_followup: bool,
    ) -> None:
        if not await try_defer(interaction):
            return
        bet_result = await place_bet_or_error(
            interaction, amount, "dice", item_ids, is_followup=is_followup
        )
        if bet_result is None:
            return

        pending_bet_id, used_items = bet_result

        try:
            rolls = [random.randint(1, 6) for _ in range(3)]

            faces: List[Optional[int]] = [None, None, None]
            embed, file = _build_embed(
                "搖骰中…", side_label, amount, faces, config.INFO_COLOR
            )
            message = await send_initial(
                interaction, embed, file=file, is_followup=is_followup
            )
        except discord.DiscordException:
            await refund_failed_start(interaction, amount, pending_bet_id=pending_bet_id)
            return
        except Exception:
            await refund_failed_start(interaction, amount, pending_bet_id=pending_bet_id)
            raise
        round_settled = False
        
        async def safe_edit(
            new_embed: discord.Embed,
            new_file: discord.File,
            new_view: discord.ui.View | None = None,
        ):
            nonlocal message
            try:
                await message.edit(embed=new_embed, view=new_view, attachments=[new_file])
            except discord.HTTPException:
                try:
                    message = await send_initial(
                        interaction,
                        new_embed,
                        view=new_view,
                        file=new_file,
                        is_followup=True,
                    )
                except Exception:
                    return
            except Exception:
                return

        for i in range(3):
            for _ in range(ROLL_FRAMES):
                await asyncio.sleep(FRAME_DELAY)
                shake_faces = list(faces)
                for j in range(i, 3):
                    shake_faces[j] = None
                embed, file = _build_embed(
                    f"搖骰中…（{i + 1}/3 即將定格）",
                    side_label,
                    amount,
                    shake_faces,
                    config.INFO_COLOR,
                )
                await safe_edit(embed, file)
            faces[i] = rolls[i]
            partial_total = sum(f for f in faces if f is not None)
            embed, file = _build_embed(
                f"第 {i + 1} 顆停下",
                side_label,
                amount,
                faces,
                config.INFO_COLOR,
                total=partial_total,
            )
            await safe_edit(embed, file)
            await asyncio.sleep(STOP_DELAY)

        total = sum(rolls)
        is_triple = rolls[0] == rolls[1] == rolls[2]

        win = False
        payout = 0
        if side_value == "triple":
            if is_triple:
                win = True
                payout = int(round(amount * TRIPLE_PAYOUT))
        elif side_value == "big":
            if not is_triple and 11 <= total <= 17:
                win = True
                payout = int(round(amount * BIG_SMALL_PAYOUT))
        elif side_value == "small":
            if not is_triple and 4 <= total <= 10:
                win = True
                payout = int(round(amount * BIG_SMALL_PAYOUT))

        if win:
            profit = payout - amount
            color = config.WIN_COLOR
            result = f"🎉 中獎！獲得 **{payout:,}**（淨利 **{profit:+,}**）"
            title = "勝！"
        else:
            profit = -amount
            color = config.LOSE_COLOR
            if is_triple and side_value != "triple":
                result = f"💀 圍骰 {rolls[0]}！莊家通殺，輸 **{amount:,}**"
            else:
                result = f"😢 沒中獎，輸 **{amount:,}**"
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

        await asyncio.sleep(0.4)

        rematch = RematchView(
            interaction.user.id,
            amount,
            lambda inter, amt: self._play_round(
                inter, side_value, side_label, amt, is_followup=True
            ),
        )
        embed, file = _build_embed(
            title,
            side_label,
            amount,
            list(rolls),
            color,
            result=result,
            new_balance=new_balance,
            total=total,
        )
        add_item_lines_field(embed, item_lines)
        await safe_edit(embed, file, rematch)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Dice(bot))
