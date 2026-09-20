"""Virtual investment system for Taiwan stocks, US stocks, and Bitcoin."""

from __future__ import annotations

import time
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config
import investment as market_math
from database import db


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0"}


@dataclass(frozen=True)
class Quote:
    symbol: str
    name: str
    price: float
    currency: str
    market: str
    provider: str = "Yahoo Finance"
    fetched_at: int = 0
    price_time: int = 0


def normalize_symbol(symbol: str) -> str:
    raw = symbol.strip().upper().replace(" ", "")
    if raw.startswith("$"):
        raw = raw[1:]
    if raw in {"BTC", "BITCOIN", "BTCUSD"}:
        return "BTC-USD"
    if is_taiwan_local_symbol(raw):
        return f"{raw}.TW"
    return raw


def is_taiwan_local_symbol(symbol: str) -> bool:
    return (
        bool(symbol)
        and "." not in symbol
        and "-" not in symbol
        and symbol[0].isdigit()
        and symbol.isalnum()
    )


def quote_candidates(symbol: str) -> list[str]:
    raw = symbol.strip().upper().replace(" ", "")
    if raw.startswith("$"):
        raw = raw[1:]
    if is_taiwan_local_symbol(raw):
        return [f"{raw}.TW", f"{raw}.TWO"]
    return [normalize_symbol(raw)]


def market_label(symbol: str) -> str:
    if symbol == "BTC-USD" or symbol.endswith("-USD"):
        return "比特幣 / Crypto"
    if symbol.endswith(".TW") or symbol.endswith(".TWO"):
        return "台股"
    return "美股"


def format_money(value: Any) -> str:
    return f"{market_math.decimal(value):,.2f}"


def format_price(value: float, currency: str) -> str:
    if value >= 100:
        price = f"{value:,.2f}"
    else:
        price = f"{value:,.4f}".rstrip("0").rstrip(".")
    return f"{price} {currency}"


def format_price_time(ts: int) -> str:
    if ts <= 0:
        return "未知"
    dt = datetime.fromtimestamp(ts, ZoneInfo(config.LOCAL_TIMEZONE))
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def position_pnl(position: dict[str, Any], current_price: float) -> tuple[float, float, float]:
    valuation = market_math.value_position(position, current_price)
    return float(valuation.net_pnl), float(valuation.equity), float(valuation.roe)


class PortfolioPageView(discord.ui.View):
    def __init__(self, player_id: int, embeds: list[discord.Embed]) -> None:
        super().__init__(timeout=180)
        self.player_id = player_id
        self.embeds = embeds
        self.page = 0
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("這不是你的投資組合。", ephemeral=True)
            return False
        return True

    def _sync_buttons(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.label == "上一頁":
                    child.disabled = self.page <= 0
                elif child.label == "下一頁":
                    child.disabled = self.page >= len(self.embeds) - 1

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary)
    async def previous_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.page], view=self)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.primary)
    async def next_page(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.page = min(len(self.embeds) - 1, self.page + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.page], view=self)


class YahooPriceProvider:
    def __init__(self) -> None:
        self._cache: dict[str, Quote] = {}

    async def quote(self, symbol: str) -> Quote:
        candidates = quote_candidates(symbol)
        normalized = candidates[0]
        cached = self._cache.get(normalized)
        now = int(time.time())
        if (
            cached is not None
            and now - int(cached.fetched_at) <= config.INVEST_PRICE_CACHE_SECONDS
        ):
            return cached

        params = {"range": "1d", "interval": "1m"}
        data = None
        resolved_symbol = normalized
        last_error = "找不到這個代號的行情"
        async with aiohttp.ClientSession(headers=HTTP_HEADERS) as session:
            for candidate in candidates:
                cached = self._cache.get(candidate)
                if (
                    cached is not None
                    and now - int(cached.fetched_at) <= config.INVEST_PRICE_CACHE_SECONDS
                ):
                    self._cache[normalized] = cached
                    return cached

                url = YAHOO_CHART_URL.format(symbol=candidate)
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        last_error = f"{candidate} 行情來源回傳 HTTP {resp.status}"
                        continue
                    fetched = await resp.json()

                chart = fetched.get("chart", {})
                result = chart.get("result") or []
                if result:
                    data = fetched
                    resolved_symbol = candidate
                    break
                error = chart.get("error") or {}
                last_error = str(error.get("description") or last_error)

        if data is None:
            raise ValueError(last_error)

        chart = data.get("chart", {})
        result = chart.get("result") or []
        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice")
        price_time = int(meta.get("regularMarketTime") or 0)
        if price is None or price_time <= 0:
            indicators = result[0].get("indicators", {})
            quote_rows = indicators.get("quote") or []
            closes = quote_rows[0].get("close", []) if quote_rows else []
            timestamps = result[0].get("timestamp") or []
            paired = [(close, ts) for close, ts in zip(closes, timestamps) if close is not None and ts]
            price, price_time = paired[-1] if paired else (None, 0)
        if price is None or not math.isfinite(float(price)) or float(price) <= 0 or price_time <= 0:
            raise ValueError("行情資料沒有有效價格與時間")

        quote = Quote(
            symbol=str(meta.get("symbol") or normalized),
            name=str(meta.get("shortName") or meta.get("longName") or normalized),
            price=float(price),
            currency=str(meta.get("currency") or ""),
            market=market_label(normalized),
            fetched_at=now,
            price_time=price_time,
        )
        self._cache[normalized] = quote
        self._cache[resolved_symbol] = quote
        return quote

    async def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        params = {"q": query.strip(), "quotes_count": limit, "news_count": 0}
        async with aiohttp.ClientSession(headers=HTTP_HEADERS) as session:
            async with session.get(YAHOO_SEARCH_URL, params=params, timeout=10) as resp:
                if resp.status != 200:
                    raise ValueError(f"搜尋來源回傳 HTTP {resp.status}")
                data = await resp.json()
        rows = []
        for quote in data.get("quotes", [])[:limit]:
            symbol = str(quote.get("symbol") or "")
            if not symbol:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "name": str(
                        quote.get("shortname")
                        or quote.get("longname")
                        or quote.get("name")
                        or symbol
                    ),
                    "type": str(quote.get("quoteType") or ""),
                    "exchange": str(quote.get("exchange") or ""),
                }
            )
        return rows


