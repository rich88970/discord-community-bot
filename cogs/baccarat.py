"""百家樂 /baccarat：標準補牌、獨立抽牌。閒／莊開和退本金。
含本金賠率為 1.93／1.88／10；計入和局後基礎回報約 95%–96%。
"""

from __future__ import annotations

import asyncio
import random
from typing import List, Tuple

import discord
from discord import app_commands
from discord.ext import commands

import config
import game_images
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


SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

DEAL_DELAY = 1.5
WAIT_BEFORE_RESULT = 1.0
PLAYER_PAYOUT = 1.93
BANKER_PAYOUT = 1.88
TIE_PAYOUT = 10.0


def card_value(rank: str) -> int:
    if rank == "A":
        return 1
    if rank in ("10", "J", "Q", "K"):
        return 0
    return int(rank)


def hand_total(hand: List[Tuple[str, str]]) -> int:
    return sum(card_value(r) for r, _ in hand) % 10


def deal_card() -> Tuple[str, str]:
    return random.choice(RANKS), random.choice(SUITS)


def play_baccarat() -> Tuple[List, List, int, int, str]:
    player = [deal_card(), deal_card()]
    banker = [deal_card(), deal_card()]

    p_total = hand_total(player)
    b_total = hand_total(banker)

    if p_total >= 8 or b_total >= 8:
        winner = (
            "player" if p_total > b_total else "banker" if b_total > p_total else "tie"
        )
        return player, banker, p_total, b_total, winner

    player_third = None
    if p_total <= 5:
        player_third = deal_card()
        player.append(player_third)

    if player_third is None:
        if b_total <= 5:
            banker.append(deal_card())
    else:
        third_val = card_value(player_third[0])
        if b_total <= 2:
            banker.append(deal_card())
        elif b_total == 3 and third_val != 8:
            banker.append(deal_card())
        elif b_total == 4 and third_val in (2, 3, 4, 5, 6, 7):
            banker.append(deal_card())
        elif b_total == 5 and third_val in (4, 5, 6, 7):
            banker.append(deal_card())
        elif b_total == 6 and third_val in (6, 7):
            banker.append(deal_card())

    p_total = hand_total(player)
    b_total = hand_total(banker)

    if p_total > b_total:
        winner = "player"
    elif b_total > p_total:
        winner = "banker"
    else:
        winner = "tie"

    return player, banker, p_total, b_total, winner


def render_hand(
    cards: List[Tuple[str, str]],
    total_face_down: int = 0,
) -> str:
    visuals_list = [visuals.render_card(r, s) for r, s in cards]
    for _ in range(total_face_down):
        visuals_list.append(visuals.card_back())
    if not visuals_list:
        return "（無）"
    return "```\n" + visuals.join_cards(visuals_list) + "\n```"


def _partial_embed(
    title_status: str,
    bet_label: str,
    amount: int,
    p_cards: List,
    b_cards: List,
    p_pending: int,
    b_pending: int,
    p_show_total: bool,
    b_show_total: bool,
    color: int,
    result: str | None = None,
    new_balance: int | None = None,
) -> tuple[discord.Embed, discord.File]:
    embed = discord.Embed(title=f"🎴 百家樂 — {title_status}", color=color)
    embed.add_field(
        name="你的下注",
        value=f"**{bet_label}**　金額：**{amount:,}**",
        inline=False,
    )

    p_label = "閒 Player"
    if p_show_total and p_cards:
        p_label += f"（{hand_total(p_cards)} 點）"
    embed.add_field(
        name=p_label, value=render_hand(p_cards, p_pending), inline=False
    )

    b_label = "莊 Banker"
    if b_show_total and b_cards:
        b_label += f"（{hand_total(b_cards)} 點）"
    embed.add_field(
        name=b_label, value=render_hand(b_cards, b_pending), inline=False
    )

    if result:
        embed.add_field(name="結果", value=result, inline=False)
    if new_balance is not None:
        embed.set_footer(text=f"目前餘額：{new_balance:,}")
    file = game_images.image_file(
        game_images.render_baccarat_table(
            p_cards,
            b_cards,
            p_pending,
            b_pending,
            hand_total(p_cards) if p_show_total and p_cards else None,
            hand_total(b_cards) if b_show_total and b_cards else None,
        ),
        "baccarat",
    )
    embed.set_image(url=f"attachment://{file.filename}")
    return embed, file


