"""Discord 小遊戲機器人主程式"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

import config
from database import db


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")


COG_MODULES = [
    "cogs.economy",
    "cogs.invest",
    "cogs.shop",
    "cogs.help",
    "cogs.baccarat",
    "cogs.minesweeper",
    "cogs.dice",
    "cogs.balloon",
    "cogs.climb",
    "cogs.crash",
    "cogs.slot",
    "cogs.roulette",
    "cogs.rpg",
    "cogs.admin",
]


intents = discord.Intents.default()
intents.message_content = False

bot = commands.Bot(command_prefix=config.COMMAND_PREFIX, intents=intents)
pending_safety_task: asyncio.Task | None = None


async def pending_safety_loop() -> None:
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            changed = await db.run_safety_updates()
            if changed:
                log.info("已執行 pending bet 安全退款/利息更新")
        except Exception as exc:  # noqa: BLE001
            log.exception("pending bet 安全更新失敗：%s", exc)
        await asyncio.sleep(30)


@bot.event
async def on_ready() -> None:
    global pending_safety_task
    log.info("已登入：%s (id=%s)", bot.user, bot.user.id if bot.user else "?")
    if pending_safety_task is None or pending_safety_task.done():
        pending_safety_task = asyncio.create_task(pending_safety_loop())
    try:
        synced = await bot.tree.sync()
        log.info("已同步 %d 個斜線指令", len(synced))
    except Exception as exc:  # noqa: BLE001
        log.exception("同步斜線指令失敗：%s", exc)
    await bot.change_presence(
        activity=discord.Game(name="/help 開始遊戲")
    )


async def load_cogs() -> None:
    for module in COG_MODULES:
        try:
            await bot.load_extension(module)
            log.info("已載入 %s", module)
        except Exception as exc:  # noqa: BLE001
            log.exception("載入 %s 失敗：%s", module, exc)


async def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit(
            "尚未設定 DISCORD_BOT_TOKEN。請複製 .env.example 為 .env，"
            "填入自己的 Bot Token，或設定同名環境變數。"
        )
    async with bot:
        await load_cogs()
        await bot.start(config.BOT_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot 已手動停止。")
