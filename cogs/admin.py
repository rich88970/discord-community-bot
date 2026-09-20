"""Bot 管理指令：熱重載 cog、重新同步斜線指令。

只有 config.OWNER_IDS 內的使用者可以執行。
"""

from __future__ import annotations

import importlib
import sys

import discord
from discord import app_commands
from discord.ext import commands

import config
from database import db


COG_CHOICES = [
    app_commands.Choice(name="economy", value="cogs.economy"),
    app_commands.Choice(name="invest", value="cogs.invest"),
    app_commands.Choice(name="shop", value="cogs.shop"),
    app_commands.Choice(name="help", value="cogs.help"),
    app_commands.Choice(name="baccarat", value="cogs.baccarat"),
    app_commands.Choice(name="minesweeper", value="cogs.minesweeper"),
    app_commands.Choice(name="dice", value="cogs.dice"),
    app_commands.Choice(name="balloon", value="cogs.balloon"),
    app_commands.Choice(name="crash", value="cogs.crash"),
    app_commands.Choice(name="slot", value="cogs.slot"),
    app_commands.Choice(name="roulette", value="cogs.roulette"),
    app_commands.Choice(name="rpg", value="cogs.rpg"),
    app_commands.Choice(name="admin", value="cogs.admin"),
    app_commands.Choice(name="<all>", value="*"),
]