class Baccarat(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="baccarat", description="百家樂：押閒/莊/和")
    @app_commands.describe(
        side="押注方：player(閒)、banker(莊)、tie(和)",
        amount=f"下注 {config.MIN_GAMBLE_BET}–{config.MAX_GAMBLE_BET}；每局含道具最多領回 {config.MAX_GAMBLE_PAYOUT:,}",
    )
    @app_commands.choices(
        side=[
            app_commands.Choice(name=f"閒 Player (含本金 {PLAYER_PAYOUT:.2f}x，和局退)", value="player"),
            app_commands.Choice(name=f"莊 Banker (含本金 {BANKER_PAYOUT:.2f}x，和局退)", value="banker"),
            app_commands.Choice(name=f"和 Tie (含本金 {TIE_PAYOUT:.2f}x)", value="tie"),
        ]
    )
    async def baccarat(
        self,
        interaction: discord.Interaction,
        side: app_commands.Choice[str],
        amount: app_commands.Range[int, config.MIN_GAMBLE_BET, config.MAX_GAMBLE_BET],
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
            interaction, amount, "baccarat", item_ids, is_followup=is_followup
        )
        if bet_result is None:
            return

        pending_bet_id, used_items = bet_result

        try:
            player_full, banker_full, p_total, b_total, winner = play_baccarat()

            # 第一幀：發牌中，全部背面
            embed, file = _partial_embed(
                "發牌中…",
                side_label,
                amount,
                [],
                [],
                2,
                2,
                False,
                False,
                config.INFO_COLOR,
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
                # 若原訊息無法編輯，改用 followup 續播，避免整局卡住。
                try:
                    message = await send_initial(
                        interaction,
                        new_embed,
                        view=new_view,
                        file=new_file,
                        is_followup=True,
                    )
                    return
                except Exception:
                    return
            except Exception:
                return

        deal_steps = [
            ("player", 1),
            ("banker", 1),
            ("player", 2),
            ("banker", 2),
        ]
        p_shown: List = []
        b_shown: List = []
        for who, count in deal_steps:
            await asyncio.sleep(DEAL_DELAY)
            if who == "player":
                p_shown = player_full[:count]
                status = f"閒家發第 {count} 張"
            else:
                b_shown = banker_full[:count]
                status = f"莊家發第 {count} 張"
            embed, file = _partial_embed(
                status,
                side_label,
                amount,
                p_shown,
                b_shown,
                p_pending=max(0, 2 - len(p_shown)),
                b_pending=max(0, 2 - len(b_shown)),
                p_show_total=bool(p_shown),
                b_show_total=bool(b_shown),
                color=config.INFO_COLOR,
            )
            await safe_edit(embed, file)

        if len(player_full) == 3:
            await asyncio.sleep(DEAL_DELAY)
            p_shown = player_full[:3]
            embed, file = _partial_embed(
                "閒家補牌（第 3 張）",
                side_label,
                amount,
                p_shown,
                b_shown,
                0,
                0,
                True,
                True,
                config.INFO_COLOR,
            )
            await safe_edit(embed, file)

        if len(banker_full) == 3:
            await asyncio.sleep(DEAL_DELAY)
            b_shown = banker_full[:3]
            embed, file = _partial_embed(
                "莊家補牌（第 3 張）",
                side_label,
                amount,
                p_shown,
                b_shown,
                0,
                0,
                True,
                True,
                config.INFO_COLOR,
            )
            await safe_edit(embed, file)

        await asyncio.sleep(WAIT_BEFORE_RESULT)

        if side_value == winner:
            if winner == "player":
                payout = calculate_payout(amount, PLAYER_PAYOUT)
            elif winner == "banker":
                payout = calculate_payout(amount, BANKER_PAYOUT)
            else:
                payout = calculate_payout(amount, TIE_PAYOUT)
            profit = payout - amount
            color = config.WIN_COLOR
            result_text = (
                f"🎉 你押 **{side_value}** 中了！獲得 **{payout:,}**（+{profit:,}）"
            )
            status = f"勝！(x{payout/amount:.2f})"
        elif winner == "tie" and side_value in {"player", "banker"}:
            payout = amount
            profit = 0
            color = config.INFO_COLOR
            result_text = (
                f"🤝 本局和局，你押 **{side_value}**，退還本金 **{amount:,}**"
            )
            status = "和局退回"
        else:
            payout = 0
            profit = -amount
            color = config.LOSE_COLOR
            winner_zh = {"player": "閒", "banker": "莊", "tie": "和"}[winner]
            result_text = (
                f"😢 你押 **{side_value}**，本局贏家為 **{winner_zh}**，"
                f"輸 **{amount:,}**"
            )
            status = "敗"

        new_balance, payout, profit, item_lines = await settle_bet_with_items(
            interaction.user.id,
            pending_bet_id,
            amount,
            payout,
            profit,
            used_items,
        )
        round_settled = True

        rematch = RematchView(
            interaction.user.id,
            amount,
            lambda inter, amt: self._play_round(
                inter, side_value, side_label, amt, is_followup=True
            ),
        )
        embed, file = _partial_embed(
            status,
            side_label,
            amount,
            player_full,
            banker_full,
            0,
            0,
            True,
            True,
            color,
            result=result_text,
            new_balance=new_balance,
        )
        add_item_lines_field(embed, item_lines)
        await safe_edit(embed, file, rematch)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Baccarat(bot))
