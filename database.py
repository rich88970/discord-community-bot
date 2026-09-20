"""簡易 JSON 資料庫

提供使用者代幣餘額、領取冷卻時間的儲存與讀取。
所有資料存在 data.json，搭配 asyncio 鎖避免競態條件。
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import time
import uuid
from datetime import datetime
from fractions import Fraction
from typing import Any, Callable, Dict
from zoneinfo import ZoneInfo

import config


class Database:
    def __init__(self, path: str = config.DATA_FILE) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._data: Dict[str, Any] = {"users": {}}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fp:
                    self._data = json.load(fp)
            except (json.JSONDecodeError, OSError):
                self._data = {"users": {}}
        else:
            self._data = {"users": {}}

        if "users" not in self._data:
            self._data["users"] = {}

    def _save(self) -> None:
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fp:
            json.dump(self._data, fp, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)

    def _ensure_user(self, user_id: int) -> Dict[str, Any]:
        key = str(user_id)
        today_key = self._today_key()
        interest_period = self._current_bank_interest_period()
        if key not in self._data["users"]:
            self._data["users"][key] = {
                "balance": config.STARTING_BALANCE,
                "last_claim": 0,
                "last_daily": 0,
                "last_claim_slot": "",
                "last_daily_date": "",
                "bank_balance": 0,
                "bank_unlock_at": 0,
                "bank_interest_date": today_key,
                "bank_interest_period": interest_period,
                "bank_interest_total": 0,
                "pending_bets": [],
                "pending_refund_total": 0,
                "pending_refund_history": [],
                "items": {item_id: 0 for item_id in config.SHOP_ITEMS},
                "shop_purchases": {},
                "invest_positions": {},
                "invest_transactions": [],
                "stats": {
                    "wins": 0,
                    "losses": 0,
                    "wagered": 0,
                    "profit": 0,
                },
            }
        else:
            user = self._data["users"][key]
            user.setdefault("balance", config.STARTING_BALANCE)
            user.setdefault("last_claim", 0)
            user.setdefault("last_daily", 0)
            user.setdefault("last_claim_slot", "")
            user.setdefault("last_daily_date", "")
            user.setdefault("bank_balance", 0)
            user.setdefault("bank_unlock_at", 0)
            user.setdefault("bank_interest_date", today_key)
            user.setdefault("bank_interest_period", interest_period)
            user.setdefault("bank_interest_total", 0)
            user.setdefault("pending_bets", [])
            user.setdefault("pending_refund_total", 0)
            user.setdefault("pending_refund_history", [])
            self._ensure_shop_state(user)
            self._ensure_invest_state(user)
            user.setdefault(
                "stats",
                {"wins": 0, "losses": 0, "wagered": 0, "profit": 0},
            )
        return self._data["users"][key]

    def _ensure_invest_state(self, user: Dict[str, Any]) -> None:
        positions = user.get("invest_positions")
        if not isinstance(positions, dict):
            user["invest_positions"] = {}
        transactions = user.get("invest_transactions")
        if not isinstance(transactions, list):
            user["invest_transactions"] = []

    def _ensure_shop_state(self, user: Dict[str, Any]) -> None:
        items = user.get("items")
        if not isinstance(items, dict):
            items = {}
            user["items"] = items
        for item_id in config.SHOP_ITEMS:
            items[item_id] = int(items.get(item_id, 0))

        purchases = user.get("shop_purchases")
        if not isinstance(purchases, dict):
            purchases = {}
            user["shop_purchases"] = purchases
        for item_id, value in list(purchases.items()):
            if item_id not in config.SHOP_ITEMS or not isinstance(value, str):
                purchases.pop(item_id, None)

    def _now_local(self) -> datetime:
        return datetime.now(ZoneInfo(config.LOCAL_TIMEZONE))

    def _today_key(self) -> str:
        return self._now_local().date().isoformat()

    def _current_bank_interest_period(self) -> int:
        return int(self._now_local().timestamp()) // int(config.BANK_INTEREST_INTERVAL_SECONDS)

    def _apply_bank_interest(self, user: Dict[str, Any]) -> bool:
        current_period = self._current_bank_interest_period()
        try:
            last_period = int(user.get("bank_interest_period", current_period))
        except (TypeError, ValueError):
            last_period = current_period

        if last_period >= current_period:
            user["bank_interest_period"] = current_period
            user["bank_interest_date"] = self._today_key()
            return False

        periods = current_period - last_period
        bank_balance = int(user.get("bank_balance", 0))
        principal = max(0, bank_balance)
        interest = int(principal * Fraction(str(config.BANK_INTEREST_RATE))) * periods
        if interest > 0:
            user["balance"] = int(user.get("balance", 0)) + interest
            user["bank_interest_total"] = int(user.get("bank_interest_total", 0)) + interest
            user["bank_last_interest"] = interest
        user["bank_interest_period"] = current_period
        user["bank_interest_date"] = self._today_key()
        return True

    def _apply_all_bank_interest(self) -> bool:
        changed = False
        for raw_user in self._data.get("users", {}).values():
            if isinstance(raw_user, dict):
                raw_user.setdefault("bank_balance", 0)
                raw_user.setdefault("bank_interest_date", self._today_key())
                raw_user.setdefault("bank_interest_period", self._current_bank_interest_period())
                changed = self._apply_bank_interest(raw_user) or changed
        return changed

    def _refund_expired_pending_bets(self, user: Dict[str, Any]) -> bool:
        pending = user.get("pending_bets", [])
        if not isinstance(pending, list) or not pending:
            user["pending_bets"] = []
            return False

        now = int(time.time())
        active: list[Dict[str, Any]] = []
        refund_total = 0
        refund_items: list[str] = []
        refund_history: list[Dict[str, Any]] = []
        for bet in pending:
            if not isinstance(bet, dict):
                continue
            amount = int(bet.get("amount", 0))
            created_at = int(bet.get("created_at", now))
            if amount > 0 and now - created_at >= config.PENDING_BET_REFUND_SECONDS:
                refund_total += amount
                refund_history.append(
                    {
                        "game": str(bet.get("game", "")),
                        "amount": amount,
                        "items": [
                            str(item_id)
                            for item_id in bet.get("items", [])
                            if item_id in config.SHOP_ITEMS
                        ],
                        "created_at": created_at,
                        "refunded_at": now,
                    }
                )
                refund_items.extend(
                    str(item_id)
                    for item_id in bet.get("items", [])
                    if item_id in config.SHOP_ITEMS
                )
            else:
                active.append(bet)

        if refund_total <= 0 and len(active) == len(pending):
            return False

        user["pending_bets"] = active
        if refund_total > 0:
            user["balance"] = int(user.get("balance", 0)) + refund_total
            user["pending_refund_total"] = int(user.get("pending_refund_total", 0)) + refund_total
            user["last_pending_refund"] = refund_total
            history = user.setdefault("pending_refund_history", [])
            if isinstance(history, list):
                history.extend(refund_history)
                del history[:-30]
        if refund_items:
            self._ensure_shop_state(user)
            for item_id in refund_items:
                user["items"][item_id] = int(user["items"].get(item_id, 0)) + 1
        return True

    def _pop_pending_bet(
        self,
        user: Dict[str, Any],
        pending_bet_id: str | None,
        *,
        amount: int | None = None,
    ) -> tuple[int, list[str]]:
        pending = user.get("pending_bets", [])
        if not isinstance(pending, list) or not pending:
            user["pending_bets"] = []
            return 0, []

        remaining: list[Dict[str, Any]] = []
        cleared_amount = 0
        cleared_items: list[str] = []
        cleared = False
        for bet in pending:
            if not isinstance(bet, dict):
                continue
            matches_id = pending_bet_id is not None and bet.get("id") == pending_bet_id
            matches_amount = (
                pending_bet_id is None
                and amount is not None
                and int(bet.get("amount", 0)) == int(amount)
                and not cleared
            )
            if matches_id or matches_amount:
                cleared_amount += int(bet.get("amount", 0))
                for item_id in bet.get("items", []):
                    if item_id in config.SHOP_ITEMS:
                        cleared_items.append(item_id)
                cleared = True
                continue
            remaining.append(bet)
        user["pending_bets"] = remaining
        return cleared_amount, cleared_items

    def _clear_pending_bet(
        self,
        user: Dict[str, Any],
        pending_bet_id: str | None,
        *,
        amount: int | None = None,
    ) -> int:
        cleared_amount, _ = self._pop_pending_bet(
            user, pending_bet_id, amount=amount
        )
        return cleared_amount

    def _place_bet_locked(
        self,
        user: Dict[str, Any],
        amount: int,
        game: str,
        item_ids: list[str],
    ) -> Dict[str, Any]:
        self._ensure_shop_state(user)
        amount = int(amount)
        if amount < config.MIN_GAMBLE_BET:
            return {"ok": False, "reason": "bet_limit"}
        if int(user["balance"]) < amount:
            return {"ok": False, "reason": "balance", "balance": int(user["balance"])}

        selected_items: list[str] = []
        for item_id in item_ids:
            if item_id in config.SHOP_ITEMS and item_id not in selected_items:
                selected_items.append(item_id)

        for item_id in selected_items:
            if int(user["items"].get(item_id, 0)) <= 0:
                return {
                    "ok": False,
                    "reason": "item",
                    "item": item_id,
                    "balance": int(user["balance"]),
                }

        pending_id = uuid.uuid4().hex
        user["balance"] = int(user["balance"]) - amount
        for item_id in selected_items:
            user["items"][item_id] = int(user["items"].get(item_id, 0)) - 1

        user.setdefault("pending_bets", []).append(
            {
                "id": pending_id,
                "game": str(game),
                "amount": amount,
                "items": selected_items,
                "created_at": int(time.time()),
            }
        )
        return {
            "ok": True,
            "pending_bet_id": pending_id,
            "items": selected_items,
            "balance": int(user["balance"]),
        }

    def _apply_safety_updates(self, user: Dict[str, Any]) -> bool:
        bank_changed = self._apply_bank_interest(user)
        pending_changed = self._refund_expired_pending_bets(user)
        return bank_changed or pending_changed

    def _apply_all_safety_updates(self) -> bool:
        changed = False
        for raw_user in self._data.get("users", {}).values():
            if isinstance(raw_user, dict):
                raw_user.setdefault("bank_balance", 0)
                raw_user.setdefault("bank_interest_date", self._today_key())
                raw_user.setdefault("bank_interest_period", self._current_bank_interest_period())
                raw_user.setdefault("pending_bets", [])
                self._ensure_shop_state(raw_user)
                self._ensure_invest_state(raw_user)
                changed = self._apply_safety_updates(raw_user) or changed
        return changed

    async def get_balance(self, user_id: int) -> int:
        async with self._lock:
            user = self._ensure_user(user_id)
            if self._apply_safety_updates(user):
                self._save()
            return int(user["balance"])

    async def run_safety_updates(self) -> bool:
        """Apply time-based safety refunds/interest for every user."""
        async with self._lock:
            changed = self._apply_all_safety_updates()
            if changed:
                self._save()
            return changed

    async def add_balance(self, user_id: int, amount: int) -> int:
        async with self._lock:
            user = self._ensure_user(user_id)
            self._apply_safety_updates(user)
            user["balance"] = int(user["balance"]) + int(amount)
            self._save()
            return int(user["balance"])

    async def deduct_balance(self, user_id: int, amount: int) -> bool:
        """嘗試扣款；餘額不足回傳 False"""
        async with self._lock:
            user = self._ensure_user(user_id)
            interest_changed = self._apply_safety_updates(user)
            if user["balance"] < amount:
                if interest_changed:
                    self._save()
                return False
            user["balance"] -= int(amount)
            self._save()
            return True

    async def place_bet(self, user_id: int, amount: int, game: str) -> str | None:
        """扣除下注並建立 pending bet；回傳 pending id，餘額不足回傳 None。"""
        async with self._lock:
            user = self._ensure_user(user_id)
            changed = self._apply_safety_updates(user)
            result = self._place_bet_locked(user, int(amount), game, [])
            if not result["ok"]:
                if changed:
                    self._save()
                return None
            self._save()
            return str(result["pending_bet_id"])

    async def place_bet_with_items(
        self,
        user_id: int,
        amount: int,
        game: str,
        item_ids: list[str] | None = None,
    ) -> Dict[str, Any]:
        async with self._lock:
            user = self._ensure_user(user_id)
            changed = self._apply_safety_updates(user)
            result = self._place_bet_locked(
                user, int(amount), game, list(item_ids or [])
            )
            if result["ok"] or changed:
                self._save()
            return copy.deepcopy(result)

    async def cancel_pending_bet(
        self,
        user_id: int,
        pending_bet_id: str | None,
        amount: int,
        *,
        restore_items: bool = False,
    ) -> int:
        """取消尚未結算的下注並退款，回傳退款後餘額。"""
        async with self._lock:
            user = self._ensure_user(user_id)
            self._apply_bank_interest(user)
            cleared_amount, cleared_items = self._pop_pending_bet(
                user, pending_bet_id, amount=int(amount)
            )
            if pending_bet_id is not None and cleared_amount <= 0:
                refund_amount = 0
            else:
                refund_amount = cleared_amount if cleared_amount > 0 else int(amount)
            user["balance"] = int(user.get("balance", 0)) + refund_amount
            if restore_items and cleared_items:
                self._ensure_shop_state(user)
                for item_id in cleared_items:
                    user["items"][item_id] = int(user["items"].get(item_id, 0)) + 1
            self._save()
            return int(user["balance"])

    async def get_shop_state(self, user_id: int) -> Dict[str, Any]:
        async with self._lock:
            user = self._ensure_user(user_id)
            if self._apply_safety_updates(user):
                self._save()
            self._ensure_shop_state(user)
            return {
                "balance": int(user.get("balance", 0)),
                "items": copy.deepcopy(user["items"]),
                "shop_purchases": copy.deepcopy(user["shop_purchases"]),
            }

    async def buy_shop_item(self, user_id: int, item_id: str) -> Dict[str, Any]:
        if item_id not in config.SHOP_ITEMS:
            return {"ok": False, "reason": "unknown"}

        today_key = self._today_key()
        item = config.SHOP_ITEMS[item_id]
        price = int(item["price"])
        async with self._lock:
            user = self._ensure_user(user_id)
            self._apply_safety_updates(user)
            self._ensure_shop_state(user)
            if user["shop_purchases"].get(item_id) == today_key:
                return {"ok": False, "reason": "daily_limit"}
            if int(user.get("balance", 0)) < price:
                return {
                    "ok": False,
                    "reason": "balance",
                    "balance": int(user.get("balance", 0)),
                    "price": price,
                }

            user["balance"] = int(user["balance"]) - price
            user["items"][item_id] = int(user["items"].get(item_id, 0)) + 1
            user["shop_purchases"][item_id] = today_key
            self._save()
            return {
                "ok": True,
                "balance": int(user["balance"]),
                "count": int(user["items"][item_id]),
            }

    async def settle_bet(
        self,
        user_id: int,
        pending_bet_id: str | None,
        wager: int,
        profit: int,
        *,
        payout: int = 0,
    ) -> int:
        """結算 pending bet、發放 payout 並更新統計，回傳結算後餘額。"""
        async with self._lock:
            user = self._ensure_user(user_id)
            self._apply_bank_interest(user)
            cleared = self._clear_pending_bet(user, pending_bet_id, amount=int(wager))
            if cleared <= 0:
                # Already settled or refunded: stale UI callbacks must not mint money.
                self._save()
                return int(user["balance"])
            payout = max(0, int(payout))
            wager = cleared
            profit = payout - wager
            if payout:
                user["balance"] = int(user.get("balance", 0)) + int(payout)

            stats = user["stats"]
            stats["wagered"] = int(stats.get("wagered", 0)) + int(wager)
            stats["profit"] = int(stats.get("profit", 0)) + int(profit)
            if profit > 0:
                stats["wins"] = int(stats.get("wins", 0)) + 1
            elif profit < 0:
                stats["losses"] = int(stats.get("losses", 0)) + 1
            self._save()
            return int(user["balance"])

    async def set_balance(self, user_id: int, amount: int) -> int:
        async with self._lock:
            user = self._ensure_user(user_id)
            self._apply_safety_updates(user)
            user["balance"] = int(amount)
            self._save()
            return int(user["balance"])

    async def get_last_claim(self, user_id: int) -> int:
        async with self._lock:
            return int(self._ensure_user(user_id)["last_claim"])

    async def set_last_claim(self, user_id: int, ts: int) -> None:
        async with self._lock:
            self._ensure_user(user_id)["last_claim"] = int(ts)
            self._save()

    async def get_last_daily(self, user_id: int) -> int:
        async with self._lock:
            return int(self._ensure_user(user_id)["last_daily"])

    async def set_last_daily(self, user_id: int, ts: int) -> None:
        async with self._lock:
            self._ensure_user(user_id)["last_daily"] = int(ts)
            self._save()

    async def record_bet(self, user_id: int, wager: int, profit: int) -> None:
        """更新統計：wager 為下注金額，profit 為淨輸贏。"""
        async with self._lock:
            user = self._ensure_user(user_id)
            self._apply_safety_updates(user)
            self._clear_pending_bet(user, None, amount=int(wager))
            stats = user["stats"]
            stats["wagered"] = int(stats.get("wagered", 0)) + int(wager)
            stats["profit"] = int(stats.get("profit", 0)) + int(profit)
            if profit > 0:
                stats["wins"] = int(stats.get("wins", 0)) + 1
            elif profit < 0:
                stats["losses"] = int(stats.get("losses", 0)) + 1
            self._save()

    async def get_stats(self, user_id: int) -> Dict[str, int]:
        async with self._lock:
            user = self._ensure_user(user_id)
            if self._apply_safety_updates(user):
                self._save()
            return dict(user["stats"])

    async def get_user_data(self, user_id: int) -> Dict[str, Any]:
        async with self._lock:
            user = self._ensure_user(user_id)
            if self._apply_safety_updates(user):
                self._save()
            return copy.deepcopy(user)

    async def mutate_user(
        self,
        user_id: int,
        callback: Callable[[Dict[str, Any]], Any],
    ) -> Any:
        async with self._lock:
            user = self._ensure_user(user_id)
            self._apply_safety_updates(user)
            result = callback(user)
            self._save()
            return copy.deepcopy(result)

    async def investment_users(self) -> dict[int, list[dict[str, Any]]]:
        """Snapshot open positions for price polling outside the database lock."""
        async with self._lock:
            return {
                int(uid): copy.deepcopy(list(user.get("invest_positions", {}).values()))
                for uid, user in self._data.get("users", {}).items()
                if isinstance(user, dict) and user.get("invest_positions")
            }

    async def top_balances(self, limit: int = 10):
        async with self._lock:
            if self._apply_all_safety_updates():
                self._save()
            users = self._data.get("users", {})
            ranked = sorted(
                users.items(),
                key=lambda item: int(item[1].get("balance", 0)),
                reverse=True,
            )
            return [
                (int(uid), int(data.get("balance", 0)))
                for uid, data in ranked[:limit]
            ]


db = Database()