def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id in config.OWNER_IDS


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="reload", description="（擁有者）熱重載指定 cog")
    @app_commands.describe(extension="要重載的 cog；選 <all> 會全部重載")
    @app_commands.choices(extension=COG_CHOICES)
    async def reload(
        self,
        interaction: discord.Interaction,
        extension: app_commands.Choice[str],
    ) -> None:
        if not is_owner(interaction):
            await interaction.response.send_message(
                "❌ 只有 bot 擁有者可以使用。", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        targets = (
            [c.value for c in COG_CHOICES if c.value not in ("*",)]
            if extension.value == "*"
            else [extension.value]
        )

        results: list[str] = []
        for name in targets:
            try:
                if name in self.bot.extensions:
                    await self.bot.reload_extension(name)
                else:
                    await self.bot.load_extension(name)
                results.append(f"✅ `{name}`")
            except Exception as exc:  # noqa: BLE001
                results.append(f"❌ `{name}` — {type(exc).__name__}: {exc}")

        await interaction.followup.send("\n".join(results), ephemeral=True)

    @app_commands.command(
        name="reload_module",
        description="（擁有者）重新載入非 cog 的 Python 模組（如 database、config）",
    )
    @app_commands.describe(module="模組名稱，例如 database 或 config")
    async def reload_module(
        self,
        interaction: discord.Interaction,
        module: str,
    ) -> None:
        if not is_owner(interaction):
            await interaction.response.send_message(
                "❌ 只有 bot 擁有者可以使用。", ephemeral=True
            )
            return

        if module not in sys.modules:
            await interaction.response.send_message(
                f"❌ 找不到模組 `{module}`，請確認名稱。", ephemeral=True
            )
            return

        try:
            importlib.reload(sys.modules[module])
            await interaction.response.send_message(
                f"✅ 已重載模組 `{module}`\n"
                "⚠️ 注意：已經 import 過此模組的 cog 不會自動更新，"
                "若有改 config 等共用設定，建議再對相關 cog 跑一次 `/reload`。",
                ephemeral=True,
            )
        except Exception as exc:  # noqa: BLE001
            await interaction.response.send_message(
                f"❌ 重載失敗：{type(exc).__name__}: {exc}", ephemeral=True
            )

    @app_commands.command(name="sync", description="（擁有者）重新同步斜線指令")
    @app_commands.describe(scope="同步範圍：global 或 guild（本伺服器）")
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="本伺服器（立即生效）", value="guild"),
            app_commands.Choice(name="全域（最久要等 1 小時）", value="global"),
        ]
    )
    async def sync(
        self,
        interaction: discord.Interaction,
        scope: app_commands.Choice[str],
    ) -> None:
        if not is_owner(interaction):
            await interaction.response.send_message(
                "❌ 只有 bot 擁有者可以使用。", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if scope.value == "guild" and interaction.guild is not None:
                self.bot.tree.copy_global_to(guild=interaction.guild)
                synced = await self.bot.tree.sync(guild=interaction.guild)
                await interaction.followup.send(
                    f"✅ 已同步 {len(synced)} 個指令到本伺服器。", ephemeral=True
                )
            else:
                synced = await self.bot.tree.sync()
                await interaction.followup.send(
                    f"✅ 已同步 {len(synced)} 個全域指令。可能需等待 Discord 推送。",
                    ephemeral=True,
                )
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(
                f"❌ 同步失敗：{type(exc).__name__}: {exc}", ephemeral=True
            )

    @app_commands.command(name="clear_guild_commands", description="（擁有者）清除本伺服器的重複斜線指令")
    async def clear_guild_commands(self, interaction: discord.Interaction) -> None:
        if not is_owner(interaction):
            await interaction.response.send_message(
                "❌ 只有 bot 擁有者可以使用。", ephemeral=True
            )
            return
        if interaction.guild is None:
            await interaction.response.send_message(
                "❌ 這個指令只能在伺服器內使用。", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            self.bot.tree.clear_commands(guild=interaction.guild)
            synced = await self.bot.tree.sync(guild=interaction.guild)
            await interaction.followup.send(
                f"✅ 已清除本伺服器專用指令，目前本伺服器專用指令數：{len(synced)}。\n"
                "Discord 可能需要幾秒到一分鐘刷新顯示；之後會只剩全域指令那一份。",
                ephemeral=True,
            )
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(
                f"❌ 清除失敗：{type(exc).__name__}: {exc}", ephemeral=True
            )

    @app_commands.command(name="reset_cooldown", description="（擁有者）重置玩家 /claim、/daily 或 /shop 冷卻")
    @app_commands.describe(user="要重置冷卻的玩家", cooldown="要重置的冷卻")
    @app_commands.choices(
        cooldown=[
            app_commands.Choice(name="/claim", value="claim"),
            app_commands.Choice(name="/daily", value="daily"),
            app_commands.Choice(name="/shop", value="shop"),
            app_commands.Choice(name="/claim + /daily", value="both"),
            app_commands.Choice(name="/claim + /daily + /shop", value="all"),
        ]
    )
    async def reset_cooldown(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        cooldown: app_commands.Choice[str],
    ) -> None:
        if not is_owner(interaction):
            await interaction.response.send_message(
                "❌ 只有 bot 擁有者可以使用。", ephemeral=True
            )
            return

        def mutate(target_data: dict) -> dict:
            if cooldown.value in {"claim", "both", "all"}:
                target_data["last_claim"] = 0
                target_data["last_claim_slot"] = ""
            if cooldown.value in {"daily", "both", "all"}:
                target_data["last_daily"] = 0
                target_data["last_daily_date"] = ""
            if cooldown.value in {"shop", "all"}:
                target_data["shop_purchases"] = {}
            return {
                "last_claim": int(target_data.get("last_claim", 0)),
                "last_daily": int(target_data.get("last_daily", 0)),
                "last_claim_slot": str(target_data.get("last_claim_slot", "")),
                "last_daily_date": str(target_data.get("last_daily_date", "")),
                "shop_purchases": dict(target_data.get("shop_purchases", {})),
            }

        result = await db.mutate_user(user.id, mutate)
        reset_text = {
            "claim": "/claim",
            "daily": "/daily",
            "shop": "/shop",
            "both": "/claim 和 /daily",
            "all": "/claim、/daily 和 /shop",
        }[cooldown.value]
        await interaction.response.send_message(
            f"✅ 已重置 {user.mention} 的 **{reset_text}** 冷卻。\n"
            f"`last_claim_slot={result['last_claim_slot']}`，"
            f"`last_daily_date={result['last_daily_date']}`，"
            f"`shop_purchases={result['shop_purchases']}`",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
