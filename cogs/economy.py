"""經濟系統：/claim、/daily、/balance、/leaderboard、/give"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

import config
from cogs._rematch import try_defer
from cogs.invest import YahooPriceProvider, format_money, position_pnl
from database import db


def format_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} 小時 {minutes} 分 {secs} 秒"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


LOCAL_TZ = ZoneInfo(config.LOCAL_TIMEZONE)
SHOP_ITEM_CHOICES = [
    app_commands.Choice(name=str(item["name"]), value=item_id)
    for item_id, item in config.SHOP_ITEMS.items()
]


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def local_ts(dt: datetime | None = None) -> int:
    return int((dt or local_now()).timestamp())


def claim_slot_key(dt: datetime) -> str:
    slot_minute = (dt.minute // 15) * 15
    return f"{dt:%Y-%m-%d %H}:{slot_minute:02d}"


def next_claim_reset(dt: datetime) -> datetime:
    slot_minute = (dt.minute // 15) * 15
    slot_start = dt.replace(minute=slot_minute, second=0, microsecond=0)
    return slot_start + timedelta(minutes=15)


def daily_date_key(dt: datetime) -> str:
    return dt.date().isoformat()


def next_daily_reset(dt: datetime) -> datetime:
    tomorrow = dt.date() + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=LOCAL_TZ)


def roll_shop_item_reward(user: dict, chance: float) -> dict | None:
    item_ids = list(config.SHOP_ITEMS)
    if not item_ids or random.random() >= chance:
        return None

    item_id = random.choice(item_ids)
    items = user.setdefault("items", {})
    for known_item_id in item_ids:
        items[known_item_id] = int(items.get(known_item_id, 0))
    items[item_id] = int(items.get(item_id, 0)) + 1
    item = config.SHOP_ITEMS[item_id]
    return {
        "id": item_id,
        "name": str(item["name"]),
        "count": int(items[item_id]),
    }


def bank_interest_period_key(dt: datetime) -> int:
    return int(dt.timestamp()) // int(config.BANK_INTEREST_INTERVAL_SECONDS)


def next_bank_interest_reset(dt: datetime) -> datetime:
    next_period = bank_interest_period_key(dt) + 1
    return datetime.fromtimestamp(
        next_period * int(config.BANK_INTEREST_INTERVAL_SECONDS),
        LOCAL_TZ,
    )


def format_local_time(ts: int) -> str:
    if ts <= 0:
        return "現在"
    return datetime.fromtimestamp(ts, LOCAL_TZ).strftime("%Y-%m-%d %H:%M")


class BankDepositConfirmView(discord.ui.View):
    def __init__(self, player_id: int, amount: int):
        super().__init__(timeout=60)
        self.player_id = player_id
        self.amount = int(amount)
        self.used = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message(
                "這不是你的銀行確認視窗。", ephemeral=True
            )
            return False
        return True

    def _disable_all(self) -> None:
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="確認存入", style=discord.ButtonStyle.success)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.used:
            await try_defer(interaction)
            return
        self.used = True
        self._disable_all()

        now = local_now()
        unlock_at = local_ts(now + timedelta(seconds=config.BANK_WITHDRAW_LOCK_SECONDS))

        def mutate(user: dict) -> dict:
            balance = int(user.get("balance", 0))
            if balance < self.amount:
                return {"ok": False, "balance": balance}

            user["balance"] = balance - self.amount
            user["bank_balance"] = int(user.get("bank_balance", 0)) + self.amount
            user["bank_unlock_at"] = unlock_at
            user["bank_interest_date"] = daily_date_key(now)
            user["bank_interest_period"] = bank_interest_period_key(now)
            return {
                "ok": True,
                "balance": int(user["balance"]),
                "bank_balance": int(user["bank_balance"]),
                "unlock_at": int(user["bank_unlock_at"]),
            }

        result = await db.mutate_user(interaction.user.id, mutate)
        if not result["ok"]:
            embed = discord.Embed(
                title="❌ 存款失敗",
                description=(
                    f"你的錢包餘額不足。\n"
                    f"目前餘額：**{int(result['balance']):,}**"
                ),
                color=config.LOSE_COLOR,
            )
            await interaction.response.edit_message(embed=embed, view=self)
            return

        embed = discord.Embed(
            title="🏦 存款成功",
            description=(
                f"已存入：**{self.amount:,}** 代幣\n"
                f"銀行餘額：**{int(result['bank_balance']):,}**\n"
                f"錢包餘額：**{int(result['balance']):,}**\n"
                f"可領出時間：**{format_local_time(int(result['unlock_at']))}**"
            ),
            color=config.WIN_COLOR,
        )
        embed.set_footer(text="每次存款後，銀行資金會重新鎖定 24 小時。")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if self.used:
            await try_defer(interaction)
            return
        self.used = True
        self._disable_all()
        embed = discord.Embed(
            title="已取消存款",
            description="沒有扣除任何代幣。",
            color=config.INFO_COLOR,
        )
        await interaction.response.edit_message(embed=embed, view=self)


class Economy(commands.Cog):
    bank = app_commands.Group(name="bank", description="銀行存款、提款與利息")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.invest_prices = YahooPriceProvider()

    @app_commands.command(name="claim", description="每 15 分鐘領取代幣，金額依機器人設定")
    async def claim(self, interaction: discord.Interaction) -> None:
        if not await try_defer(interaction):
            return

        now = local_now()
        slot_key = claim_slot_key(now)
        next_reset = next_claim_reset(now)

        def mutate(user):
            today = daily_date_key(now)
            if user.get("claim_count_date") != today:
                user["claim_count_date"] = today
                user["claim_count"] = 0
            if int(user.get("claim_count", 0)) >= config.CLAIM_DAILY_LIMIT:
                return {"ok": False, "daily_limit": True}
            if str(user.get("last_claim_slot", "")) == slot_key:
                return {
                    "ok": False,
                    "cooldown": True,
                    "remain": max(0, int(next_reset.timestamp() - now.timestamp())),
                }
            user["balance"] = int(user.get("balance", 0)) + config.CLAIM_AMOUNT
            user["last_claim"] = local_ts(now)
            user["last_claim_slot"] = slot_key
            user["claim_count"] = int(user.get("claim_count", 0)) + 1
            item_reward = roll_shop_item_reward(user, config.CLAIM_SHOP_ITEM_CHANCE)
            return {
                "ok": True,
                "balance": int(user["balance"]),
                "item_reward": item_reward,
            }

        result = await db.mutate_user(interaction.user.id, mutate)
        if not result["ok"]:
            if result.get("daily_limit"):
                await interaction.followup.send(
                    f"今天已領滿 {config.CLAIM_DAILY_LIMIT} 次，台灣時間午夜重置。可透過 RPG 挑戰取得更多獎勵。",
                    ephemeral=True,
                )
                return
            if result.get("cooldown"):
                remain = int(result["remain"])
                embed = discord.Embed(
                    title="⏳ 還在冷卻中",
                    description=f"請於 **{format_seconds(remain)}** 後再來領取！",
                    color=config.LOSE_COLOR,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

        embed = discord.Embed(
            title="✅ 已領取代幣",
            description=(
                f"你領到了 **{config.CLAIM_AMOUNT:,}** 代幣！\n"
                f"目前餘額：**{result['balance']:,}**"
            ),
            color=config.WIN_COLOR,
        )
        if result.get("item_reward"):
            item = result["item_reward"]
            embed.add_field(
                name="額外道具",
                value=f"抽到 **{item['name']}** x1（目前持有 {item['count']}）",
                inline=False,
            )
        embed.set_footer(text=f"每 15 分鐘刷新；每日最多 {config.CLAIM_DAILY_LIMIT} 次，台灣時間午夜重置")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="daily", description="每日領取代幣，金額依機器人設定")
    async def daily(self, interaction: discord.Interaction) -> None:
        if not await try_defer(interaction):
            return

        now = local_now()
        today_key = daily_date_key(now)
        next_reset = next_daily_reset(now)

        def mutate(user):
            if str(user.get("last_daily_date", "")) == today_key:
                return {
                    "ok": False,
                    "cooldown": True,
                    "remain": max(0, int(next_reset.timestamp() - now.timestamp())),
                }
            user["balance"] = int(user.get("balance", 0)) + config.DAILY_AMOUNT
            user["last_daily"] = local_ts(now)
            user["last_daily_date"] = today_key
            item_reward = roll_shop_item_reward(user, config.DAILY_SHOP_ITEM_CHANCE)
            return {
                "ok": True,
                "balance": int(user["balance"]),
                "item_reward": item_reward,
            }

        result = await db.mutate_user(interaction.user.id, mutate)
        if not result["ok"]:
            if result.get("cooldown"):
                remain = int(result["remain"])
                embed = discord.Embed(
                    title="⏳ 今天已經領過了",
                    description=f"請於 **{format_seconds(remain)}** 後再來！",
                    color=config.LOSE_COLOR,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

        embed = discord.Embed(
            title="🎁 每日獎勵",
            description=(
                f"你領到了 **{config.DAILY_AMOUNT:,}** 代幣！\n"
                f"目前餘額：**{result['balance']:,}**"
            ),
            color=config.WIN_COLOR,
        )
        if result.get("item_reward"):
            item = result["item_reward"]
            embed.add_field(
                name="額外道具",
                value=f"抽到 **{item['name']}** x1（目前持有 {item['count']}）",
                inline=False,
            )
        embed.set_footer(text="台灣時間每天 00:00 刷新")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="balance", description="查看自己的代幣餘額")
    @app_commands.describe(user="要查看的玩家（可省略）")
    async def balance(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
    ) -> None:
        await interaction.response.defer(thinking=True)
        target = user or interaction.user
        data = await db.get_user_data(target.id)
        bal = int(data.get("balance", 0))
        bank_balance = int(data.get("bank_balance", 0))
        stats = await db.get_stats(target.id)
        positions = list(data.get("invest_positions", {}).values())
        invest_margin = 0.0
        invest_equity = 0.0
        invest_pnl = 0.0
        invest_price_failures = 0

        for position in positions:
            invest_margin += float(position.get("margin", 0))
            try:
                quote = await self.invest_prices.quote(str(position["symbol"]))
                current_price = quote.price
            except Exception:  # noqa: BLE001
                current_price = float(position.get("entry_price", 0))
                invest_price_failures += 1
            pnl, equity, _ = position_pnl(position, current_price)
            invest_equity += equity
            invest_pnl += pnl

        embed = discord.Embed(
            title=f"💰 {target.display_name} 的錢包",
            color=config.EMBED_COLOR,
        )
        embed.add_field(name="餘額", value=f"**{bal:,}** 代幣", inline=False)
        embed.add_field(name="銀行", value=f"**{bank_balance:,}** 代幣", inline=False)
        embed.add_field(
            name="戰績",
            value=(
                f"勝場：**{stats.get('wins', 0)}**　"
                f"敗場：**{stats.get('losses', 0)}**\n"
                f"總投注：**{stats.get('wagered', 0):,}**\n"
                f"淨輸贏：**{stats.get('profit', 0):+,}**"
            ),
            inline=False,
        )
        if positions:
            pnl_pct = (invest_pnl / invest_margin * 100.0) if invest_margin > 0 else 0.0
            note = ""
            if invest_price_failures:
                note = f"\n有 {invest_price_failures} 筆行情暫時無法更新，已用進場價估算。"
            embed.add_field(
                name="投資績效",
                value=(
                    f"持倉數：**{len(positions)}**\n"
                    f"投入保證金：**{format_money(invest_margin)}**\n"
                    f"目前權益：**{format_money(invest_equity)}**\n"
                    f"未實現損益：**{format_money(invest_pnl)}**（{pnl_pct:+.2f}%）"
                    f"{note}"
                ),
                inline=False,
            )
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.followup.send(embed=embed)

    @bank.command(name="info", description="查看自己的銀行存款與利息狀態")
    async def bank_info(self, interaction: discord.Interaction) -> None:
        data = await db.get_user_data(interaction.user.id)
        balance = int(data.get("balance", 0))
        bank_balance = int(data.get("bank_balance", 0))
        unlock_at = int(data.get("bank_unlock_at", 0))
        total_interest = int(data.get("bank_interest_total", 0))
        now_ts = local_ts()
        next_interest = next_bank_interest_reset(local_now())

        if bank_balance <= 0:
            unlock_line = "沒有存款"
        elif unlock_at > now_ts:
            unlock_line = (
                f"鎖定中，剩餘 **{format_seconds(unlock_at - now_ts)}**\n"
                f"可領出時間：**{format_local_time(unlock_at)}**"
            )
        else:
            unlock_line = "目前可領出"

        embed = discord.Embed(
            title="🏦 銀行資訊",
            description=(
                f"錢包餘額：**{balance:,}**\n"
                f"銀行存款：**{bank_balance:,}**\n"
                f"累計利息：**{total_interest:,}**"
            ),
            color=config.EMBED_COLOR,
        )
        embed.add_field(name="領出狀態", value=unlock_line, inline=False)
        embed.add_field(
            name="利息",
            value=(
                f"每 **{config.BANK_INTEREST_INTERVAL_HOURS} 小時** 發放銀行存款的 "
                f"**{config.BANK_INTEREST_RATE * 100:g}%** 到錢包。\n"
                f"計息本金上限 **{config.BANK_INTEREST_PRINCIPAL_CAP:,}**，每期向下取整。\n"
                f"下次發放：**{next_interest:%Y-%m-%d %H:%M}**"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bank.command(name="deposit", description="存錢進銀行，需按確認後才會扣款")
    @app_commands.describe(amount="要存入銀行的金額")
    async def bank_deposit(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, 100_000_000_000_000],
    ) -> None:
        data = await db.get_user_data(interaction.user.id)
        balance = int(data.get("balance", 0))
        bank_balance = int(data.get("bank_balance", 0))
        unlock_at = local_ts(local_now() + timedelta(seconds=config.BANK_WITHDRAW_LOCK_SECONDS))
        embed = discord.Embed(
            title="🏦 確認存款",
            description=(
                f"準備存入：**{amount:,}** 代幣\n"
                f"目前錢包：**{balance:,}**\n"
                f"目前銀行：**{bank_balance:,}**\n\n"
                "按下確認後才會扣款。"
            ),
            color=config.INFO_COLOR,
        )
        embed.add_field(
            name="鎖定規則",
            value=(
                "每次存款後，銀行內全部資金會重新鎖定 **24 小時**。\n"
                f"本次確認後預計可領出：**{format_local_time(unlock_at)}**"
            ),
            inline=False,
        )
        embed.add_field(
            name="利息",
            value=(
                f"每 **{config.BANK_INTEREST_INTERVAL_HOURS} 小時** 給予銀行存款 "
                f"**{config.BANK_INTEREST_RATE * 100:g}%**，利息直接進錢包。\n"
                f"計息本金上限 **{config.BANK_INTEREST_PRINCIPAL_CAP:,}**，每期向下取整。"
            ),
            inline=False,
        )
        view = BankDepositConfirmView(interaction.user.id, int(amount))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @bank.command(name="withdraw", description="從銀行領出代幣")
    @app_commands.describe(amount="要領出的金額")
    async def bank_withdraw(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, 1_000_000_000],
    ) -> None:
        now_ts = local_ts()

        def mutate(user: dict) -> dict:
            bank_balance = int(user.get("bank_balance", 0))
            unlock_at = int(user.get("bank_unlock_at", 0))
            if bank_balance <= 0:
                return {"ok": False, "reason": "empty"}
            if unlock_at > now_ts:
                return {
                    "ok": False,
                    "reason": "locked",
                    "remain": unlock_at - now_ts,
                    "unlock_at": unlock_at,
                    "bank_balance": bank_balance,
                }
            if bank_balance < amount:
                return {
                    "ok": False,
                    "reason": "insufficient",
                    "bank_balance": bank_balance,
                }

            user["bank_balance"] = bank_balance - int(amount)
            user["balance"] = int(user.get("balance", 0)) + int(amount)
            if int(user["bank_balance"]) <= 0:
                user["bank_unlock_at"] = 0
            return {
                "ok": True,
                "balance": int(user["balance"]),
                "bank_balance": int(user["bank_balance"]),
            }

        result = await db.mutate_user(interaction.user.id, mutate)
        if not result["ok"]:
            reason = result.get("reason")
            if reason == "locked":
                msg = (
                    f"銀行資金還在鎖定中。\n"
                    f"剩餘：**{format_seconds(int(result['remain']))}**\n"
                    f"可領出時間：**{format_local_time(int(result['unlock_at']))}**"
                )
            elif reason == "insufficient":
                msg = f"銀行餘額不足，目前銀行存款：**{int(result['bank_balance']):,}**"
            else:
                msg = "你目前沒有銀行存款。"

            embed = discord.Embed(
                title="❌ 提款失敗",
                description=msg,
                color=config.LOSE_COLOR,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title="🏦 提款成功",
            description=(
                f"已領出：**{amount:,}** 代幣\n"
                f"銀行餘額：**{int(result['bank_balance']):,}**\n"
                f"錢包餘額：**{int(result['balance']):,}**"
            ),
            color=config.WIN_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="leaderboard", description="代幣排行榜 Top 10")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        ranking = await db.top_balances(10)
        if not ranking:
            await interaction.response.send_message("目前還沒有任何玩家資料。")
            return

        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for idx, (uid, bal) in enumerate(ranking):
            user = interaction.client.get_user(uid)
            name = user.display_name if user else f"使用者 {uid}"
            prefix = medals[idx] if idx < 3 else f"`#{idx + 1:>2}`"
            lines.append(f"{prefix} **{name}** — `{bal:,}`")

        embed = discord.Embed(
            title="🏆 富豪排行榜",
            description="\n".join(lines),
            color=config.EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="give", description="把代幣轉給其他玩家")
    @app_commands.describe(user="收款人", amount="金額")
    async def give(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        amount: app_commands.Range[int, 1, 1_000_000_000_000],
    ) -> None:
        if user.bot or user.id == interaction.user.id:
            await interaction.response.send_message(
                "不能轉給自己或機器人。", ephemeral=True
            )
            return

        if not await db.deduct_balance(interaction.user.id, amount):
            await interaction.response.send_message(
                "你的餘額不足！", ephemeral=True
            )
            return

        await db.add_balance(user.id, amount)
        embed = discord.Embed(
            title="💸 轉帳成功",
            description=(
                f"{interaction.user.mention} 轉了 **{amount:,}** 代幣給 "
                f"{user.mention}"
            ),
            color=config.WIN_COLOR,
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
