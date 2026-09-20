from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

import config
from database import db


LOCAL_TZ = ZoneInfo(config.LOCAL_TIMEZONE)
SHOP_ITEM_CHOICES = [
    app_commands.Choice(
        name=f"{item['name']} - {int(item['price']):,} 元 - {item['description']}",
        value=item_id,
    )
    for item_id, item in config.SHOP_ITEMS.items()
]


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def daily_date_key(dt: datetime) -> str:
    return dt.date().isoformat()


def next_daily_reset(dt: datetime) -> datetime:
    tomorrow = dt.date() + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=LOCAL_TZ)


def format_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} 小時 {minutes} 分 {secs} 秒"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


class Shop(commands.Cog):
    shop = app_commands.Group(name="shop", description="賭博道具商店")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @shop.command(name="list", description="查看賭博道具商店")
    async def shop_list(self, interaction: discord.Interaction) -> None:
        state = await db.get_shop_state(interaction.user.id)
        today_key = daily_date_key(local_now())
        lines = []
        for item_id, item in config.SHOP_ITEMS.items():
            bought = state["shop_purchases"].get(item_id) == today_key
            owned = int(state["items"].get(item_id, 0))
            status = "今日已買" if bought else "今日可買"
            lines.append(
                f"**{item['name']}**：{int(item['price']):,} 元｜持有 {owned}｜{status}\n"
                f"{item['description']}"
            )

        embed = discord.Embed(
            title="道具商店",
            description="\n\n".join(lines),
            color=config.EMBED_COLOR,
        )
        embed.set_footer(
            text=f"每日購買限制於台灣時間 00:00 重置。餘額：{int(state['balance']):,}"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @shop.command(name="buy", description="購買賭博道具，每種每天限買一次")
    @app_commands.describe(item="要購買的道具，選項會顯示價格與效果")
    @app_commands.choices(item=SHOP_ITEM_CHOICES)
    async def shop_buy(
        self,
        interaction: discord.Interaction,
        item: app_commands.Choice[str],
    ) -> None:
        result = await db.buy_shop_item(interaction.user.id, item.value)
        item_data = config.SHOP_ITEMS[item.value]
        if not result["ok"]:
            reason = result.get("reason")
            if reason == "daily_limit":
                now = local_now()
                remain = int(next_daily_reset(now).timestamp() - now.timestamp())
                msg = f"你今天已經買過「{item_data['name']}」了，請等 **{format_seconds(remain)}** 後再購買。"
            elif reason == "balance":
                msg = (
                    f"餘額不足，購買「{item_data['name']}」需要 **{int(result['price']):,}**。\n"
                    f"目前餘額：**{int(result['balance']):,}**"
                )
            else:
                msg = "找不到這個道具。"
            await interaction.response.send_message(msg, ephemeral=True)
            return

        embed = discord.Embed(
            title="購買成功",
            description=(
                f"獲得 **{item_data['name']}** x1\n"
                f"效果：{item_data['description']}\n"
                f"花費：**{int(item_data['price']):,}**\n"
                f"目前持有：**{int(result['count'])}**\n"
                f"目前餘額：**{int(result['balance']):,}**"
            ),
            color=config.WIN_COLOR,
        )
        embed.set_footer(text="每種道具每日限買一次，台灣時間 00:00 重置。")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @shop.command(name="inventory", description="查看持有的賭博道具")
    async def shop_inventory(self, interaction: discord.Interaction) -> None:
        state = await db.get_shop_state(interaction.user.id)
        lines = [
            f"**{item['name']}**：{int(state['items'].get(item_id, 0))}"
            for item_id, item in config.SHOP_ITEMS.items()
        ]
        embed = discord.Embed(
            title="道具背包",
            description="\n".join(lines),
            color=config.EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Shop(bot))
