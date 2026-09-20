"""Virtual investment system for Taiwan stocks, US stocks, and Bitcoin."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config
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


def format_money(value: float | int) -> str:
    return f"{int(round(float(value))):,}"


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
    entry_price = float(position["entry_price"])
    margin = float(position["margin"])
    leverage = float(position["leverage"])
    side_sign = 1.0 if position.get("side", "long") == "long" else -1.0
    quantity = float(position["quantity"])
    pnl = (current_price - entry_price) * quantity * side_sign
    equity = max(0.0, margin + pnl)
    pnl_pct = 0.0
    if margin > 0:
        pnl_pct = (pnl / margin) * 100.0
    return pnl, equity, pnl_pct


def same_position_bucket(
    position: dict[str, Any],
    *,
    symbol: str,
    side: str,
    leverage: float,
) -> bool:
    return (
        str(position.get("symbol", "")).upper() == symbol.upper()
        and str(position.get("side", "long")) == side
        and abs(float(position.get("leverage", 1.0)) - float(leverage)) < 1e-9
    )


def position_bucket_key(position: dict[str, Any]) -> tuple[str, str, float]:
    return (
        str(position.get("symbol", "")).upper(),
        str(position.get("side", "long")),
        round(float(position.get("leverage", 1.0)), 8),
    )


def merge_position_into(base: dict[str, Any], incoming: dict[str, Any]) -> None:
    base_quantity = float(base.get("quantity", 0.0))
    incoming_quantity = float(incoming.get("quantity", 0.0))
    total_quantity = base_quantity + incoming_quantity
    if total_quantity > 0:
        base["entry_price"] = (
            float(base.get("entry_price", 0.0)) * base_quantity
            + float(incoming.get("entry_price", 0.0)) * incoming_quantity
        ) / total_quantity
    base["quantity"] = total_quantity
    base["margin"] = int(base.get("margin", 0)) + int(incoming.get("margin", 0))
    base["name"] = incoming.get("name", base.get("name", ""))
    base["market"] = incoming.get("market", base.get("market", ""))
    base["currency"] = incoming.get("currency", base.get("currency", ""))
    base["updated_at"] = int(time.time())


def consolidate_positions(positions: dict[str, Any]) -> int:
    buckets: dict[tuple[str, str, float], dict[str, Any]] = {}
    removed: list[str] = []
    for position_id, position in list(positions.items()):
        if not isinstance(position, dict):
            removed.append(position_id)
            continue
        position.setdefault("id", position_id)
        key = position_bucket_key(position)
        if key not in buckets:
            buckets[key] = position
            continue
        merge_position_into(buckets[key], position)
        removed.append(position_id)

    for position_id in removed:
        positions.pop(position_id, None)
    return len(removed)


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
        if price is None:
            indicators = result[0].get("indicators", {})
            quote_rows = indicators.get("quote") or []
            closes = quote_rows[0].get("close", []) if quote_rows else []
            price = next((close for close in reversed(closes) if close is not None), None)
        if price is None:
            price = meta.get("chartPreviousClose")
        if price is None or float(price) <= 0:
            raise ValueError("行情資料沒有有效價格")

        timestamps = result[0].get("timestamp") or []
        price_time = int(meta.get("regularMarketTime") or 0)
        if price_time <= 0 and timestamps:
            price_time = int(timestamps[-1])

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
    invest = app_commands.Group(name="invest", description="虛擬投資")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.prices = YahooPriceProvider()

    async def _quote_or_reply(
        self, interaction: discord.Interaction, symbol: str
    ) -> Quote | None:
        try:
            return await self.prices.quote(symbol)
        except Exception as exc:  # noqa: BLE001
            await interaction.response.send_message(
                f"查詢行情失敗：{type(exc).__name__}: {exc}",
                ephemeral=True,
            )
            return None

    @invest.command(name="price", description="查詢台股、美股或 BTC 價格")
    @app_commands.describe(symbol="股票/幣種代號，例如 2330、AAPL、BTC")
    async def price(self, interaction: discord.Interaction, symbol: str) -> None:
        quote = await self._quote_or_reply(interaction, symbol)
        if quote is None:
            return
        embed = discord.Embed(
            title=f"{quote.symbol} 價格",
            description=(
                f"**{quote.name}**\n"
                f"市場：**{quote.market}**\n"
                f"價格：**{format_price(quote.price, quote.currency)}**\n"
                f"資料時間：**{format_price_time(quote.price_time)}**（台灣時間）"
            ),
            color=config.EMBED_COLOR,
        )
        embed.set_footer(text=f"來源：{quote.provider}，價格快取 {config.INVEST_PRICE_CACHE_SECONDS} 秒")
        await interaction.response.send_message(embed=embed)

    @invest.command(name="search", description="搜尋股票/幣種代號")
    @app_commands.describe(query="搜尋關鍵字，例如 台積電、2330、Apple、BTC")
    async def search(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            rows = await self.prices.search(query)
        except Exception as exc:  # noqa: BLE001
            await interaction.followup.send(
                f"搜尋失敗：{type(exc).__name__}: {exc}",
                ephemeral=True,
            )
            return

        if not rows:
            await interaction.followup.send("沒有找到符合的代號。", ephemeral=True)
            return

        lines = [
            f"`{row['symbol']}`｜{row['name']}｜{row['type']}｜{row['exchange']}"
            for row in rows
        ]
        embed = discord.Embed(
            title="搜尋結果",
            description="\n".join(lines),
            color=config.EMBED_COLOR,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @invest.command(name="buy", description="建立虛擬投資部位")
    @app_commands.describe(
        symbol="股票/幣種代號，例如 2330、AAPL、BTC",
        amount="投入保證金/本金",
        leverage="槓桿倍率，1 到 500",
        side="方向：做多或放空",
    )
    @app_commands.choices(
        side=[
            app_commands.Choice(name="做多 Long", value="long"),
            app_commands.Choice(name="放空 Short", value="short"),
        ]
    )
    async def buy(
        self,
        interaction: discord.Interaction,
        symbol: str,
        amount: app_commands.Range[int, 100, 1_000_000_000],
        leverage: app_commands.Range[float, 1.0, 500.0] = 1.0,
        side: app_commands.Choice[str] | None = None,
    ) -> None:
        quote = await self._quote_or_reply(interaction, symbol)
        if quote is None:
            return

        side_value = side.value if side is not None else "long"
        leverage_value = min(float(leverage), float(config.INVEST_MAX_LEVERAGE))
        margin = int(amount)
        notional = margin * leverage_value
        quantity = notional / quote.price
        now = int(time.time())

        def mutate(user: dict[str, Any]) -> dict[str, Any]:
            balance = int(user.get("balance", 0))
            if balance < margin:
                return {"ok": False, "balance": balance}
            user["balance"] = balance - margin
            positions = user.setdefault("invest_positions", {})
            consolidated = consolidate_positions(positions)
            position = next(
                (
                    row
                    for row in positions.values()
                    if isinstance(row, dict)
                    and same_position_bucket(
                        row,
                        symbol=quote.symbol,
                        side=side_value,
                        leverage=leverage_value,
                    )
                ),
                None,
            )
            merged = position is not None
            if merged:
                old_quantity = float(position.get("quantity", 0.0))
                old_entry_price = float(position.get("entry_price", quote.price))
                new_quantity = old_quantity + quantity
                if new_quantity > 0:
                    position["entry_price"] = (
                        (old_entry_price * old_quantity) + (quote.price * quantity)
                    ) / new_quantity
                position["margin"] = int(position.get("margin", 0)) + margin
                position["quantity"] = new_quantity
                position["name"] = quote.name
                position["market"] = quote.market
                position["currency"] = quote.currency
                position["updated_at"] = now
                position_id = str(position["id"])
            else:
                position_id = uuid.uuid4().hex[:8]
                position = {
                    "id": position_id,
                    "symbol": quote.symbol,
                    "name": quote.name,
                    "market": quote.market,
                    "currency": quote.currency,
                    "side": side_value,
                    "margin": margin,
                    "leverage": leverage_value,
                    "quantity": quantity,
                    "entry_price": quote.price,
                    "opened_at": now,
                }
                positions[position_id] = position
            transactions = user.setdefault("invest_transactions", [])
            transactions.append(
                {
                    "type": "open",
                    "id": position_id,
                    "symbol": quote.symbol,
                    "side": side_value,
                    "margin": margin,
                    "leverage": leverage_value,
                    "price": quote.price,
                    "merged": merged,
                    "time": now,
                }
            )
            del transactions[:-config.INVEST_TRANSACTION_HISTORY_LIMIT]
            return {
                "ok": True,
                "balance": int(user["balance"]),
                "position": position,
                "merged": merged,
                "consolidated": consolidated,
            }

        result = await db.mutate_user(interaction.user.id, mutate)
        if not result["ok"]:
            await interaction.response.send_message(
                f"餘額不足，需要 **{margin:,}**，目前餘額 **{int(result['balance']):,}**。",
                ephemeral=True,
            )
            return

        side_text = "做多" if side_value == "long" else "放空"
        position = result["position"]
        position_id = str(position["id"])
        merged = bool(result.get("merged"))
        embed = discord.Embed(
            title="投資加倉成功" if merged else "投資開倉成功",
            description=(
                f"部位 ID：`{position_id}`\n"
                f"標的：**{quote.symbol}** {quote.name}\n"
                f"方向：**{side_text}**｜槓桿：**{leverage_value:.2f}x**\n"
                f"投入保證金：**{margin:,}**｜名目本金：**{format_money(notional)}**\n"
                f"進場價：**{format_price(quote.price, quote.currency)}**\n"
                f"合併後均價：**{format_price(float(position['entry_price']), quote.currency)}**\n"
                f"合併後保證金：**{int(position['margin']):,}**\n"
                f"價格時間：**{format_price_time(quote.price_time)}**（台灣時間）\n"
                f"本次數量：**{quantity:,.6f}**｜合併後數量：**{float(position['quantity']):,.6f}**\n"
                f"目前餘額：**{int(result['balance']):,}**"
            ),
            color=config.WIN_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @invest.command(name="sell", description="平倉投資部位")
    @app_commands.describe(position_id="部位 ID，可在 /invest portfolio 查看")
    async def sell(self, interaction: discord.Interaction, position_id: str) -> None:
        data = await db.get_user_data(interaction.user.id)
        positions = data.get("invest_positions", {})
        position = positions.get(position_id.strip())
        if not position:
            await interaction.response.send_message("找不到這個部位 ID。", ephemeral=True)
            return

        quote = await self._quote_or_reply(interaction, str(position["symbol"]))
        if quote is None:
            return
        pnl, equity, pnl_pct = position_pnl(position, quote.price)
        payout = int(round(equity))
        now = int(time.time())

        def mutate(user: dict[str, Any]) -> dict[str, Any]:
            positions = user.setdefault("invest_positions", {})
            current = positions.pop(position_id.strip(), None)
            if current is None:
                return {"ok": False}
            user["balance"] = int(user.get("balance", 0)) + payout
            transactions = user.setdefault("invest_transactions", [])
            transactions.append(
                {
                    "type": "close",
                    "id": position_id.strip(),
                    "symbol": quote.symbol,
                    "side": current.get("side", "long"),
                    "margin": int(current["margin"]),
                    "leverage": float(current["leverage"]),
                    "entry_price": float(current["entry_price"]),
                    "exit_price": quote.price,
                    "pnl": int(round(pnl)),
                    "payout": payout,
                    "time": now,
                }
            )
            del transactions[:-config.INVEST_TRANSACTION_HISTORY_LIMIT]
            return {"ok": True, "balance": int(user["balance"])}

        result = await db.mutate_user(interaction.user.id, mutate)
        if not result["ok"]:
            await interaction.response.send_message("這個部位已經不存在。", ephemeral=True)
            return

        liquidated = equity <= 0
        color = config.LOSE_COLOR if pnl < 0 else config.WIN_COLOR
        side_text = "做多" if position.get("side", "long") == "long" else "放空"
        embed = discord.Embed(
            title="投資平倉完成",
            description=(
                f"部位 ID：`{position_id.strip()}`\n"
                f"標的：**{quote.symbol}** {position.get('name', '')}\n"
                f"方向：**{side_text}**｜槓桿：**{float(position['leverage']):.2f}x**\n"
                f"進場價：**{format_price(float(position['entry_price']), quote.currency)}**\n"
                f"平倉價：**{format_price(quote.price, quote.currency)}**\n"
                f"平倉價格時間：**{format_price_time(quote.price_time)}**（台灣時間）\n"
                f"損益：**{format_money(pnl)}**（{pnl_pct:+.2f}%）\n"
                f"領回：**{payout:,}**"
                f"{'｜已爆倉' if liquidated else ''}\n"
                f"目前餘額：**{int(result['balance']):,}**"
            ),
            color=color,
        )
        await interaction.response.send_message(embed=embed)

    @invest.command(name="portfolio", description="查看投資組合")
    async def portfolio(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        def mutate(user: dict[str, Any]) -> dict[str, Any]:
            raw_positions = user.setdefault("invest_positions", {})
            consolidated = consolidate_positions(raw_positions)
            return {
                "positions": list(raw_positions.values()),
                "consolidated": consolidated,
            }

        data = await db.mutate_user(interaction.user.id, mutate)
        positions = sorted(
            list(data.get("positions", [])),
            key=lambda row: (
                str(row.get("symbol", "")),
                str(row.get("side", "long")),
                float(row.get("leverage", 1.0)),
                str(row.get("id", "")),
            ),
        )
        if not positions:
            await interaction.followup.send("目前沒有持倉。")
            return

        lines = []
        total_margin = 0.0
        total_equity = 0.0
        total_pnl = 0.0
        latest_price_time = 0
        failed_quotes = 0
        for position in positions:
            try:
                quote = await self.prices.quote(str(position["symbol"]))
                current_price = quote.price
                currency = quote.currency
                latest_price_time = max(latest_price_time, int(quote.price_time))
            except Exception:  # noqa: BLE001
                current_price = float(position["entry_price"])
                currency = str(position.get("currency", ""))
                failed_quotes += 1
            pnl, equity, pnl_pct = position_pnl(position, current_price)
            total_margin += float(position["margin"])
            total_equity += equity
            total_pnl += pnl
            side_text = "多" if position.get("side", "long") == "long" else "空"
            lines.append(
                f"`{position['id']}` **{position['symbol']}** {side_text} "
                f"{float(position['leverage']):.2f}x｜保證金 {int(position['margin']):,}\n"
                f"進 {format_price(float(position['entry_price']), currency)} → "
                f"現 {format_price(current_price, currency)}｜"
                f"損益 **{format_money(pnl)}** ({pnl_pct:+.2f}%)｜權益 **{format_money(equity)}**"
            )

        page_size = 6
        pages = [lines[index : index + page_size] for index in range(0, len(lines), page_size)]
        embeds: list[discord.Embed] = []
        for page_index, page_lines in enumerate(pages, start=1):
            embed = discord.Embed(
                title=f"{interaction.user.display_name} 的投資組合",
                description="\n\n".join(page_lines),
                color=config.EMBED_COLOR,
            )
            embed.add_field(
                name="投入保證金",
                value=f"**{format_money(total_margin)}**",
                inline=True,
            )
            embed.add_field(
                name="目前權益",
                value=f"**{format_money(total_equity)}**",
                inline=True,
            )
            embed.add_field(
                name="未實現損益",
                value=f"**{format_money(total_pnl)}**",
                inline=True,
            )
            footer_parts = [f"第 {page_index}/{len(pages)} 頁｜總持倉 {len(positions)} 筆"]
            if latest_price_time > 0:
                footer_parts.append(f"最新價格時間：{format_price_time(latest_price_time)}（台灣時間）")
            if failed_quotes:
                footer_parts.append(f"{failed_quotes} 筆暫用進場價估算")
            embed.set_footer(text="｜".join(footer_parts))
            embeds.append(embed)

        if len(embeds) > 1:
            await interaction.followup.send(
                embed=embeds[0],
                view=PortfolioPageView(interaction.user.id, embeds),
            )
        else:
            await interaction.followup.send(embed=embeds[0])

    @invest.command(name="transactions", description="查看最近投資交易紀錄")
    @app_commands.describe(limit="顯示幾筆交易")
    async def transactions(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 20] = 10,
    ) -> None:
        data = await db.get_user_data(interaction.user.id)
        rows = list(data.get("invest_transactions", []))[-int(limit):]
        if not rows:
            await interaction.response.send_message("目前沒有投資交易紀錄。", ephemeral=True)
            return
        lines = []
        for row in reversed(rows):
            if row.get("type") == "open":
                side_text = "做多" if row.get("side") == "long" else "放空"
                action_text = "加倉" if row.get("merged") else "開倉"
                lines.append(
                    f"{action_text} `{row['id']}` **{row['symbol']}** {side_text} "
                    f"{float(row['leverage']):.2f}x｜保證金 {int(row['margin']):,}｜"
                    f"價格 {float(row['price']):,.4f}"
                )
            else:
                lines.append(
                    f"平倉 `{row['id']}` **{row['symbol']}**｜"
                    f"損益 {int(row.get('pnl', 0)):,}｜領回 {int(row.get('payout', 0)):,}"
                )
        embed = discord.Embed(
            title="投資交易紀錄",
            description="\n".join(lines),
            color=config.EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Invest(bot))