class Invest(commands.Cog):
    invest = app_commands.Group(name="invest", description="逐倉線性合約模擬投資")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.prices = YahooPriceProvider()

    async def _send(self, interaction: discord.Interaction, *args, **kwargs):
        if interaction.response.is_done():
            return await interaction.followup.send(*args, **kwargs)
        return await interaction.response.send_message(*args, **kwargs)

    async def _quote_or_reply(self, interaction: discord.Interaction, symbol: str) -> Quote | None:
        try:
            return await self.prices.quote(symbol)
        except Exception as exc:
            await self._send(interaction, f"查詢行情失敗：{type(exc).__name__}，請稍後再試。", ephemeral=True)
            return None

    async def check_liquidations(self, user_id: int | None = None) -> int:
        if user_id is None:
            users = await db.investment_users()
        else:
            data = await db.get_user_data(user_id)
            users = {user_id: list(data.get("invest_positions", {}).values())}
        by_symbol: dict[str, set[int]] = {}
        for owner, positions in users.items():
            for position in positions:
                by_symbol.setdefault(str(position["symbol"]), set()).add(owner)
        closed = 0
        for symbol, owners in by_symbol.items():
            try:
                quote = await self.prices.quote(symbol)
            except Exception:
                # No invented price and no liquidation from a failed quote.
                continue
            for owner in owners:
                results = await db.mutate_user(owner, lambda user: market_math.liquidate_user(user, quote, int(time.time())))
                closed += len(results)
        return closed

    @invest.command(name="price", description="查詢台股、美股或 BTC 價格")
    @app_commands.describe(symbol="股票/幣種代號，例如 2330、AAPL、BTC")
    async def price(self, interaction: discord.Interaction, symbol: str) -> None:
        await interaction.response.defer(thinking=True)
        quote = await self._quote_or_reply(interaction, symbol)
        if quote is None:
            return
        embed = discord.Embed(
            title=f"{quote.symbol} 價格",
            description=(f"**{quote.name}**\n市場：**{quote.market}**\n"
                         f"價格：**{format_price(quote.price, quote.currency)}**\n"
                         f"資料時間：**{format_price_time(quote.price_time)}**（台灣時間）"),
            color=config.EMBED_COLOR,
        )
        embed.set_footer(text=f"來源：{quote.provider}，價格快取 {config.INVEST_PRICE_CACHE_SECONDS} 秒")
        await self._send(interaction, embed=embed)

    @invest.command(name="search", description="搜尋股票/幣種代號")
    @app_commands.describe(query="搜尋關鍵字，例如 台積電、2330、Apple、BTC")
    async def search(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            rows = await self.prices.search(query)
        except Exception as exc:
            await self._send(interaction, f"搜尋失敗：{type(exc).__name__}，請稍後再試。", ephemeral=True)
            return
        if not rows:
            await self._send(interaction, "沒有找到符合的代號。", ephemeral=True)
            return
        lines = [f"`{r['symbol']}`｜{r['name']}｜{r['type']}｜{r['exchange']}" for r in rows]
        await self._send(interaction, embed=discord.Embed(title="搜尋結果", description="\n".join(lines), color=config.EMBED_COLOR), ephemeral=True)

    @invest.command(name="buy", description="建立逐倉模擬部位，預算包含開倉費")
    @app_commands.describe(
        symbol="股票/幣種代號，例如 2330、AAPL、BTC",
        amount="投入代幣預算（含開倉費，至少 100，無金額上限）",
        leverage=f"槓桿倍率，1 到 {config.INVEST_MAX_LEVERAGE:g}",
        side="方向：做多或放空",
    )
    @app_commands.choices(side=[app_commands.Choice(name="做多 Long", value="long"), app_commands.Choice(name="放空 Short", value="short")])
    async def buy(self, interaction: discord.Interaction, symbol: str,
                  amount: app_commands.Range[int, 100],
                  leverage: app_commands.Range[float, 1.0, config.INVEST_MAX_LEVERAGE] = 1.0,
                  side: app_commands.Choice[str] | None = None) -> None:
        if amount < 100 or not 1 <= leverage <= config.INVEST_MAX_LEVERAGE:
            await self._send(interaction, f"預算至少 100，槓桿須為 1–{config.INVEST_MAX_LEVERAGE:g} 倍。", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        quote = await self._quote_or_reply(interaction, symbol)
        if quote is None:
            return
        direction = side.value if side else "long"
        result = await db.mutate_user(interaction.user.id, lambda user: market_math.open_position(user, quote, int(amount), leverage, direction, int(time.time())))
        if not result["ok"]:
            message = (f"餘額不足，需要 {amount:,}，目前餘額 {result['balance']:,}。"
                       if result["reason"] == "balance" else "行情無效或早於已使用價格，請取得最新行情後再試。")
            await self._send(interaction, message, ephemeral=True)
            return
        position = result["position"]
        valuation = market_math.value_position(position, quote.price)
        side_text = "做多" if direction == "long" else "放空"
        embed = discord.Embed(
            title="投資加倉成功" if result["merged"] else "投資開倉成功",
            description=(
                f"部位 ID：`{position['id']}`\n標的：**{quote.symbol}** {quote.name}\n"
                f"方向：**{side_text}**｜槓桿：**{leverage:g}x**\n"
                f"本次預算：**{amount:,}**（含開倉費 **{format_money(result['entry_fee'])}**）\n"
                f"本次名目價值：**{format_money(result['notional'])}**\n"
                f"合計投入預算：**{int(position['margin']):,}**\n"
                f"加權平均進場價：**{format_price(float(position['entry_price']), quote.currency)}**\n"
                f"合計模擬數量：**{market_math.decimal(position['quantity']):,.6f}**\n"
                f"參考強平價：**{format_price(float(valuation.liquidation_price), quote.currency)}**\n"
                f"價格時間：**{format_price_time(quote.price_time)}**\n"
                f"目前餘額：**{result['balance']:,}**"
            ), color=config.WIN_COLOR,
        )
        if result["liquidations"]:
            embed.add_field(name="部位處理", value=f"同標的有 {len(result['liquidations'])} 筆部位先觸發強制平倉，結果已記錄於交易紀錄。", inline=False)
        embed.set_footer(text=f"逐倉模擬；開平倉各收名目價值 {market_math.decimal(config.INVEST_TRADING_FEE_RATE) * 100:g}%，Yahoo 行情作估值。")
        await self._send(interaction, embed=embed)

    @invest.command(name="sell", description="平倉投資部位，結算費用與損益")
    @app_commands.describe(position_id="部位 ID，可在 /invest portfolio 查看")
    async def sell(self, interaction: discord.Interaction, position_id: str) -> None:
        await interaction.response.defer(thinking=True)
        position_id = position_id.strip()
        data = await db.get_user_data(interaction.user.id)
        snapshot = data.get("invest_positions", {}).get(position_id)
        if snapshot is None:
            await self._send(interaction, "找不到這個部位 ID，可能已平倉；請查看交易紀錄。", ephemeral=True)
            return
        quote = await self._quote_or_reply(interaction, str(snapshot["symbol"]))
        if quote is None:
            return
        result = await db.mutate_user(interaction.user.id, lambda user: market_math.close_position(user, position_id, quote, int(time.time())))
        if not result["ok"]:
            await self._send(interaction, "部位已平倉或行情早於最近估值，請重新查詢。", ephemeral=True)
            return
        row = result["trade"]
        embed = discord.Embed(
            title="部位觸發強制平倉" if row["type"] == "liquidation" else "投資平倉完成",
            description=(
                f"部位 ID：`{position_id}`\n標的：**{row['symbol']}**\n"
                f"進場價：**{format_price(float(row['entry_price']), quote.currency)}**\n"
                f"平倉價：**{format_price(quote.price, quote.currency)}**\n"
                f"價差損益：**{format_money(row['gross_pnl'])}**\n"
                f"開倉費／平倉費：**{format_money(row['entry_fee'])}／{format_money(row['exit_fee'])}**\n"
                f"淨損益：**{row['pnl']:+,}**｜領回：**{row['payout']:,}**\n"
                f"價格時間：**{format_price_time(quote.price_time)}**\n"
                f"目前餘額：**{result['balance']:,}**"
            ), color=config.LOSE_COLOR if row["pnl"] < 0 else config.WIN_COLOR,
        )
        embed.set_footer(text="領回金額扣除開平倉費後向下取整；逐倉虧損以本部位預算為限。")
        await self._send(interaction, embed=embed)

    @invest.command(name="portfolio", description="查看持倉、費用後權益及參考強平價")
    async def portfolio(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        closed = await self.check_liquidations(interaction.user.id)
        data = await db.get_user_data(interaction.user.id)
        positions = list(data.get("invest_positions", {}).values())
        if not positions:
            await self._send(interaction, f"目前沒有持倉。{'本次檢查有部位觸發強平，詳見 /invest transactions。' if closed else ''}")
            return
        lines = []
        total_margin = 0
        total_equity = market_math.decimal(0)
        failed = 0
        for position in positions:
            try:
                quote = await self.prices.quote(str(position["symbol"]))
                price = quote.price
                price_time = quote.price_time
            except Exception:
                price = position["entry_price"]
                price_time = 0
                failed += 1
            valuation = market_math.value_position(position, price)
            total_margin += int(position["margin"])
            total_equity += valuation.equity
            side_text = "多" if position.get("side", "long") == "long" else "空"
            currency = str(position.get("currency", ""))
            lines.append(
                f"`{position['id']}` **{position['symbol']}** {side_text} {float(position['leverage']):g}x\n"
                f"投入 {int(position['margin']):,}｜均價 {format_price(float(position['entry_price']), currency)}\n"
                f"費後估值 **{format_money(valuation.equity)}**｜淨損益 **{format_money(valuation.net_pnl)}** ({valuation.roe:+.2f}%)\n"
                f"參考強平價 {format_price(float(valuation.liquidation_price), currency)}\n"
                f"行情時間：{format_price_time(price_time) if price_time else '查詢失敗，暫用進場價估算'}"
            )
        pages = [lines[i:i + 5] for i in range(0, len(lines), 5)]
        embeds = []
        for index, page in enumerate(pages, 1):
            embed = discord.Embed(title=f"{interaction.user.display_name} 的投資組合", description="\n\n".join(page), color=config.EMBED_COLOR)
            embed.add_field(name="合計投入", value=format_money(total_margin))
            embed.add_field(name="合計費後估值", value=format_money(total_equity))
            embed.set_footer(text=f"{index}/{len(pages)} 頁｜{closed} 筆強平｜{failed} 筆行情失敗｜代幣模擬，非實股或外匯資產")
            embeds.append(embed)
        await self._send(interaction, embed=embeds[0], view=PortfolioPageView(interaction.user.id, embeds))

    @invest.command(name="transactions", description="查看投資交易、費用與強平紀錄")
    @app_commands.describe(limit="顯示幾筆交易")
    async def transactions(self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 20] = 10) -> None:
        data = await db.get_user_data(interaction.user.id)
        rows = list(data.get("invest_transactions", []))[-int(limit):]
        if not rows:
            await self._send(interaction, "目前沒有投資交易紀錄。", ephemeral=True)
            return
        lines = []
        for row in reversed(rows):
            if row.get("type") == "open":
                action = "加倉" if row.get("merged") else "開倉"
                lines.append(f"{action} `{row['id']}` **{row['symbol']}** {float(row['leverage']):g}x｜預算 {int(row['margin']):,}｜開倉費 {format_money(row.get('entry_fee', 0))}")
            else:
                action = "強制平倉" if row.get("type") == "liquidation" else "平倉"
                lines.append(f"{action} `{row['id']}` **{row['symbol']}**｜淨損益 {int(row.get('pnl', 0)):+,}｜領回 {int(row.get('payout', 0)):,}｜平倉費 {format_money(row.get('exit_fee', 0))}")
        # A large requested history is split to stay within Discord embed limits.
        for start in range(0, len(lines), 10):
            embed = discord.Embed(title="投資交易紀錄", description="\n".join(lines[start:start + 10]), color=config.EMBED_COLOR)
            await self._send(interaction, embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Invest(bot))
