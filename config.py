"""Bot 設定檔

私人設定透過環境變數或專案根目錄的 .env 載入。
複製 .env.example 為 .env 後自行填寫；不要提交 .env。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Existing environment variables take precedence over .env.
load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)


def _owner_ids_from_env() -> list[int]:
    values = os.getenv("DISCORD_OWNER_IDS", "").strip()
    if not values:
        return []
    ids = []
    for value in values.split(","):
        value = value.strip()
        if not value.isascii() or not value.isdigit() or int(value) <= 0:
            raise ValueError("DISCORD_OWNER_IDS 必須是以逗號分隔的正整數使用者 ID。")
        ids.append(int(value))
    return ids


BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()

# Bot 擁有者的 Discord 使用者 ID；只有這些 ID 才能用 /reload 等管理指令。
# 取得方式：在 Discord 開發者模式下，右鍵自己的頭像 → 複製使用者 ID
OWNER_IDS: list[int] = _owner_ids_from_env()

COMMAND_PREFIX = "!"

CLAIM_AMOUNT = 50
CLAIM_COOLDOWN_SECONDS = 15 * 60
CLAIM_SKILL_DRAWS = 0
CLAIM_WEAPON_DRAWS = 0
CLAIM_ARMOR_DRAWS = 0
CLAIM_SHOP_ITEM_CHANCE = 0.0

DAILY_AMOUNT = 200
DAILY_COOLDOWN_SECONDS = 24 * 60 * 60
DAILY_SKILL_DRAWS = 0
DAILY_WEAPON_DRAWS = 0
DAILY_ARMOR_DRAWS = 0
DAILY_SHOP_ITEM_CHANCE = 0.10

LOCAL_TIMEZONE = "Asia/Taipei"
BANK_INTEREST_RATE = 0.01
BANK_INTEREST_INTERVAL_HOURS = 4
BANK_INTEREST_INTERVAL_SECONDS = BANK_INTEREST_INTERVAL_HOURS * 60 * 60
BANK_WITHDRAW_LOCK_SECONDS = 24 * 60 * 60

STARTING_BALANCE = 1_000

DATA_FILE = "data.json"

# Base return before integer rounding and consumable items.
EV_TARGET = 0.99
CLIMB_CONTINUE_RETURN = 1.0
PENDING_BET_REFUND_SECONDS = 6 * 60
MIN_GAMBLE_BET = 10

INVEST_PRICE_CACHE_SECONDS = 60
INVEST_MAX_LEVERAGE = 5.0
# Simulation parameters, not live exchange-specific rates.
INVEST_TRADING_FEE_RATE = "0.00055"
INVEST_MAINTENANCE_MARGIN_RATE = "0.005"
INVEST_LIQUIDATION_CHECK_SECONDS = 60
INVEST_TRANSACTION_HISTORY_LIMIT = 100

SHOP_ITEMS = {
    "insurance": {
        "name": "保險券",
        "price": 250,
        "description": "本局賭博若輸掉，返還實際虧損的 50%，不會因部分虧損反而獲利。",
    },
    "defuse": {
        "name": "拆彈券",
        "price": 400,
        "description": "本局 climb/mines 第一次踩到炸彈時免疫，免疫不增加獎金或倍率。",
    },
    "bonus": {
        "name": "加倍券",
        "price": 150,
        "description": "本局勝利後，額外獲得 25% 淨利，不包含本金。",
    },
}
GAMBLE_INSURANCE_REFUND_RATE = 0.5
GAMBLE_BONUS_RATE = 0.25

EMBED_COLOR = 0xF1C40F
WIN_COLOR = 0x2ECC71
LOSE_COLOR = 0xE74C3C
INFO_COLOR = 0x3498DB
