"""/help 指令清單"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config


COMMANDS = [
    ("/claim", f"台灣時間每 15 分鐘整點領 {config.CLAIM_AMOUNT:,} 代幣"),
    ("/daily", f"台灣時間每天 00:00 後領 {config.DAILY_AMOUNT:,} 代幣"),
    ("/balance", "查看自己或他人的餘額與戰績"),
    ("/bank info", "查看銀行存款、鎖定與利息狀態"),
    ("/bank deposit", "存錢進銀行，需按確認後才會扣款"),
    ("/bank withdraw", "銀行鎖定結束後領出代幣"),
    ("/leaderboard", "代幣排行榜 Top 10"),
    ("/give", "把代幣轉給其他玩家"),
    ("/shop list · buy · inventory", "道具商店、購買與背包"),
    ("/invest price · search", "台股、美股與 BTC 行情查詢"),
    ("/invest buy · sell · portfolio · transactions", "虛擬投資、平倉與紀錄"),
    ("/baccarat", "百家樂：押閒/莊/和"),
    ("/mines", "踩地雷（3/7/12 顆地雷）"),
    ("/dice", "擲骰子（大/小/圍骰）"),
    ("/balloon", "打氣球，越打越爆"),
    ("/climb", "爬塔遊戲，每層一顆炸彈與三個獎勵"),
    ("/crash", "Crash 倍率崩盤"),
    ("/slot", "拉霸機，三連線中大獎"),
    ("/roulette", "輪盤：紅黑單雙或指定號碼"),
    ("/rpg profile", "回合制 RPG：角色、職業、抽裝、爬塔"),
    ("/rpg classroll", "抽取普通職業"),
    ("/rpg reset_stats", "重製屬性配點並返還點數"),
    ("/rpg skillgacha", "400 元抽取或強化 RPG 技能"),
    ("/rpg skill list", "查看所有技能效果與排序篩選"),
    ("/rpg craft accessory", "消耗 Boss 素材製作飾品"),
    ("/rpg craft skill", "消耗 Boss 戰素材製作或強化秘密終極技能"),
    ("/rpg skillequip", "把技能放進 1-6 技能欄"),
    ("/rpg transmute_weapon", "置換武器類型，只改技能攻擊屬性"),
    ("/rpg dismantle", "分解指定稀有度裝備換鍛造石"),
    ("/rpg enhance once", "消耗鍛造石強化裝備一次"),
    ("/rpg enhance auto", "一鍵連續強化裝備"),
    ("/rpg boss", "挑戰五種高難度流派 Boss"),
    ("/rpg reward", "12 小時一次的金幣或經驗副本"),
]


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="顯示所有指令說明")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        # Discord permits at most 25 fields per embed.
        embeds = []
        for offset in range(0, len(COMMANDS), 20):
            embed = discord.Embed(
                title="🎰 小遊戲機器人 指令清單",
                description="輸入下列斜線指令開始遊戲！",
                color=config.EMBED_COLOR,
            )
            for cmd, desc in COMMANDS[offset:offset + 20]:
                embed.add_field(name=cmd, value=desc, inline=False)
            embed.set_footer(text="新玩家加入即贈送起始代幣")
            embeds.append(embed)
        await interaction.response.send_message(embeds=embeds)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
