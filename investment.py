"""Isolated linear-contract simulation, settled in integer game tokens.

See INVESTMENT.md for formulas, primary references and model boundaries.
Financial values are Decimal during calculation and strings in JSON.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Any
import uuid

import config


def decimal(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("數值必須有限且有效。")
    return result


@dataclass(frozen=True)
class Valuation:
    gross_pnl: Decimal
    entry_fee: Decimal
    exit_fee: Decimal
    equity: Decimal
    net_pnl: Decimal
    roe: Decimal
    maintenance: Decimal
    liquidation_price: Decimal
    liquidated: bool


def value_position(position: dict[str, Any], price: Any) -> Valuation:
    with localcontext() as context:
        context.prec = 64
        price = decimal(price)
        quantity = decimal(position["quantity"])
        entry = decimal(position["entry_price"])
        margin = decimal(position["margin"])
        if min(price, quantity, entry, margin) <= 0:
            raise ValueError("價格、數量與保證金必須大於零。")
        entry_fee = decimal(position.get("entry_fee", 0))
        fee_rate = decimal(position.get("fee_rate", config.INVEST_TRADING_FEE_RATE))
        maintenance_rate = decimal(position.get("maintenance_rate", config.INVEST_MAINTENANCE_MARGIN_RATE))
        sign = 1 if position.get("side", "long") == "long" else -1
        gross_pnl = quantity * (price - entry) * sign
        exit_fee = quantity * price * fee_rate
        maintenance = quantity * price * maintenance_rate
        available_margin = margin - entry_fee
        before_close = available_margin + gross_pnl
        equity = max(Decimal(0), before_close - exit_fee)
        net_pnl = equity - margin
        combined_rate = maintenance_rate + fee_rate
        if sign == 1:
            liquidation_price = max(Decimal(0), (quantity * entry - available_margin) / (quantity * (1 - combined_rate)))
        else:
            liquidation_price = (quantity * entry + available_margin) / (quantity * (1 + combined_rate))
        return Valuation(gross_pnl, entry_fee, exit_fee, equity, net_pnl,
                         net_pnl / margin * 100, maintenance, liquidation_price,
                         before_close <= maintenance + exit_fee)


def merge_position(base: dict[str, Any], incoming: dict[str, Any], now: int) -> None:
    with localcontext() as context:
        context.prec = 64
        old_quantity = decimal(base["quantity"])
        new_quantity = decimal(incoming["quantity"])
        total = old_quantity + new_quantity
        base["entry_price"] = str((decimal(base["entry_price"]) * old_quantity + decimal(incoming["entry_price"]) * new_quantity) / total)
        base["quantity"] = str(total)
        base["margin"] = int(base["margin"]) + int(incoming["margin"])
        base["entry_fee"] = str(decimal(base.get("entry_fee", 0)) + decimal(incoming.get("entry_fee", 0)))
        base["last_price_time"] = max(int(base.get("last_price_time", 0)), int(incoming.get("last_price_time", 0)))
        for key in ("name", "market", "currency", "fee_rate", "maintenance_rate"):
            if key in incoming:
                base[key] = incoming[key]
        base["updated_at"] = now


def _record(user: dict[str, Any], row: dict[str, Any]) -> None:
    history = user.setdefault("invest_transactions", [])
    history.append(row)
    del history[:-config.INVEST_TRANSACTION_HISTORY_LIMIT]


def _valid_quote(position: dict[str, Any], quote: Any) -> bool:
    return (str(position.get("symbol", "")).upper() == str(quote.symbol).upper()
            and str(position.get("currency", "")) == str(quote.currency)
            and int(quote.price_time) > 0
            and int(quote.price_time) >= int(position.get("last_price_time", 0)))


def _close(user: dict[str, Any], position: dict[str, Any], quote: Any, now: int, *, forced: bool) -> dict[str, Any]:
    valuation = value_position(position, quote.price)
    payout = int(valuation.equity)  # Floor exactly once, when crediting the wallet.
    user["balance"] = int(user.get("balance", 0)) + payout
    user["invest_positions"].pop(position["id"])
    row = {
        "type": "liquidation" if forced else "close", "id": position["id"],
        "symbol": quote.symbol, "side": position.get("side", "long"),
        "margin": int(position["margin"]), "leverage": position["leverage"],
        "entry_price": str(position["entry_price"]), "exit_price": str(quote.price),
        "quantity": str(position["quantity"]), "entry_fee": str(valuation.entry_fee),
        "exit_fee": str(valuation.exit_fee), "gross_pnl": str(valuation.gross_pnl),
        "pnl": payout - int(position["margin"]), "payout": payout,
        "rounding": str(valuation.equity - payout),
        "price_time": int(quote.price_time), "time": now,
    }
    _record(user, row)
    return {"ok": True, "position": dict(position), "trade": row, "balance": int(user["balance"])}


def liquidate_user(user: dict[str, Any], quote: Any, now: int) -> list[dict[str, Any]]:
    """Called inside Database.mutate_user; no network I/O while holding its lock."""
    result = []
    for position in list(user.setdefault("invest_positions", {}).values()):
        if not isinstance(position, dict) or not _valid_quote(position, quote):
            continue
        valuation = value_position(position, quote.price)
        position["last_price_time"] = int(quote.price_time)
        if valuation.liquidated:
            result.append(_close(user, position, quote, now, forced=True))
    return result


def open_position(user: dict[str, Any], quote: Any, amount: int, leverage: Any, side: str, now: int) -> dict[str, Any]:
    """The budget includes initial margin and the opening fee; no amount cap."""
    with localcontext() as context:
        context.prec = 64
        leverage = decimal(leverage)
        price = decimal(quote.price)
        if amount < 100 or not 1 <= leverage <= decimal(config.INVEST_MAX_LEVERAGE):
            return {"ok": False, "reason": "parameters"}
        if price <= 0 or int(quote.price_time) <= 0 or side not in {"long", "short"}:
            return {"ok": False, "reason": "quote"}
        liquidations = liquidate_user(user, quote, now)
        balance = int(user.get("balance", 0))
        if balance < amount:
            return {"ok": False, "reason": "balance", "balance": balance, "liquidations": liquidations}
        positions = user.setdefault("invest_positions", {})
        # A late quote cannot reopen risk or average into a more recent mark.
        for position in positions.values():
            if str(position["symbol"]).upper() == str(quote.symbol).upper() and not _valid_quote(position, quote):
                return {"ok": False, "reason": "stale_quote"}
        fee_rate = decimal(config.INVEST_TRADING_FEE_RATE)
        maintenance_rate = decimal(config.INVEST_MAINTENANCE_MARGIN_RATE)
        notional = decimal(amount) / (1 / leverage + fee_rate)
        entry_fee = notional * fee_rate
        quantity = notional / price
        incoming = {
            "id": uuid.uuid4().hex, "symbol": quote.symbol, "name": quote.name,
            "market": quote.market, "currency": quote.currency, "side": side,
            "margin": amount, "leverage": float(leverage), "quantity": str(quantity),
            "entry_price": str(price), "entry_fee": str(entry_fee),
            "fee_rate": str(fee_rate), "maintenance_rate": str(maintenance_rate),
            "opened_at": now, "last_price_time": int(quote.price_time),
        }
        position = next((p for p in positions.values()
                         if str(p["symbol"]).upper() == str(quote.symbol).upper()
                         and p.get("side", "long") == side and decimal(p["leverage"]) == leverage
                         and decimal(p.get("fee_rate", fee_rate)) == fee_rate
                         and decimal(p.get("maintenance_rate", maintenance_rate)) == maintenance_rate), None)
        merged = position is not None
        if merged:
            merge_position(position, incoming, now)
        else:
            position = incoming
            positions[position["id"]] = position
        user["balance"] = balance - amount
        _record(user, {"type": "open", "id": position["id"], "symbol": quote.symbol,
                       "side": side, "margin": amount, "leverage": float(leverage),
                       "price": str(price), "entry_fee": str(entry_fee), "notional": str(notional),
                       "merged": merged, "price_time": int(quote.price_time), "time": now})
        return {"ok": True, "balance": int(user["balance"]), "position": position,
                "merged": merged, "notional": str(notional), "entry_fee": str(entry_fee),
                "quantity": str(quantity), "liquidations": liquidations}


def close_position(user: dict[str, Any], position_id: str, quote: Any, now: int) -> dict[str, Any]:
    # Fetch the live state under the database lock, not a pre-request snapshot.
    position = user.setdefault("invest_positions", {}).get(position_id)
    if position is None:
        return {"ok": False, "reason": "missing"}
    if not _valid_quote(position, quote):
        return {"ok": False, "reason": "stale_quote"}
    valuation = value_position(position, quote.price)
    return _close(user, position, quote, now, forced=valuation.liquidated)
