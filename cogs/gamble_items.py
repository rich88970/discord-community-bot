from __future__ import annotations

from typing import Iterable

import discord

import config
from cogs._rematch import send_error
from database import db


INSURANCE = "insurance"
DEFUSE = "defuse"
BONUS = "bonus"


def selected_items(
    *,
    insurance: bool = False,
    defuse: bool = False,
    bonus: bool = False,
    allow_defuse: bool = False,
) -> list[str]:
    items: list[str] = []
    if insurance:
        items.append(INSURANCE)
    if allow_defuse and defuse:
        items.append(DEFUSE)
    if bonus:
        items.append(BONUS)
    return items


def item_name(item_id: str) -> str:
    item = config.SHOP_ITEMS.get(item_id, {})
    return str(item.get("name", item_id))


def format_item_names(item_ids: Iterable[str]) -> str:
    names = [item_name(item_id) for item_id in item_ids]
    return "、".join(names) if names else "無"


def add_items_field(embed: discord.Embed, item_ids: Iterable[str]) -> None:
    items = list(item_ids)
    if items:
        embed.add_field(name="使用道具", value=format_item_names(items), inline=False)


def add_item_lines_field(embed: discord.Embed, item_lines: Iterable[str]) -> None:
    lines = [line for line in item_lines if line]
    if lines:
        embed.add_field(name="道具效果", value="\n".join(lines), inline=False)


async def place_bet_or_error(
    interaction: discord.Interaction,
    amount: int,
    game: str,
    item_ids: list[str] | None,
    *,
    is_followup: bool,
) -> tuple[str, list[str]] | None:
    if item_ids and DEFUSE in item_ids and int(amount) > config.DEFUSE_MAX_BET:
        await send_error(
            interaction,
            f"拆彈券只能在下注金額小於等於 **{config.DEFUSE_MAX_BET:,}** 時使用。",
            is_followup=is_followup,
        )
        return None

    result = await db.place_bet_with_items(
        interaction.user.id, int(amount), game, list(item_ids or [])
    )
    if result.get("ok"):
        return str(result["pending_bet_id"]), list(result.get("items", []))

    if result.get("reason") == "item":
        missing_item = item_name(str(result.get("item", "")))
        await send_error(
            interaction,
            f"你沒有可使用的「{missing_item}」，請先到 /shop buy 購買。",
            is_followup=is_followup,
        )
        return None

    await send_error(interaction, "你的餘額不足。", is_followup=is_followup)
    return None


def apply_settlement_items(
    wager: int,
    payout: int,
    profit: int,
    item_ids: Iterable[str],
) -> tuple[int, int, list[str]]:
    item_set = set(item_ids)
    lines: list[str] = []
    adjusted_payout = int(payout)
    adjusted_profit = int(profit)

    if adjusted_profit > 0 and BONUS in item_set:
        bonus_amount = int(round(adjusted_profit * config.GAMBLE_BONUS_RATE))
        if bonus_amount > 0:
            adjusted_payout += bonus_amount
            adjusted_profit += bonus_amount
            lines.append(f"加倍券生效：淨利 +25%，額外 +{bonus_amount:,}。")

    if adjusted_profit < 0 and INSURANCE in item_set:
        refund = int(round(int(wager) * config.GAMBLE_INSURANCE_REFUND_RATE))
        adjusted_payout += refund
        adjusted_profit += refund
        lines.append(f"保險券生效：返還全額下注本金 {refund:,}。")

    if lines:
        lines.append(f"道具後結算：領回 {adjusted_payout:,}，淨利 {adjusted_profit:+,}。")

    return adjusted_payout, adjusted_profit, lines


async def settle_bet_with_items(
    user_id: int,
    pending_bet_id: str | None,
    wager: int,
    payout: int,
    profit: int,
    item_ids: Iterable[str],
) -> tuple[int, int, int, list[str]]:
    adjusted_payout, adjusted_profit, lines = apply_settlement_items(
        wager, payout, profit, item_ids
    )
    new_balance = await db.settle_bet(
        user_id,
        pending_bet_id,
        wager,
        adjusted_profit,
        payout=adjusted_payout,
    )
    return new_balance, adjusted_payout, adjusted_profit, lines
