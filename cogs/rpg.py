"""Turn-based RPG extension."""

from __future__ import annotations

import math
import random
import re
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands

import config
import rpg_data as data
import rpg_skills
from database import db


GACHA_COST = 100
RARITY_GAMMA = 0.45
LEVEL_POINTS = 4
MAX_INVENTORY = 2000
ITEMS_PER_PAGE = 10
SKILL_ITEMS_PER_PAGE = 5
TEMP_FIXED_SKILL_GACHA_ID: int | None = None
SKILL_ENHANCE_MAX_LEVEL = 5
SKILL_ENHANCE_DAMAGE_BONUS = 0.10
OLD_ACCESSORY_BUDGETS = {
    "pure_hp": 0.80,
    "pure_atk": 0.80,
    "pure_def": 0.80,
    "pure_crit_chance": 0.80,
    "pure_crit_damage": 0.80,
    "defense": 0.58,
    "attack": 0.58,
    "balanced": 0.45,
}
TOWER_POWER_RANGES = {
    "verdant": (0.025, 0.10),
    "brimstone": (0.11, 0.25),
    "astral": (0.27, 0.50),
    "abyssal": (0.55, 1.00),
}
REGULAR_TOWER_STAT_RATIOS = {
    "hp": 0.22,
    "atk": 0.12,
    "def": 0.14,
}
ITEM_DEF_EFFECTIVE_MULTIPLIER = 1.5
WEAPON_POWER_MULTIPLIER = 1.4
EQUIPMENT_SPD_MULTIPLIER = 1.4
ACCESSORY_ATK_MULTIPLIER = 1.3
EQUIPMENT_HP_MULTIPLIER = data.HP_SCALE
BOSS_TRIAL_HP_MULTIPLIER = 1.5
BOSS_TRIAL_ATK_MULTIPLIER = 0.6
BOSS_SKILL_MULTIPLIER = 0.8
BOSS_TRIAL_MATERIAL_DROP_RATE = 0.50
CRIT_OVERCAP_DAMAGE_MULTIPLIER = 0.5
PERCENT_STAT_POINT_KEYS = {"hp", "atk", "def"}
ULTIMATE_SKILL_CRAFT_COST = 5
BOSS_SKILL_PLAYER_SCALE = {
    "控場流": 1.8,
    "基礎射手流": 1.6,
}
BOSS_TRIALS = {
    "blood": {
        "name": "血誓暴君",
        "style": "自殘流",
        "material_name": "血誓殘晶",
        "color": 0xC0392B,
        "variance": 0.90,
        "skill_ids": (11, 12, 13, 14, 15),
    },
    "aegis": {
        "name": "神壁巨像",
        "style": "戰士流",
        "material_name": "神壁核心",
        "color": 0x95A5A6,
        "variance": 0.96,
        "skill_ids": (1, 3, 5, 8, 10),
    },
    "colossus": {
        "name": "巨獸獵主",
        "style": "最大生命值流",
        "material_name": "巨獸解剖片",
        "color": 0xE67E22,
        "variance": 1.00,
        "skill_ids": (53, 54, 55, 56, 57),
    },
    "plague": {
        "name": "瘟疫星核",
        "style": "Debuff流",
        "material_name": "瘟疫星核",
        "color": 0x27AE60,
        "variance": 1.06,
        "skill_ids": (64, 67, 69, 72, 74),
    },
    "deadeye": {
        "name": "弒神神射",
        "style": "基礎射手流",
        "material_name": "弒神箭芯",
        "color": 0x3498DB,
        "variance": 1.12,
        "skill_ids": (32, 34, 36, 38, 41),
    },
}
SKILL_SORT_CHOICES = [
    app_commands.Choice(name="稀有度高到低", value="rarity_desc"),
    app_commands.Choice(name="ID 小到大", value="id_asc"),
    app_commands.Choice(name="ID 大到小", value="id_desc"),
    app_commands.Choice(name="流派", value="school"),
]
SKILL_SCHOOL_CHOICES = [
    app_commands.Choice(name="全部流派", value="all"),
    *[
        app_commands.Choice(name=school, value=school)
        for school in sorted({skill["school"] for skill in rpg_skills.SKILLS})
    ],
]
SKILL_RARITY_CHOICES = [
    app_commands.Choice(name="全部稀有度", value="all"),
    *[
        app_commands.Choice(name=rarity, value=rarity)
        for rarity in data.SKILL_RARITY_RATES
    ],
]
ULTIMATE_SKILL_CHOICES = [
    app_commands.Choice(name=f"{skill['id']} {skill['name']}", value=int(skill["id"]))
    for skill in rpg_skills.SKILLS
    if skill.get("craft_only")
]
AUTO_DISMANTLE_CHOICES = [
    app_commands.Choice(name="不自動分解", value="none"),
    *[
        app_commands.Choice(name=f"{rarity} 以下", value=rarity)
        for rarity in data.RARITY_RATES
    ],
]


def _new_profile() -> Dict[str, Any]:
    return {
        "level": 1,
        "exp": 0,
        "stat_points": 0,
        "allocated": {key: 0 for key in data.POINT_GAINS},
        "inventory": [],
        "equipped": {
            "weapon": None,
            "armor": None,
            "accessories": [None] * data.ACCESSORY_SLOT_COUNT,
        },
        "materials": {},
        "forge_stones": 0,
        "skills": {
            "owned": [],
            "equipped": [None] * data.SKILL_SLOT_COUNT,
            "levels": {},
        },
        "towers": {},
        "battles": {"wins": 0, "losses": 0},
        "class_id": None,
    }


def _ensure_profile(user: Dict[str, Any]) -> Dict[str, Any]:
    profile = user.setdefault("rpg", _new_profile())
    profile.setdefault("level", 1)
    profile.setdefault("exp", 0)
    profile.setdefault("stat_points", 0)
    profile.setdefault("allocated", {})
    for key in data.POINT_GAINS:
        profile["allocated"].setdefault(key, 0)
    for old_key in ("exp", "crit"):
        profile["allocated"].pop(old_key, None)

    profile.setdefault("inventory", [])
    profile["inventory"] = [
        item for item in profile["inventory"] if item.get("slot") in data.ITEM_SLOTS
    ]
    for item in profile["inventory"]:
        _migrate_item_stats(item)

    old_equipped = profile.setdefault("equipped", {})
    accessories = old_equipped.get("accessories")
    if not isinstance(accessories, list):
        legacy = old_equipped.get("accessory")
        accessories = [legacy] if legacy else []
    accessories = (accessories + [None] * data.ACCESSORY_SLOT_COUNT)[: data.ACCESSORY_SLOT_COUNT]
    profile["equipped"] = {
        "weapon": old_equipped.get("weapon"),
        "armor": old_equipped.get("armor"),
        "accessories": accessories,
    }

    profile.setdefault("materials", {})
    profile["forge_stones"] = int(profile.get("forge_stones", 0))
    profile["reward_dungeon_last"] = int(profile.get("reward_dungeon_last", 0))
    skill_state = profile.setdefault("skills", {})
    owned = []
    seen = set()
    duplicate_levels: Dict[int, int] = {}
    for skill_id in skill_state.get("owned", []):
        skill = _skill_by_id(skill_id)
        if not skill:
            continue
        numeric_id = int(skill["id"])
        if numeric_id in seen:
            duplicate_levels[numeric_id] = duplicate_levels.get(numeric_id, 0) + 1
            continue
        owned.append(numeric_id)
        seen.add(numeric_id)
    raw_levels = skill_state.get("levels", {})
    levels: Dict[str, int] = {}
    if isinstance(raw_levels, dict):
        for key, value in raw_levels.items():
            try:
                numeric_id = int(key)
                level = int(value)
            except (TypeError, ValueError):
                continue
            if numeric_id in seen:
                levels[str(numeric_id)] = max(0, min(SKILL_ENHANCE_MAX_LEVEL, level))
    for numeric_id, extra_level in duplicate_levels.items():
        current = levels.get(str(numeric_id), 0)
        levels[str(numeric_id)] = min(SKILL_ENHANCE_MAX_LEVEL, current + extra_level)
    equipped = []
    for skill_id in skill_state.get("equipped", []):
        skill = _skill_by_id(skill_id)
        equipped.append(skill["id"] if skill and skill["id"] in seen else None)
    equipped = (equipped + [None] * data.SKILL_SLOT_COUNT)[: data.SKILL_SLOT_COUNT]
    profile["skills"] = {"owned": owned, "equipped": equipped, "levels": levels}
    profile.setdefault("towers", {})
    for tower_id in data.TOWERS:
        profile["towers"].setdefault(tower_id, 0)
    profile.setdefault("battles", {"wins": 0, "losses": 0})
    if profile.get("class_id") not in data.CLASSES:
        profile["class_id"] = None
    return profile


def _exp_to_next(level: int) -> int:
    return 100 + (level - 1) * 45 + int((level - 1) ** 1.85 * 28)


def _format_stat_value(key: str, value: float) -> str:
    if key in {"crit_chance", "crit_damage", "def_pen", "debuffed_damage"}:
        return f"{value:.1f}%"
    if abs(value - round(value)) < 0.05:
        return str(int(round(value)))
    return f"{value:.1f}"


def _stat_label(key: str) -> str:
    emoji = getattr(data, "STAT_EMOJIS", {}).get(key, "")
    label = data.STAT_LABELS[key]
    return f"{emoji} {label}" if emoji else label


def _format_stats(stats: Dict[str, float], *, include_zero: bool = False) -> str:
    parts = []
    for key in data.BASE_PLAYER_STATS:
        value = float(stats.get(key, 0))
        if not include_zero and abs(value) < 0.05:
            continue
        sign = "+" if value >= 0 else ""
        parts.append(f"{_stat_label(key)} {sign}{_format_stat_value(key, value)}")
    return "、".join(parts) if parts else "無"


def _format_total_stats(stats: Dict[str, float]) -> str:
    return "\n".join(
        f"**{_stat_label(key)}**: {_format_stat_value(key, stats[key])}"
        for key in data.BASE_PLAYER_STATS
    )


def _rarity_index(rarity: str) -> int:
    return list(data.RARITY_RATES).index(rarity)


def _skill_by_id(skill_id: Any) -> Optional[Dict[str, Any]]:
    try:
        numeric_id = int(skill_id)
    except (TypeError, ValueError):
        return None
    return rpg_skills.SKILLS_BY_ID.get(numeric_id)


def _skill_rarity_index(rarity: str) -> int:
    return list(data.SKILL_RARITY_RATES).index(rarity)


def _roll_skill_rarity() -> str:
    roll = random.random()
    cursor = 0.0
    for rarity, rate in data.SKILL_RARITY_RATES.items():
        cursor += rate
        if roll <= cursor:
            return rarity
    return "神話"


def _roll_skill() -> Dict[str, Any]:
    if TEMP_FIXED_SKILL_GACHA_ID is not None:
        fixed_skill = _skill_by_id(TEMP_FIXED_SKILL_GACHA_ID)
        if fixed_skill:
            return dict(fixed_skill)
    rarity = _roll_skill_rarity()
    choices = [skill for skill in rpg_skills.SKILLS if skill["rarity"] == rarity]
    return dict(random.choice(choices))


def _skill_enhance_level(profile: Dict[str, Any], skill_id: Any) -> int:
    try:
        numeric_id = int(skill_id)
    except (TypeError, ValueError):
        return 0
    levels = profile.get("skills", {}).get("levels", {})
    if not isinstance(levels, dict):
        return 0
    try:
        level = int(levels.get(str(numeric_id), 0))
    except (TypeError, ValueError):
        return 0
    return max(0, min(SKILL_ENHANCE_MAX_LEVEL, level))


def _skill_enhance_damage_multiplier(level: int) -> float:
    level = max(0, min(SKILL_ENHANCE_MAX_LEVEL, int(level)))
    return 1.0 + level * SKILL_ENHANCE_DAMAGE_BONUS


def _skill_enhance_effect_multiplier(level: int) -> float:
    return _skill_enhance_damage_multiplier(level)


def _scale_positive_percent(value: float, multiplier: float) -> float:
    return value * multiplier if value > 0 else value


def _scale_positive_int(value: int, multiplier: float) -> int:
    if value <= 0:
        return value
    return max(1, int(round(value * multiplier)))


def _skill_line(
    skill: Dict[str, Any],
    *,
    equipped_slot: int | None = None,
    level: int | None = None,
) -> str:
    prefix = f"{equipped_slot}. " if equipped_slot else ""
    cooldown = int(skill.get("cooldown") or 0)
    multiplier = skill.get("multiplier")
    skill_level = max(0, min(SKILL_ENHANCE_MAX_LEVEL, int(level or 0)))
    enhance_name = f" +{skill_level}" if skill_level else ""
    enhance_detail = ""
    if skill_level:
        enhance_detail = f" / 強化+{skill_level}"
        if skill["type"] == "Buff":
            enhance_detail += f" 效果×{_skill_enhance_effect_multiplier(skill_level):.2f}"
        else:
            enhance_detail += f" 最終×{_skill_enhance_damage_multiplier(skill_level):.2f}"
    if skill["type"] == "Buff":
        detail = f"Buff / CD {cooldown}{enhance_detail}"
    else:
        base = {
            "近戰": "(ATK+近戰)",
            "遠程": "(ATK+RATK)",
            "魔法": "(ATK+MATK)",
        }.get(str(skill["type"]), "ATK")
        detail = f"{skill['type']} / {base}×{float(multiplier or 1) * 100:.0f}% / CD {cooldown}{enhance_detail}"
    return f"{prefix}`{skill['id']}` **[{skill['rarity']}] {skill['name']}{enhance_name}** ({detail})"


def _skill_detail_line(
    skill: Dict[str, Any],
    *,
    equipped_slot: int | None = None,
    level: int | None = None,
) -> str:
    effect = str(skill.get("effect") or "無效果")
    return f"{_skill_line(skill, equipped_slot=equipped_slot, level=level)}\n效果：{effect}"


def _find_item(profile: Dict[str, Any], item_id: str) -> Optional[Dict[str, Any]]:
    item_id = item_id.strip().upper()
    for item in profile["inventory"]:
        current = str(item.get("id", "")).upper()
        if current == item_id or current.startswith(item_id):
            return item
    return None


def _equipped_items(profile: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for slot in data.EQUIPMENT_SLOTS:
        item_id = profile["equipped"].get(slot)
        if item_id:
            item = _find_item(profile, str(item_id))
            if item:
                out[slot] = item
    for idx, item_id in enumerate(profile["equipped"].get("accessories", []), start=1):
        if item_id:
            item = _find_item(profile, str(item_id))
            if item:
                out[f"accessory{idx}"] = item
    return out


def _enhance_multiplier(level: int) -> float:
    return 1.0 + max(0, int(level)) * 0.04


def _item_stat_value(item: Dict[str, Any], key: str, value: float) -> float:
    enhance = int(item.get("enhance", 0))
    final = float(value) * _enhance_multiplier(enhance)
    if key == "def":
        final *= ITEM_DEF_EFFECTIVE_MULTIPLIER
    if enhance >= data.ENHANCE_LIMIT_BREAK_LEVEL and item.get("limit_break_stat") == key:
        final *= data.ENHANCE_LIMIT_BREAK_MULTIPLIER
    if enhance >= data.ENHANCE_FINAL_BREAK_LEVEL and item.get("final_break_stat") == key:
        final *= data.ENHANCE_FINAL_BREAK_MULTIPLIER
    return final


def _apply_crit_chance_overcap(stats: Dict[str, float]) -> None:
    crit_chance = max(0.0, float(stats.get("crit_chance", 0.0)))
    overcap = max(0.0, crit_chance - 100.0)
    stats["crit_chance"] = min(100.0, crit_chance)
    stats["crit_damage"] = max(
        0.0,
        float(stats.get("crit_damage", 0.0)) + overcap * 1.2 * CRIT_OVERCAP_DAMAGE_MULTIPLIER,
    )


def _total_stats(profile: Dict[str, Any]) -> Dict[str, float]:
    stats = dict(data.BASE_PLAYER_STATS)
    for key, points in profile["allocated"].items():
        if key in data.POINT_GAINS and key not in PERCENT_STAT_POINT_KEYS:
            stats[key] = stats.get(key, 0) + data.POINT_GAINS[key] * int(points)
    for item in _equipped_items(profile).values():
        for key, value in item.get("stats", {}).items():
            if key in stats:
                stats[key] = stats.get(key, 0) + _item_stat_value(item, key, float(value))
    class_id = profile.get("class_id")
    if class_id in data.CLASSES:
        class_info = data.CLASSES[class_id]
        for key, multiplier in class_info.get("multipliers", {}).items():
            if key in stats:
                stats[key] *= float(multiplier)
        for key, value in class_info.get("add", {}).items():
            if key in stats:
                stats[key] += float(value)
    for key in PERCENT_STAT_POINT_KEYS:
        points = max(0, int(profile["allocated"].get(key, 0)))
        if points > 0 and key in stats:
            stats[key] *= 1.0 + data.POINT_GAINS[key] * points
    stats["hp"] = max(1, stats["hp"])
    _apply_crit_chance_overcap(stats)
    stats["def_pen"] = min(95.0, max(0.0, stats.get("def_pen", 0.0)))
    stats["debuffed_damage"] = max(0.0, stats.get("debuffed_damage", 0.0))
    return stats


def _roll_rarity() -> str:
    roll = random.random()
    cursor = 0.0
    for rarity, rate in data.RARITY_RATES.items():
        cursor += rate
        if roll <= cursor:
            return rarity
    return "Secret"


def _rarity_multiplier(rarity: str) -> float:
    return (1.0 / data.RARITY_RATES[rarity]) ** RARITY_GAMMA


def _round_stat(key: str, value: float) -> float | int:
    if key in {"crit_chance", "crit_damage", "spd", "melee_damage", "ranged_damage", "magic_damage"}:
        return round(value, 1)
    return max(1, int(round(value)))


def _weapon_crit_profile(rarity: str, name: str) -> tuple[bool, bool]:
    names = data.WEAPON_NAMES_BY_RARITY[rarity]
    idx = names.index(name)
    if idx < 3:
        return True, False
    if idx < 6:
        return False, True
    return True, True


def _weapon_kind(name: str) -> str:
    return data.WEAPON_KIND_BY_NAME.get(name, "sword")


def _item_weapon_kind(item: Dict[str, Any]) -> str:
    kind = str(item.get("weapon_kind") or _weapon_kind(str(item.get("name", ""))))
    return kind if kind in data.WEAPON_KIND_LABELS else "sword"


def _weapon_names_for_kind(rarity: str, kind: str) -> List[str]:
    return [
        name
        for name in data.WEAPON_NAMES_BY_RARITY.get(rarity, [])
        if data.WEAPON_KIND_BY_NAME.get(name) == kind
    ]


def _sync_weapon_skill_damage(item: Dict[str, Any]) -> None:
    if item.get("slot") != "weapon":
        return
    stats = item.setdefault("stats", {})
    for key in ("melee_damage", "ranged_damage", "magic_damage"):
        stats.pop(key, None)
    kind = _weapon_kind(str(item.get("name", "")))
    item["weapon_kind"] = kind
    skill_stat = data.WEAPON_KIND_SKILL_STAT[kind]
    atk = float(stats.get("atk", 0))
    stats[skill_stat] = _round_stat(skill_stat, atk * 5 / 7)


def _transmute_weapon_kind(item: Dict[str, Any], target_kind: str) -> tuple[bool, str, Dict[str, Any]]:
    if item.get("slot") != "weapon":
        return False, "只能置換武器。", {}
    if target_kind not in data.WEAPON_KIND_LABELS:
        return False, "不支援的武器類型。", {}

    rarity = str(item.get("rarity", ""))
    names = _weapon_names_for_kind(rarity, target_kind)
    if not names:
        return False, f"找不到 {rarity} 稀有度的 {data.WEAPON_KIND_LABELS[target_kind]} 武器。", {}

    old_kind = _item_weapon_kind(item)
    if old_kind == target_kind:
        return False, f"這件武器已經是 {data.WEAPON_KIND_LABELS[target_kind]} 類型。", {}

    stats = item.setdefault("stats", {})
    old_skill_stats = {
        key: float(stats.get(key, 0.0))
        for key in data.WEAPON_KIND_SKILL_STAT.values()
        if abs(float(stats.get(key, 0.0))) >= 0.05
    }
    old_name = str(item.get("name", "未知武器"))
    new_name = random.choice(names)
    old_skill_stat = data.WEAPON_KIND_SKILL_STAT.get(old_kind)
    new_skill_stat = data.WEAPON_KIND_SKILL_STAT[target_kind]
    if old_skill_stat and item.get("limit_break_stat") == old_skill_stat:
        item["limit_break_stat"] = new_skill_stat
    if old_skill_stat and item.get("final_break_stat") == old_skill_stat:
        item["final_break_stat"] = new_skill_stat
    item["name"] = new_name
    item["weapon_kind"] = target_kind
    item["weapon_transmuted_v1"] = True
    history = item.get("transmute_history")
    if not isinstance(history, list):
        history = []
        item["transmute_history"] = history
    history.append(
        {
            "from": old_name,
            "to": new_name,
            "from_kind": old_kind,
            "to_kind": target_kind,
            "at": int(time.time()),
        }
    )
    _sync_weapon_skill_damage(item)
    item["score"] = round(
        sum(_item_stat_value(item, key, float(value)) for key, value in stats.items()),
        1,
    )
    return True, "", {
        "old_name": old_name,
        "new_name": new_name,
        "old_kind": old_kind,
        "new_kind": target_kind,
        "old_skill_stats": old_skill_stats,
        "new_skill_stat": new_skill_stat,
        "new_skill_value": float(stats.get(new_skill_stat, 0.0)),
    }


def _apply_weapon_power_scale(item: Dict[str, Any]) -> None:
    if item.get("slot") != "weapon" or item.get("weapon_power_scaled_v2"):
        return
    stats = item.setdefault("stats", {})
    if "atk" in stats:
        stats["atk"] = _round_stat("atk", float(stats["atk"]) * WEAPON_POWER_MULTIPLIER)
    item["weapon_power_scaled_v2"] = True


def _apply_equipment_stat_scales(item: Dict[str, Any]) -> None:
    stats = item.setdefault("stats", {})
    if item.get("slot") in data.ITEM_SLOTS and "hp" in stats and not item.get("equipment_hp_scaled_v5"):
        stats["hp"] = _round_stat("hp", float(stats["hp"]) * EQUIPMENT_HP_MULTIPLIER)
    item["equipment_hp_scaled_v5"] = True

    if "spd" in stats and not item.get("equipment_spd_scaled_v2"):
        stats["spd"] = _round_stat("spd", float(stats["spd"]) * EQUIPMENT_SPD_MULTIPLIER)
    item["equipment_spd_scaled_v2"] = True

    if item.get("slot") == "accessory":
        if "atk" in stats and not item.get("accessory_atk_scaled_v2"):
            stats["atk"] = _round_stat("atk", float(stats["atk"]) * ACCESSORY_ATK_MULTIPLIER)
        item["accessory_atk_scaled_v2"] = True


def _migrate_item_stats(item: Dict[str, Any]) -> None:
    stats = item.setdefault("stats", {})
    stats.pop("crit", None)
    if "hp" in stats and not item.get("hp_scaled_v4"):
        stats["hp"] = _round_stat("hp", float(stats["hp"]) * data.HP_SCALE)
        item["hp_scaled_v4"] = True

    if item.get("slot") == "armor":
        stats.pop("crit_chance", None)
        stats.pop("crit_damage", None)
    elif item.get("slot") == "weapon":
        if "crit_damage" in stats and not item.get("crit_damage_halved_v2"):
            stats["crit_damage"] = round(float(stats["crit_damage"]) / 2, 1)
            item["crit_damage_halved_v2"] = True
        if not item.get("weapon_transmuted_v1"):
            try:
                has_chance, has_damage = _weapon_crit_profile(str(item["rarity"]), str(item["name"]))
            except (KeyError, ValueError):
                has_chance, has_damage = True, True
            if not has_chance:
                stats.pop("crit_chance", None)
            if not has_damage:
                stats.pop("crit_damage", None)
        _apply_weapon_power_scale(item)
        _sync_weapon_skill_damage(item)
    elif item.get("slot") == "accessory":
        if "crit_damage" in stats and not item.get("crit_damage_halved_v2"):
            stats["crit_damage"] = round(float(stats["crit_damage"]) / 2, 1)
            item["crit_damage_halved_v2"] = True
        kind = str(item.get("kind", "balanced"))
        if kind in data.ACCESSORY_KINDS and not item.get("accessory_balance_v2"):
            old_budget = OLD_ACCESSORY_BUDGETS.get(kind, data.ACCESSORY_KINDS[kind]["budget"])
            new_budget = float(data.ACCESSORY_KINDS[kind]["budget"])
            if old_budget > 0 and abs(new_budget - old_budget) > 0.0001:
                scale = new_budget / old_budget
                for key, value in list(stats.items()):
                    stats[key] = _round_stat(key, float(value) * scale)
            item["accessory_balance_v2"] = True

    _apply_equipment_stat_scales(item)
    item["enhance"] = max(0, min(data.ENHANCE_MAX_LEVEL, int(item.get("enhance", 0))))
    if int(item.get("enhance", 0)) < data.ENHANCE_LIMIT_BREAK_LEVEL:
        item.pop("limit_break_stat", None)
    elif item.get("limit_break_stat") not in stats:
        item.pop("limit_break_stat", None)
    if int(item.get("enhance", 0)) < data.ENHANCE_FINAL_BREAK_LEVEL:
        item.pop("final_break_stat", None)
    elif item.get("final_break_stat") not in stats:
        item.pop("final_break_stat", None)
    item["score"] = round(
        sum(_item_stat_value(item, key, float(value)) for key, value in stats.items()),
        1,
    )


def _item_name(slot: str, rarity: str) -> str:
    if slot == "weapon":
        return random.choice(data.WEAPON_NAMES_BY_RARITY[rarity])
    if slot == "armor":
        return random.choice(data.ARMOR_NAMES_BY_RARITY[rarity])
    raise ValueError(f"unsupported equipment slot: {slot}")


def _generate_item(slot: str) -> Dict[str, Any]:
    rarity = _roll_rarity()
    name = _item_name(slot, rarity)
    multiplier = _rarity_multiplier(rarity)
    variance = random.uniform(0.9, 1.1)
    stats = {}
    for key, base in data.EQUIPMENT_BASE_STATS[slot].items():
        if slot == "weapon" and key in {"crit_chance", "crit_damage"}:
            has_chance, has_damage = _weapon_crit_profile(rarity, name)
            if key == "crit_chance" and not has_chance:
                continue
            if key == "crit_damage" and not has_damage:
                continue
        raw = base * multiplier * variance
        stats[key] = _round_stat(key, raw)
    score = sum(float(value) for value in stats.values())
    item = {
        "id": f"{slot[0].upper()}{uuid4().hex[:7].upper()}",
        "slot": slot,
        "rarity": rarity,
        "name": name,
        "stats": stats,
        "score": round(score, 1),
        "enhance": 0,
        "created_at": int(time.time()),
        "crit_damage_halved_v2": True,
        "hp_scaled_v4": True,
    }
    _apply_weapon_power_scale(item)
    _sync_weapon_skill_damage(item)
    _apply_equipment_stat_scales(item)
    item["score"] = round(sum(float(value) for value in item["stats"].values()), 1)
    return item


def _combined_equipment_base_for_stat(stat: str) -> float:
    total = 0.0
    for slot in data.EQUIPMENT_SLOTS:
        total += float(data.EQUIPMENT_BASE_STATS[slot].get(stat, 0))
    return total


def _generate_accessory(rarity: str, kind: str) -> Dict[str, Any]:
    template = data.ACCESSORY_KINDS[kind]
    multiplier = _rarity_multiplier(rarity)
    variance = random.uniform(0.85, 1.15)
    stats = {}
    for stat in template["stats"]:
        base = _combined_equipment_base_for_stat(stat)
        if base <= 0:
            if stat == "crit_chance":
                base = data.EQUIPMENT_BASE_STATS["weapon"]["crit_chance"]
            elif stat == "crit_damage":
                base = data.EQUIPMENT_BASE_STATS["weapon"]["crit_damage"]
        raw = base * multiplier * float(template["budget"]) * variance
        stats[stat] = _round_stat(stat, raw)
    score = sum(float(value) for value in stats.values())
    item = {
        "id": f"A{uuid4().hex[:7].upper()}",
        "slot": "accessory",
        "rarity": rarity,
        "kind": kind,
        "name": f"{data.ACCESSORY_NAMES_BY_KIND[kind]}",
        "stats": stats,
        "score": round(score, 1),
        "enhance": 0,
        "created_at": int(time.time()),
        "crit_damage_halved_v2": True,
        "hp_scaled_v4": True,
        "accessory_balance_v2": True,
    }
    _apply_equipment_stat_scales(item)
    item["score"] = round(sum(float(value) for value in item["stats"].values()), 1)
    return item


def _grant_or_enhance_skill(profile: Dict[str, Any], skill: Dict[str, Any]) -> Dict[str, Any]:
    owned = profile["skills"]["owned"]
    owned_set = set(owned)
    levels = profile["skills"].setdefault("levels", {})
    numeric_id = int(skill["id"])
    duplicate = numeric_id in owned_set
    if not duplicate:
        owned.append(numeric_id)
        levels.setdefault(str(numeric_id), 0)
        return {
            "skill": skill,
            "duplicate": False,
            "new": True,
            "enhanced": False,
            "maxed": False,
            "level": 0,
        }

    current_level = max(0, min(SKILL_ENHANCE_MAX_LEVEL, int(levels.get(str(numeric_id), 0))))
    if current_level >= SKILL_ENHANCE_MAX_LEVEL:
        levels[str(numeric_id)] = SKILL_ENHANCE_MAX_LEVEL
        return {
            "skill": skill,
            "duplicate": True,
            "new": False,
            "enhanced": False,
            "maxed": True,
            "level": SKILL_ENHANCE_MAX_LEVEL,
        }

    new_level = current_level + 1
    levels[str(numeric_id)] = new_level
    return {
        "skill": skill,
        "duplicate": True,
        "new": False,
        "enhanced": True,
        "maxed": False,
        "level": new_level,
    }


def _skill_result_line(result: Dict[str, Any]) -> str:
    skill = result["skill"]
    level = int(result.get("level", 0))
    line = _skill_line(skill, level=level)
    if result.get("new"):
        return f"{line}（新增）"
    if result.get("enhanced"):
        return f"{line}（強化到 +{level}）"
    if result.get("maxed"):
        return f"{line}（已達強化上限）"
    return line


def _grant_skill_draws(profile: Dict[str, Any], draw_count: int) -> List[Dict[str, Any]]:
    results = []
    for _ in range(int(draw_count)):
        skill = _roll_skill()
        results.append(_grant_or_enhance_skill(profile, skill))
    return results


def grant_free_draws(
    user: Dict[str, Any],
    *,
    weapon_draws: int = 0,
    armor_draws: int = 0,
    skill_draws: int = 0,
) -> Dict[str, Any]:
    profile = _ensure_profile(user)
    weapon_draws = int(weapon_draws)
    armor_draws = int(armor_draws)
    skill_draws = int(skill_draws)
    required_slots = weapon_draws + armor_draws
    free_slots = MAX_INVENTORY - len(profile["inventory"])
    if required_slots > free_slots:
        return {
            "ok": False,
            "reason": f"背包空間不足，這次免費裝備抽需要 {required_slots} 格，目前只剩 {free_slots} 格。",
            "required_slots": required_slots,
            "free_slots": free_slots,
        }

    weapons = [_generate_item("weapon") for _ in range(weapon_draws)]
    armors = [_generate_item("armor") for _ in range(armor_draws)]
    profile["inventory"].extend(weapons)
    profile["inventory"].extend(armors)
    skill_results = _grant_skill_draws(profile, skill_draws)
    return {
        "ok": True,
        "weapons": weapons,
        "armors": armors,
        "skills": skill_results,
        "required_slots": required_slots,
        "free_slots": free_slots - required_slots,
    }


def _item_line(item: Dict[str, Any]) -> str:
    slot = data.SLOT_LABELS.get(item["slot"], "飾品")
    kind = ""
    if item["slot"] == "accessory":
        kind = f" / {data.ACCESSORY_KINDS[item.get('kind', 'balanced')]['name']}"
    elif item["slot"] == "weapon":
        kind = f" / {data.WEAPON_KIND_LABELS.get(_item_weapon_kind(item), '武器')}"
    enhance = int(item.get("enhance", 0))
    enhance_text = f" +{enhance}" if enhance else ""
    bonus_parts = []
    if enhance:
        bonus_parts.append(f"強化倍率 {_enhance_multiplier(enhance):.2f}x")
    limit_break_stat = item.get("limit_break_stat")
    if enhance >= data.ENHANCE_LIMIT_BREAK_LEVEL and limit_break_stat in item.get("stats", {}):
        bonus_parts.append(
            f"+15突破 {_stat_label(str(limit_break_stat))} ×{data.ENHANCE_LIMIT_BREAK_MULTIPLIER:g}"
        )
    final_break_stat = item.get("final_break_stat")
    if enhance >= data.ENHANCE_FINAL_BREAK_LEVEL and final_break_stat in item.get("stats", {}):
        bonus_parts.append(
            f"+20突破 {_stat_label(str(final_break_stat))} ×{data.ENHANCE_FINAL_BREAK_MULTIPLIER:g}"
        )
    bonus_text = f"（{'；'.join(bonus_parts)}）" if bonus_parts else ""
    return (
        f"`{item['id']}` **[{item['rarity']}] {item['name']}{enhance_text}**"
        f"（{slot}{kind}）\n{_format_stats(item.get('stats', {}))}{bonus_text}"
    )


def _equipment_rarity_summary(items: List[Dict[str, Any]]) -> str:
    counts = {rarity: 0 for rarity in data.RARITY_RATES}
    for item in items:
        counts[item["rarity"]] += 1
    return " ".join(f"{rarity}:{amount}" for rarity, amount in counts.items() if amount) or "無"


def _auto_dismantle_split(
    items: List[Dict[str, Any]],
    threshold: str,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    if threshold not in data.RARITY_RATES:
        return items, [], 0
    threshold_index = _rarity_index(threshold)
    kept: List[Dict[str, Any]] = []
    dismantled: List[Dict[str, Any]] = []
    stones = 0
    for item in items:
        if _rarity_index(str(item["rarity"])) <= threshold_index:
            dismantled.append(item)
            stones += data.FORGE_STONES_BY_RARITY[str(item["rarity"])]
        else:
            kept.append(item)
    return kept, dismantled, stones


def _skill_rarity_summary(results: List[Dict[str, Any]]) -> str:
    counts = {rarity: 0 for rarity in data.SKILL_RARITY_RATES}
    for result in results:
        counts[result["skill"]["rarity"]] += 1
    return " ".join(f"{rarity}:{amount}" for rarity, amount in counts.items() if amount) or "無"


def format_free_draw_reward(result: Dict[str, Any]) -> str:
    weapons = result.get("weapons", [])
    armors = result.get("armors", [])
    skill_results = result.get("skills", [])
    new_skill_count = sum(1 for result_item in skill_results if result_item.get("new"))
    enhanced_count = sum(1 for result_item in skill_results if result_item.get("enhanced"))
    maxed_count = sum(1 for result_item in skill_results if result_item.get("maxed"))
    lines = []
    if weapons:
        lines.append(f"武器 **{len(weapons)}** 抽：{_equipment_rarity_summary(weapons)}")
    if armors:
        lines.append(f"盔甲 **{len(armors)}** 抽：{_equipment_rarity_summary(armors)}")
    if skill_results:
        lines.append(
            f"技能 **{len(skill_results)}** 抽：{_skill_rarity_summary(skill_results)}；"
            f"新增 **{new_skill_count}**，強化 **{enhanced_count}**，滿級重複 **{maxed_count}**"
        )
    lines.append(f"剩餘背包空間：**{int(result.get('free_slots', 0))}**")
    return "\n".join(lines)


def format_free_draw_highlights(result: Dict[str, Any]) -> str:
    items = [*result.get("weapons", []), *result.get("armors", [])]
    best_items = sorted(
        items,
        key=lambda item: (_rarity_index(item["rarity"]), item["score"]),
        reverse=True,
    )[:4]
    skill_results = result.get("skills", [])
    best_skill_results = sorted(
        skill_results,
        key=lambda result: (_skill_rarity_index(result["skill"]["rarity"]), result["skill"]["id"]),
        reverse=True,
    )[:4]
    parts = []
    if best_items:
        parts.append("裝備亮點\n" + "\n\n".join(_item_line(item) for item in best_items))
    if best_skill_results:
        parts.append("技能亮點\n" + "\n".join(_skill_result_line(result) for result in best_skill_results))
    return "\n\n".join(parts)[:1024] if parts else "無"


def _add_exp(profile: Dict[str, Any], amount: int) -> List[int]:
    levels = []
    profile["exp"] = int(profile.get("exp", 0)) + int(amount)
    while profile["exp"] >= _exp_to_next(int(profile["level"])):
        profile["exp"] -= _exp_to_next(int(profile["level"]))
        profile["level"] = int(profile["level"]) + 1
        profile["stat_points"] = int(profile.get("stat_points", 0)) + LEVEL_POINTS
        levels.append(int(profile["level"]))
    return levels


def _material_id(tower_id: str, floor: int, rarity: str) -> str:
    return f"{tower_id}:{floor}:{rarity}"


def _boss_trial_material_id(boss_id: str) -> str:
    return f"boss:{boss_id}"


def _material_name(material_id: str) -> str:
    if material_id.startswith("boss:"):
        boss_id = material_id.split(":", 1)[1]
        trial = BOSS_TRIALS.get(boss_id)
        if trial:
            return f"Boss戰素材：{trial['material_name']}（{trial['name']}）"
        return f"Boss戰素材：{boss_id}"
    tower_id, floor_text, rarity = material_id.split(":")
    tower = data.TOWERS[tower_id]
    boss_name = tower["bosses"][int(floor_text)][0]
    return f"{tower['material_prefix']}素材 Lv.{floor_text} ({boss_name}) [{rarity}]"


def _boss_trial_material_total(profile: Dict[str, Any]) -> int:
    return sum(
        int(profile["materials"].get(_boss_trial_material_id(boss_id), 0))
        for boss_id in BOSS_TRIALS
    )


def _consume_boss_trial_materials(profile: Dict[str, Any], amount: int) -> List[tuple[str, int]]:
    consumed = []
    remaining = int(amount)
    for boss_id in BOSS_TRIALS:
        mat_id = _boss_trial_material_id(boss_id)
        available = int(profile["materials"].get(mat_id, 0))
        if available <= 0:
            continue
        take = min(available, remaining)
        profile["materials"][mat_id] = available - take
        consumed.append((mat_id, take))
        remaining -= take
        if remaining <= 0:
            break
    return consumed


def _next_tower_floor(
    profile: Dict[str, Any],
    tower_id: str,
    skip_to_boss: bool,
    requested_floor: int | None = None,
) -> Optional[int]:
    highest = int(profile["towers"].get(tower_id, 0))
    if requested_floor is not None:
        max_floor = 50 if highest >= 50 else min(max(highest + 1, 1), 50)
        return int(requested_floor) if 1 <= int(requested_floor) <= max_floor else None
    if highest >= 50:
        return 50 if skip_to_boss else 1
    if skip_to_boss:
        return min(((highest // 10) + 1) * 10, 50)
    return highest + 1


def _legacy_final_tower_boss_stats() -> Dict[str, float]:
    floor = 50
    difficulty = 2.18
    boss_scale = 1.8 + floor / 18
    scale = difficulty * (1.0 + floor * 0.055) * boss_scale
    stats = {
        "hp": int((90 + floor * 18) * scale),
        "atk": int((12 + floor * 2.5) * scale),
        "def": int((4 + floor * 1.05) * scale),
        "spd": round((8 + floor * 0.45) * difficulty, 1),
        "crit_chance": round(3.0 + floor * 0.12 + 4, 1),
        "crit_damage": round(35.0 + floor * 0.55 + 20, 1),
    }
    stats["hp"] = int(round(float(stats["hp"]) * data.HP_SCALE))
    return stats


FINAL_TOWER_BOSS_STATS = _legacy_final_tower_boss_stats()


def _tower_power(tower_id: str, floor: int) -> float:
    start, end = TOWER_POWER_RANGES.get(tower_id, (0.025, 1.0))
    floor = max(1, min(50, int(floor)))
    return start + (end - start) * ((floor - 1) / 49)


def _tower_global_floor(tower_id: str, floor: int) -> int:
    tower_index = list(data.TOWERS).index(tower_id)
    return tower_index * 50 + max(1, min(50, int(floor)))


def _tower_global_level(tower_id: str, floor: int) -> int:
    return max(1, int(round(_tower_global_floor(tower_id, floor) * 0.65)))


def _tower_floor_rewards(tower_id: str, floor: int) -> tuple[int, int]:
    global_floor = _tower_global_floor(tower_id, floor)
    exp = max(1, int(round((45 + global_floor * 12) * 0.5)))
    coins = max(1, int(round((35 + global_floor * 8) * 0.5)))
    return exp, coins


def _format_cooldown(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours} 小時 {minutes} 分 {secs} 秒"
    if minutes:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def _enemy_for_floor(tower_id: str, floor: int) -> Dict[str, Any]:
    tower = data.TOWERS[tower_id]
    is_boss = floor % 10 == 0
    if is_boss:
        name, material_rarity = tower["bosses"][floor]
    else:
        name = random.choice(data.MONSTER_NAMES_BY_TOWER[tower_id])
        material_rarity = None

    level = _tower_global_level(tower_id, floor)
    power = _tower_power(tower_id, floor)
    anchor = FINAL_TOWER_BOSS_STATS
    if is_boss:
        stats = {
            "hp": max(1, int(round(float(anchor["hp"]) * power))),
            "atk": max(1, int(round(float(anchor["atk"]) * power))),
            "def": max(0, int(round(float(anchor["def"]) * power))),
            "spd": round(5.0 + (float(anchor["spd"]) - 5.0) * power, 1),
            "crit_chance": round(5.0 + (float(anchor["crit_chance"]) - 5.0) * power, 1),
            "crit_damage": round(40.0 + (float(anchor["crit_damage"]) - 40.0) * power, 1),
        }
    else:
        stats = {
            "hp": max(1, int(round(float(anchor["hp"]) * power * REGULAR_TOWER_STAT_RATIOS["hp"]))),
            "atk": max(1, int(round(float(anchor["atk"]) * power * REGULAR_TOWER_STAT_RATIOS["atk"]))),
            "def": max(0, int(round(float(anchor["def"]) * power * REGULAR_TOWER_STAT_RATIOS["def"]))),
            "spd": round(4.0 + (float(anchor["spd"]) * 0.75 - 4.0) * power, 1),
            "crit_chance": round(3.0 + 6.0 * power, 1),
            "crit_damage": round(35.0 + 30.0 * power, 1),
        }

    return {
        "name": name,
        "tower_id": tower_id,
        "tower_name": tower["name"],
        "floor": floor,
        "level": level,
        "is_boss": is_boss,
        "material_rarity": material_rarity,
        "stats": stats,
    }


def _boss_trial_skill(skill_id: int) -> Dict[str, Any]:
    skill = dict(rpg_skills.SKILLS_BY_ID[int(skill_id)])
    if skill.get("multiplier") is not None:
        player_scale = BOSS_SKILL_PLAYER_SCALE.get(str(skill.get("school")), 1.0)
        skill["multiplier"] = round(float(skill["multiplier"]) / player_scale * BOSS_SKILL_MULTIPLIER, 4)
    return skill


def _enemy_for_boss_trial(boss_id: str) -> Dict[str, Any]:
    trial = BOSS_TRIALS[boss_id]
    variance = float(trial["variance"])
    anchor = FINAL_TOWER_BOSS_STATS
    stats = {
        "hp": max(1, int(round(float(anchor["hp"]) * 1.60 * variance * BOSS_TRIAL_HP_MULTIPLIER))),
        "atk": max(1, int(round(float(anchor["atk"]) * 1.12 * variance * BOSS_TRIAL_ATK_MULTIPLIER))),
        "def": max(0, int(round(float(anchor["def"]) * 1.12 * variance))),
        "spd": round(float(anchor["spd"]) * 1.12 * variance, 1),
        "crit_chance": round(float(anchor["crit_chance"]) * 1.12 * variance, 1),
        "crit_damage": round(float(anchor["crit_damage"]) * 1.12 * variance, 1),
    }
    skill_damage = round(float(stats["atk"]) * 0.6, 1)
    style = str(trial["style"])
    stats["melee_damage"] = skill_damage if style in {"自殘流", "戰士流"} else 0.0
    stats["ranged_damage"] = skill_damage if style in {"最大生命值流", "基礎射手流"} else 0.0
    stats["magic_damage"] = skill_damage if style == "Debuff流" else 0.0
    return {
        "name": str(trial["name"]),
        "battle_type": "boss_trial",
        "boss_id": boss_id,
        "boss_style": str(trial["style"]),
        "floor": 50,
        "level": 130,
        "is_boss": True,
        "material_rarity": None,
        "color": int(trial["color"]),
        "skills": [_boss_trial_skill(skill_id) for skill_id in trial["skill_ids"]],
        "stats": stats,
    }


def _reward_type_label(reward_type: str) -> str:
    return "金幣" if reward_type == "coins" else "經驗"


def _enemy_for_reward_dungeon(difficulty_id: str, reward_type: str) -> Dict[str, Any]:
    dungeon = data.REWARD_DUNGEONS[difficulty_id]
    enemy = _enemy_for_floor(str(dungeon["tower_id"]), int(dungeon["floor"]))
    label = _reward_type_label(reward_type)
    enemy.update(
        {
            "battle_type": "reward_dungeon",
            "reward_type": reward_type,
            "reward_amount": int(dungeon[reward_type]),
            "reward_difficulty": difficulty_id,
            "dungeon_name": f"{label}副本：{dungeon['name']}（{dungeon['difficulty_name']}）",
            "material_rarity": None,
        }
    )
    return enemy


def _attack_once(
    attacker: str,
    attacker_stats: Dict[str, float],
    defender: str,
    defender_stats: Dict[str, float],
) -> tuple[int, str]:
    crit_chance = min(100.0, max(0.0, float(attacker_stats.get("crit_chance", 0))))
    crit = random.random() < crit_chance / 100
    effective_def = float(defender_stats["def"]) * (1.0 - float(attacker_stats.get("def_pen", 0)) / 100)
    base = max(1.0, float(attacker_stats["atk"]) - effective_def * 0.42)
    damage = base * random.uniform(0.88, 1.12)
    if crit:
        damage *= 1.0 + float(attacker_stats.get("crit_damage", 50)) / 100
    final = max(1, int(round(damage)))
    mark = "（暴擊）" if crit else ""
    return final, f"{attacker} 對 {defender} 造成 **{final}** 傷害{mark}"


def _attack_count(attacker_stats: Dict[str, float], defender_stats: Dict[str, float]) -> int:
    return 2 if float(attacker_stats.get("spd", 0)) > float(defender_stats.get("spd", 0)) * 2 else 1


def _parse_percent(value: Any) -> float:
    match = re.search(r"(\d+(?:\.\d+)?)%", str(value or ""))
    return float(match.group(1)) / 100 if match else 0.0


def _effect_duration(effect: str | None) -> int:
    match = re.search(r"持續\s*(\d+)\s*回合", effect or "")
    if match:
        return int(match.group(1))
    match = re.search(r"(\d+)\s*回合", effect or "")
    return int(match.group(1)) if match else 1


def _effect_stat_groups(effect: str | None) -> List[tuple[List[str], float]]:
    groups = []
    for match in re.finditer(
        r"(ATK|DEF|SPD|MATK|RATK|ATK、RATK、MATK|ATK、RATK、MATK、DEF|暴擊率|暴擊傷害)\s*([+-]\d+(?:\.\d+)?)%",
        effect or "",
    ):
        labels = match.group(1).split("、")
        groups.append((labels, float(match.group(2)) / 100))
    return groups


def _skill_attack_once(
    attacker: str,
    attacker_stats: Dict[str, float],
    defender: str,
    defender_stats: Dict[str, float],
    skill: Dict[str, Any],
    *,
    damage_multiplier: float = 1.0,
    flat_bonus: float = 0.0,
    crit_chance_bonus: float = 0.0,
    crit_damage_bonus: float = 0.0,
    post_mitigation_damage: float = 0.0,
    multiplier_override: float | None = None,
) -> tuple[int, str, bool]:
    skill_type = str(skill.get("type", ""))
    extra_stat = data.SKILL_TYPE_DAMAGE_STAT.get(skill_type)
    base = float(attacker_stats["atk"])
    if extra_stat:
        base += float(attacker_stats.get(extra_stat, 0))
    multiplier = float(multiplier_override if multiplier_override is not None else (skill.get("multiplier") or 1.0))
    crit_chance = max(0.0, float(attacker_stats.get("crit_chance", 0)) + crit_chance_bonus)
    crit_overcap = max(0.0, crit_chance - 100.0)
    crit_damage_bonus += crit_overcap * 1.2 * CRIT_OVERCAP_DAMAGE_MULTIPLIER
    crit = random.random() < min(100.0, crit_chance) / 100
    raw = max(1.0, multiplier * (base + flat_bonus) * damage_multiplier)
    effective_def = float(defender_stats["def"]) * (1.0 - float(attacker_stats.get("def_pen", 0)) / 100)
    damage = max(1.0, raw - effective_def * 0.42)
    damage *= random.uniform(0.88, 1.12)
    if crit:
        damage *= 1.0 + (float(attacker_stats.get("crit_damage", 50)) + crit_damage_bonus) / 100
    damage += post_mitigation_damage
    final = max(1, int(round(damage)))
    mark = "（暴擊）" if crit else ""
    return final, f"{attacker} 使用 **{skill['name']}** 對 {defender} 造成 **{final}** 傷害{mark}", crit


def _enhance_success_rate(level: int) -> float:
    level = max(0, min(data.ENHANCE_MAX_LEVEL - 1, int(level)))
    if level >= data.ENHANCE_LIMIT_BREAK_LEVEL - 1:
        span = max(1, data.ENHANCE_MAX_LEVEL - data.ENHANCE_LIMIT_BREAK_LEVEL)
        step = (0.05 - 0.02) / span
        return max(0.02, 0.05 - (level - (data.ENHANCE_LIMIT_BREAK_LEVEL - 1)) * step)
    if level >= 10:
        return 0.20
    step = (0.80 - 0.20) / 9
    return 0.80 - level * step


def _profile_embed(user: discord.User | discord.Member, user_data: Dict[str, Any]) -> discord.Embed:
    profile = _ensure_profile(user_data)
    stats = _total_stats(profile)
    needed = _exp_to_next(int(profile["level"]))
    equipped = _equipped_items(profile)

    embed = discord.Embed(
        title=f"⚔️ {user.display_name} 的 RPG 角色",
        color=config.EMBED_COLOR,
    )
    embed.add_field(
        name="等級",
        value=(
            f"Lv. **{profile['level']}**\n"
            f"EXP **{profile['exp']:,}/{needed:,}**\n"
            f"可用點數 **{profile['stat_points']}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="戰績",
        value=(
            f"勝場 **{profile['battles'].get('wins', 0)}**\n"
            f"敗場 **{profile['battles'].get('losses', 0)}**\n"
            f"背包 **{len(profile['inventory'])}/{MAX_INVENTORY}**\n"
            f"鍛造石 **{profile['forge_stones']:,}**"
        ),
        inline=True,
    )
    class_id = profile.get("class_id")
    if class_id in data.CLASSES:
        class_info = data.CLASSES[class_id]
        class_text = f"**{class_info['name']}**（{class_info['rarity']}）\n{class_info['description']}"
    else:
        class_text = "尚未抽取\n使用 `/rpg classroll`"
    embed.add_field(name="職業", value=class_text, inline=True)
    tower_lines = [
        f"{tower['name']}：**{profile['towers'].get(tower_id, 0)}F**"
        for tower_id, tower in data.TOWERS.items()
    ]
    embed.add_field(name="爬塔進度", value="\n".join(tower_lines), inline=False)
    embed.add_field(name="總屬性", value=_format_total_stats(stats), inline=False)

    equip_lines = []
    for slot in data.EQUIPMENT_SLOTS:
        item = equipped.get(slot)
        if item:
            equip_lines.append(f"{data.SLOT_LABELS[slot]}：`{item['id']}` [{item['rarity']}] {item['name']}")
        else:
            equip_lines.append(f"{data.SLOT_LABELS[slot]}：未裝備")
    for idx in range(1, data.ACCESSORY_SLOT_COUNT + 1):
        item = equipped.get(f"accessory{idx}")
        if item:
            equip_lines.append(f"飾品{idx}：`{item['id']}` [{item['rarity']}] {item['name']}")
        else:
            equip_lines.append(f"飾品{idx}：未裝備")
    embed.add_field(name="裝備", value="\n".join(equip_lines), inline=False)

    skill_state = profile["skills"]
    skill_lines = []
    for idx, skill_id in enumerate(skill_state["equipped"], start=1):
        skill = _skill_by_id(skill_id)
        level = _skill_enhance_level(profile, skill_id)
        skill_lines.append(_skill_line(skill, equipped_slot=idx, level=level) if skill else f"{idx}. 未裝備")
    embed.add_field(
        name=f"技能欄 ({len(skill_state['owned'])} 個已擁有)",
        value="\n".join(skill_lines),
        inline=False,
    )

    if profile["materials"]:
        mats = []
        for mat_id, count in sorted(profile["materials"].items()):
            if int(count) > 0:
                mats.append(f"{_material_name(mat_id)} × **{count}**")
        if mats:
            embed.add_field(name="素材", value="\n".join(mats[:8]), inline=False)
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text="/rpg tower 爬塔，/rpg craft accessory 製作飾品，/rpg train 配點，/rpg reset_stats 重置配點")
    return embed


def _effect_percent_text(value: float) -> str:
    if abs(value) < 0.05:
        value = 0.0
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def _effect_stat_label(key: str) -> str:
    icon = getattr(data, "STAT_EMOJIS", {}).get(key, "")
    label = data.STAT_LABELS.get(key, key)
    return f"{icon} {label}".strip()


def _status_field_text(lines: List[str], limit: int = 1024) -> str:
    if not lines:
        return "無"
    output: List[str] = []
    used = 0
    for line in lines:
        added = len(line) + (1 if output else 0)
        if used + added > limit - 24:
            output.append("...還有更多狀態")
            break
        output.append(line)
        used += added
    return "\n".join(output)


def _enemy_stat_text(
    enemy: Dict[str, Any],
    stats: Dict[str, float] | None = None,
    current_hp: int | None = None,
) -> str:
    stats = stats or enemy["stats"]
    hp_text = (
        f"{max(0, int(current_hp))}/{int(enemy['stats']['hp'])}"
        if current_hp is not None
        else str(int(stats["hp"]))
    )
    return (
        f"❤️ HP **{hp_text}** / ⚔️ ATK **{int(stats['atk'])}** / 🛡️ DEF **{int(stats['def'])}**\n"
        f"💨 SPD **{_format_stat_value('spd', stats['spd'])}** / "
        f"🎯 Crit Chance **{_format_stat_value('crit_chance', stats['crit_chance'])}** / "
        f"💥 Crit Damage **{_format_stat_value('crit_damage', stats['crit_damage'])}**"
    )


class TowerBattleView(discord.ui.View):
    def __init__(
        self,
        player_id: int,
        profile: Dict[str, Any],
        enemy: Dict[str, Any],
    ) -> None:
        super().__init__(timeout=180.0)
        self.player_id = player_id
        self.enemy = enemy
        self.class_id = profile.get("class_id")
        self.class_info = data.CLASSES.get(self.class_id, {})
        self.class_battle = dict(self.class_info.get("battle", {}))
        self.player_stats = _total_stats(profile)
        self.player_hp = int(self.player_stats["hp"])
        self.enemy_hp = int(enemy["stats"]["hp"])
        self.skill_slots = list(profile["skills"].get("equipped", []))
        self.skill_slots = (self.skill_slots + [None] * data.SKILL_SLOT_COUNT)[: data.SKILL_SLOT_COUNT]
        raw_skill_levels = profile["skills"].get("levels", {})
        self.skill_levels: Dict[int, int] = {}
        if isinstance(raw_skill_levels, dict):
            for key, value in raw_skill_levels.items():
                try:
                    self.skill_levels[int(key)] = max(0, min(SKILL_ENHANCE_MAX_LEVEL, int(value)))
                except (TypeError, ValueError):
                    continue
        self.skill_cooldowns: Dict[int, int] = {}
        self.enemy_skill_cooldowns: Dict[int, int] = {}
        self.player_buffs: List[Dict[str, Any]] = []
        self.enemy_debuffs: List[Dict[str, Any]] = []
        self.player_shield = 0
        self.next_skill_multiplier = 1.0
        self.next_magic_crit_damage_bonus = 0.0
        self.next_magic_crit_damage_rounds = 0
        self.magic_crit_damage_bonus = 0.0
        self.magic_crit_damage_rounds = 0
        self.magic_damage_buff = 0.0
        self.magic_damage_buff_rounds = 0
        self.enemy_skip_turns = 0
        self.sword_aura = int(self.class_battle.get("starting_sword_aura", 0))
        self.was_damaged_last_round = False
        self.damaged_this_round = False
        self.player_actions_left = 0
        self.logs: List[str] = []
        self.round_no = 0
        self.finished = False
        self.won = False
        if self.sword_aura:
            self.logs.append(f"{self.class_info.get('name', '職業')}效果：開場獲得 **{self.sword_aura}** 層劍氣。")
        self._reset_action_queue()
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("這不是你的戰鬥。", ephemeral=True)
            return False
        return True

    def _player_current_stats(self) -> Dict[str, float]:
        stats = dict(self.player_stats)
        for buff in self.player_buffs:
            for key, multiplier in buff.get("multipliers", {}).items():
                if key in stats:
                    stats[key] *= float(multiplier)
            for key, amount in buff.get("add", {}).items():
                if key in stats:
                    stats[key] += float(amount)
        stats["hp"] = max(1, stats["hp"])
        _apply_crit_chance_overcap(stats)
        return stats

    def _enemy_current_stats(self) -> Dict[str, float]:
        stats = dict(self.enemy["stats"])
        for debuff in self.enemy_debuffs:
            for key, multiplier in debuff.get("multipliers", {}).items():
                if key in stats:
                    stats[key] *= float(multiplier)
        stats["atk"] = max(1.0, float(stats["atk"]))
        stats["def"] = max(0.0, float(stats["def"]))
        stats["spd"] = max(0.0, float(stats["spd"]))
        stats["crit_chance"] = min(100.0, max(0.0, float(stats["crit_chance"])))
        stats["crit_damage"] = max(0.0, float(stats["crit_damage"]))
        return stats

    def _enemy_has_debuff(self) -> bool:
        return any(int(debuff.get("rounds", 0)) > 0 for debuff in self.enemy_debuffs)

    def _player_damage_taken_multiplier(self) -> float:
        multiplier = 1.0
        for buff in self.player_buffs:
            multiplier *= float(buff.get("damage_taken_multiplier", 1.0))
        return max(0.05, multiplier)

    def _has_light_tachi_stance(self) -> bool:
        return any(
            bool(buff.get("light_tachi"))
            and int(buff.get("rounds", 0)) > 0
            and int(buff.get("light_tachi_ticks", 0)) < int(buff.get("light_tachi_release_tick", 3))
            for buff in self.player_buffs
        )

    def _deal_player_effect_damage(
        self,
        *,
        name: str,
        skill_type: str,
        multiplier: float,
        note: str = "",
    ) -> int:
        pseudo_skill = {
            "name": name,
            "type": skill_type,
            "multiplier": multiplier,
            "effect": "",
        }
        context = self._skill_damage_context(pseudo_skill, self._player_current_stats())
        damage, text, _ = _skill_attack_once(
            "你",
            self._player_current_stats(),
            self.enemy["name"],
            self._enemy_current_stats(),
            pseudo_skill,
            damage_multiplier=context["damage_multiplier"],
            flat_bonus=context["flat_bonus"],
            crit_chance_bonus=context["crit_chance_bonus"],
            crit_damage_bonus=context["crit_damage_bonus"],
            post_mitigation_damage=context["post_damage_bonus"],
            multiplier_override=context["multiplier_override"],
        )
        self.next_skill_multiplier = 1.0
        self.enemy_hp = max(0, self.enemy_hp - damage)
        self.logs.append(text)
        return damage

    def _tick_round_effects(self) -> None:
        self.was_damaged_last_round = self.damaged_this_round
        self.damaged_this_round = False
        class_loss_pct = float(self.class_battle.get("round_hp_loss_pct", 0))
        if class_loss_pct > 0:
            loss = max(1, int(round(float(self.player_stats["hp"]) * class_loss_pct)))
            self.player_hp = max(1, self.player_hp - loss)
            self.logs.append(f"{self.class_info.get('name', '職業')}效果使你失去 **{loss}** HP。")

        kept_buffs = []
        for buff in self.player_buffs:
            loss_pct = float(buff.get("hp_loss_pct", 0))
            if loss_pct > 0:
                loss = max(1, int(round(float(self.player_stats["hp"]) * loss_pct)))
                self.player_hp = max(1, self.player_hp - loss)
                self.logs.append(f"{buff['name']} 使你失去 **{loss}** HP。")
            if buff.get("light_tachi"):
                gained = max(0, int(buff.get("light_tachi_aura_per_round", 0)))
                if gained:
                    self.sword_aura += gained
                    self.logs.append(
                        f"{buff['name']} 納刀蓄勢，獲得 **{gained}** 層劍氣，目前 **{self.sword_aura}** 層。"
                    )
                buff["light_tachi_ticks"] = int(buff.get("light_tachi_ticks", 0)) + 1
                if int(buff["light_tachi_ticks"]) >= int(buff.get("light_tachi_release_tick", 3)):
                    base_multiplier = float(buff.get("light_tachi_base_multiplier", 5.0))
                    per_aura_multiplier = float(buff.get("light_tachi_per_aura_multiplier", 1.6))
                    final_multiplier = base_multiplier + per_aura_multiplier * max(0, self.sword_aura)
                    effect_multiplier = float(buff.get("effect_multiplier", 1.0))
                    level_text = (
                        f"，技能+{int(buff.get('skill_level', 0))} 效果×{effect_multiplier:.2f}"
                        if int(buff.get("skill_level", 0)) > 0
                        else ""
                    )
                    self._deal_player_effect_damage(
                        name="光之太刀",
                        skill_type="近戰",
                        multiplier=final_multiplier,
                        note=(
                            f"（納刀釋放，倍率 {final_multiplier:.2f}x，"
                            f"劍氣 {self.sword_aura} 層{level_text}）"
                        ),
                    )
                    buff["light_tachi"] = False
                    buff["rounds"] = 0
                    continue
            buff["rounds"] = int(buff.get("rounds", 0)) - 1
            if buff["rounds"] > 0:
                kept_buffs.append(buff)
        self.player_buffs = kept_buffs

        kept_debuffs = []
        for debuff in self.enemy_debuffs:
            dot_damage = int(debuff.get("dot_damage", 0))
            if dot_damage > 0:
                self.enemy_hp = max(0, self.enemy_hp - dot_damage)
                self.logs.append(f"{debuff['name']} 持續造成 **{dot_damage}** 傷害。")
            debuff["rounds"] = int(debuff.get("rounds", 0)) - 1
            if debuff["rounds"] > 0:
                kept_debuffs.append(debuff)
        self.enemy_debuffs = kept_debuffs

        self.skill_cooldowns = {
            skill_id: max(0, int(turns) - 1)
            for skill_id, turns in self.skill_cooldowns.items()
            if max(0, int(turns) - 1) > 0
        }
        self.enemy_skill_cooldowns = {
            skill_id: max(0, int(turns) - 1)
            for skill_id, turns in self.enemy_skill_cooldowns.items()
            if max(0, int(turns) - 1) > 0
        }
        if self.next_magic_crit_damage_rounds > 0:
            self.next_magic_crit_damage_rounds -= 1
            if self.next_magic_crit_damage_rounds <= 0:
                self.next_magic_crit_damage_bonus = 0.0
        if self.magic_crit_damage_rounds > 0:
            self.magic_crit_damage_rounds -= 1
            if self.magic_crit_damage_rounds <= 0:
                self.magic_crit_damage_bonus = 0.0
        if self.magic_damage_buff_rounds > 0:
            self.magic_damage_buff_rounds -= 1
            if self.magic_damage_buff_rounds <= 0:
                self.magic_damage_buff = 0.0

    def _reset_action_queue(self) -> None:
        self.round_no += 1
        if self.round_no > 1:
            self._tick_round_effects()
            if self.enemy_hp <= 0 or self.player_hp <= 0:
                self.player_actions_left = 0
                return
        player_stats = self._player_current_stats()
        enemy_stats = self._enemy_current_stats()
        player_hits = _attack_count(player_stats, enemy_stats)
        self.player_actions_left = player_hits
        self.logs.append(f"第 {self.round_no} 回合開始。")
        if player_hits == 2:
            self.logs.append("你的 SPD 高於敵人 2 倍，本回合有 1 次追加攻擊。")

    def _sync_buttons(self) -> None:
        player_turn = not self.finished and self.player_actions_left > 0
        is_reward_dungeon = self.enemy.get("battle_type") == "reward_dungeon"
        is_boss_trial = self.enemy.get("battle_type") == "boss_trial"
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            custom_id = child.custom_id or ""
            if custom_id == "rpg_normal_attack":
                child.label = "普通攻擊"
                child.disabled = not player_turn
                child.style = discord.ButtonStyle.danger
            elif custom_id == "rpg_flee":
                child.disabled = self.finished
            elif custom_id == "rpg_next_floor":
                child.disabled = is_reward_dungeon or is_boss_trial or not (self.finished and self.won and self.enemy["floor"] < 50)
            elif custom_id == "rpg_retry_floor":
                child.disabled = is_reward_dungeon or not self.finished
            elif custom_id == "rpg_jump_boss":
                boss_in_progress = self.enemy["is_boss"] and not (self.finished and self.won)
                child.disabled = is_reward_dungeon or is_boss_trial or self.enemy["floor"] >= 50 or boss_in_progress
            elif custom_id.startswith("rpg_skill_"):
                idx = int(custom_id.rsplit("_", 1)[-1])
                skill = _skill_by_id(self.skill_slots[idx])
                if not skill:
                    child.label = f"技能{idx + 1}空"
                    child.disabled = True
                    child.style = discord.ButtonStyle.secondary
                    continue
                cooldown = int(self.skill_cooldowns.get(skill["id"], 0))
                suffix = f"({cooldown})" if cooldown else ""
                level = self._battle_skill_level(skill["id"])
                level_text = f"+{level}" if level else ""
                child.label = f"{idx + 1}.{skill['name'][:6]}{level_text}{suffix}"
                stance_blocks_skill = self._has_light_tachi_stance() and skill["type"] != "Buff"
                child.disabled = (not player_turn) or cooldown > 0 or stance_blocks_skill
                child.style = discord.ButtonStyle.primary if not child.disabled else discord.ButtonStyle.secondary

    def _battle_skill_level(self, skill_id: Any) -> int:
        try:
            return max(0, min(SKILL_ENHANCE_MAX_LEVEL, int(self.skill_levels.get(int(skill_id), 0))))
        except (TypeError, ValueError):
            return 0

    def _apply_skill_enhance_damage(self, damage: int, text: str, skill_level: int) -> tuple[int, str]:
        if skill_level <= 0:
            return damage, text
        multiplier = _skill_enhance_damage_multiplier(skill_level)
        enhanced_damage = max(1, int(round(damage * multiplier)))
        return enhanced_damage, f"{text}（技能+{skill_level} 最終×{multiplier:.2f}）"

    def _simple_log_line(self, line: str) -> str | None:
        if re.match(r"第 \d+ 回合開始。", line):
            return line
        if "無法行動" in line:
            return line
        if line.startswith("你使用 **"):
            return line.split("：", 1)[0].rstrip("。") + "。"
        if "造成 **" in line and "傷害" in line:
            return line
        if line.startswith("勝利") or line.startswith("敗北") or line.startswith("你撤退"):
            return line
        return None

    def _battle_log_text(self) -> str:
        current_round = max(1, int(self.round_no))
        min_round = max(1, current_round - 1)
        visible: List[str] = []
        seen_round = 1
        for line in self.logs:
            match = re.match(r"第 (\d+) 回合開始。", line)
            if match:
                seen_round = int(match.group(1))
            if seen_round < min_round:
                continue
            simple = self._simple_log_line(line)
            if simple:
                visible.append(simple)
        return "\n".join(visible[-10:])[:1024] or "尚無行動紀錄"

    def _status_parts_from_modifiers(self, status: Dict[str, Any]) -> List[str]:
        parts: List[str] = []
        for key, multiplier in status.get("multipliers", {}).items():
            pct = (float(multiplier) - 1.0) * 100
            if abs(pct) >= 0.05:
                parts.append(f"{_effect_stat_label(key)} {_effect_percent_text(pct)}")
        for key, amount in status.get("add", {}).items():
            value = float(amount)
            if abs(value) < 0.05:
                continue
            if key in {"crit_chance", "crit_damage", "def_pen", "debuffed_damage"}:
                parts.append(f"{_effect_stat_label(key)} {_effect_percent_text(value)}")
            else:
                sign = "+" if value > 0 else ""
                parts.append(f"{_effect_stat_label(key)} {sign}{_format_stat_value(key, value)}")
        return parts

    def _player_status_lines(self) -> List[str]:
        lines: List[str] = []
        for buff in self.player_buffs:
            if int(buff.get("rounds", 0)) <= 0:
                continue
            parts = self._status_parts_from_modifiers(buff)
            damage_taken = float(buff.get("damage_taken_multiplier", 1.0))
            if abs(damage_taken - 1.0) >= 0.0005:
                parts.append(f"受到傷害 {_effect_percent_text((damage_taken - 1.0) * 100)}")
            hp_loss_pct = float(buff.get("hp_loss_pct", 0.0))
            if hp_loss_pct > 0:
                parts.append(f"每回合失去最大 HP {hp_loss_pct * 100:.1f}%")
            if buff.get("light_tachi"):
                base = float(buff.get("light_tachi_base_multiplier", 5.0))
                per_aura = float(buff.get("light_tachi_per_aura_multiplier", 1.6))
                current_multiplier = base + per_aura * max(0, self.sword_aura)
                parts.append(
                    "納刀："
                    f"每回合劍氣 +{int(buff.get('light_tachi_aura_per_round', 0))}，"
                    f"蓄勢 {int(buff.get('light_tachi_ticks', 0))}/{int(buff.get('light_tachi_release_tick', 3))}，"
                    f"釋放 {base * 100:.0f}% + {per_aura * 100:.0f}%×劍氣"
                    f"（目前 {current_multiplier:.2f}x）"
                )
            if not parts:
                parts.append("持續效果")
            lines.append(f"• {buff['name']}（{int(buff.get('rounds', 0))} 回合）：{'；'.join(parts)}")

        class_loss_pct = float(self.class_battle.get("round_hp_loss_pct", 0))
        if class_loss_pct > 0:
            lines.append(
                f"• {self.class_info.get('name', '職業')}（職業）：每回合失去最大 HP {class_loss_pct * 100:.1f}%"
            )
        if self.next_skill_multiplier > 1.0:
            lines.append(f"• 下一次技能：技能傷害 {_effect_percent_text((self.next_skill_multiplier - 1.0) * 100)}")
        if self.next_magic_crit_damage_rounds > 0:
            lines.append(
                "• 下一次魔法暴擊"
                f"（{self.next_magic_crit_damage_rounds} 回合）："
                f"暴擊傷害 {_effect_percent_text(self.next_magic_crit_damage_bonus)}"
            )
        if self.magic_crit_damage_rounds > 0:
            lines.append(
                f"• 魔法暴擊強化（{self.magic_crit_damage_rounds} 回合）："
                f"魔法暴擊傷害 {_effect_percent_text(self.magic_crit_damage_bonus)}"
            )
        if self.magic_damage_buff_rounds > 0:
            lines.append(
                f"• 魔法傷害強化（{self.magic_damage_buff_rounds} 回合）："
                f"魔法技能傷害 {_effect_percent_text(self.magic_damage_buff * 100)}"
            )
        return lines

    def _enemy_status_lines(self) -> List[str]:
        lines: List[str] = []
        if self.enemy_skip_turns > 0:
            lines.append(f"• 控制（{self.enemy_skip_turns} 次行動）：無法行動")
        for debuff in self.enemy_debuffs:
            if int(debuff.get("rounds", 0)) <= 0:
                continue
            parts = self._status_parts_from_modifiers(debuff)
            dot_damage = int(debuff.get("dot_damage", 0))
            if dot_damage > 0:
                parts.append(f"每回合傷害 {dot_damage}")
            tag = str(debuff.get("tag") or "")
            stacks = int(debuff.get("stacks", 1))
            if tag == "shadow_mark":
                parts.append(f"影痕 {stacks} 層：受到傷害 +{stacks * 8:.1f}%")
            elif tag == "magic_mark":
                parts.append("魔紋刻印：受到魔法暴擊傷害 +45.0%")
            elif tag == "giant_mark":
                parts.append("巨獸標記：最大生命值流技能強化")
            elif tag and dot_damage <= 0:
                stack_text = f" {stacks} 層" if stacks > 1 else ""
                parts.append(f"{tag}{stack_text}")
            if not parts:
                parts.append("持續效果")
            lines.append(f"• {debuff['name']}（{int(debuff.get('rounds', 0))} 回合）：{'；'.join(parts)}")
        return lines

    def _embed(self) -> discord.Embed:
        is_reward_dungeon = self.enemy.get("battle_type") == "reward_dungeon"
        is_boss_trial = self.enemy.get("battle_type") == "boss_trial"
        if is_reward_dungeon:
            dungeon = data.REWARD_DUNGEONS[self.enemy["reward_difficulty"]]
            title = self.enemy["dungeon_name"]
            color = int(dungeon["color"])
        elif is_boss_trial:
            title = f"Boss戰：{self.enemy['boss_style']}"
            color = int(self.enemy["color"])
        else:
            tower = data.TOWERS[self.enemy["tower_id"]]
            title = f"{tower['name']} {self.enemy['floor']}F"
            if self.enemy["is_boss"]:
                title += " Boss"
            color = tower["color"]
        embed = discord.Embed(
            title=f"⚔️ {title}：{self.enemy['name']}",
            color=color,
        )
        player_stats = self._player_current_stats()
        enemy_stats = self._enemy_current_stats()
        shield_text = f"\n護盾：**{self.player_shield}**" if self.player_shield > 0 else ""
        aura_text = f"\n劍氣：**{self.sword_aura}**" if self.sword_aura > 0 else ""
        embed.description = (
            f"你的 HP：**{max(0, self.player_hp)}/{int(self.player_stats['hp'])}**{shield_text}{aura_text}\n"
            f"敵人 HP：**{max(0, self.enemy_hp)}/{int(self.enemy['stats']['hp'])}**"
        )
        embed.add_field(name="敵人數值", value=_enemy_stat_text(self.enemy, enemy_stats, self.enemy_hp), inline=False)
        embed.add_field(
            name="你的數值",
            value=(
                f"❤️ HP **{max(0, self.player_hp)}/{int(self.player_stats['hp'])}** / "
                f"⚔️ ATK **{int(player_stats['atk'])}** / 🛡️ DEF **{int(player_stats['def'])}** / "
                f"💨 SPD **{_format_stat_value('spd', player_stats['spd'])}**\n"
                f"🗡️ 近戰 **{_format_stat_value('melee_damage', player_stats['melee_damage'])}** / "
                f"🏹 遠程 **{_format_stat_value('ranged_damage', player_stats['ranged_damage'])}** / "
                f"🔮 魔法 **{_format_stat_value('magic_damage', player_stats['magic_damage'])}**\n"
                f"🎯 Crit Chance **{_format_stat_value('crit_chance', player_stats['crit_chance'])}** / "
                f"💥 Crit Damage **{_format_stat_value('crit_damage', player_stats['crit_damage'])}**\n"
                f"🪓 Def Pen **{_format_stat_value('def_pen', player_stats['def_pen'])}** / "
                f"🧪 Debuffed Damage **{_format_stat_value('debuffed_damage', player_stats['debuffed_damage'])}**"
            ),
            inline=False,
        )
        embed.add_field(name="你的 Buff / Debuff", value=_status_field_text(self._player_status_lines()), inline=False)
        embed.add_field(name="敵人 Buff / Debuff", value=_status_field_text(self._enemy_status_lines()), inline=False)
        embed.add_field(name="回合紀錄", value=self._battle_log_text(), inline=False)
        if not self.finished:
            embed.set_footer(text=f"本回合剩餘行動：{self.player_actions_left}。敵人會在你行動結束後自動行動。")
        self._sync_buttons()
        return embed

    def _apply_self_hp_cost(self, skill: Dict[str, Any]) -> None:
        pct = _parse_percent(skill.get("self_hp_cost"))
        if pct <= 0:
            return
        cost = max(1, int(round(float(self.player_stats["hp"]) * pct)))
        self.player_hp = max(1, self.player_hp - cost)
        self.logs.append(f"{skill['name']} 消耗你 **{cost}** HP。")

    def _consume_sword_aura(self, skill: Dict[str, Any]) -> int:
        aura = str(skill.get("sword_aura") or "")
        if aura == "全消耗":
            consumed = self.sword_aura
            self.sword_aura = 0
            return consumed
        match = re.fullmatch(r"-(\d+)", aura)
        if not match:
            return 0
        requested = int(match.group(1))
        consumed = min(requested, self.sword_aura)
        self.sword_aura -= consumed
        return consumed

    def _gain_sword_aura(self, skill: Dict[str, Any]) -> None:
        aura = str(skill.get("sword_aura") or "")
        match = re.fullmatch(r"\+(\d+)", aura)
        if not match:
            return
        gained = int(match.group(1))
        self.sword_aura += gained
        self.logs.append(f"獲得 **{gained}** 層劍氣，目前 **{self.sword_aura}** 層。")

    def _max_hp_extra_damage(self, skill: Dict[str, Any], player_stats: Dict[str, float]) -> int:
        effect = str(skill.get("effect") or "")
        match = re.search(r"目標最大 HP\s*(\d+(?:\.\d+)?)%", effect)
        if not match:
            return 0
        hp_pct = float(match.group(1)) / 100
        if "巨獸標記" in effect and self._enemy_has_tag("giant_mark"):
            replace_match = re.search(r"改為\s*(\d+(?:\.\d+)?)%", effect)
            if replace_match:
                hp_pct = float(replace_match.group(1)) / 100
        extra = float(self.enemy["stats"]["hp"]) * hp_pct
        cap_match = re.search(r"上限\s*=\s*\(ATK\+RATK\)×(\d+(?:\.\d+)?)%", effect)
        if cap_match and self.enemy["is_boss"]:
            cap_base = float(player_stats["atk"]) + float(player_stats.get("ranged_damage", 0))
            extra = min(extra, cap_base * float(cap_match.group(1)) / 100)
        return max(0, int(round(extra)))

    def _enemy_has_tag(self, tag: str) -> bool:
        return any(debuff.get("tag") == tag and int(debuff.get("rounds", 0)) > 0 for debuff in self.enemy_debuffs)

    def _enemy_tag_stacks(self, tag: str) -> int:
        total = 0
        for debuff in self.enemy_debuffs:
            if debuff.get("tag") == tag and int(debuff.get("rounds", 0)) > 0:
                total += int(debuff.get("stacks", 1))
        return total

    def _add_enemy_tag(self, *, name: str, tag: str, stacks: int = 1, rounds: int = 4) -> None:
        for debuff in self.enemy_debuffs:
            if debuff.get("tag") == tag:
                debuff["stacks"] = int(debuff.get("stacks", 1)) + int(stacks)
                debuff["rounds"] = max(int(debuff.get("rounds", 0)), int(rounds))
                break
        else:
            self.enemy_debuffs.append(
                {
                    "name": name,
                    "rounds": int(rounds),
                    "multipliers": {},
                    "tag": tag,
                    "stacks": int(stacks),
                }
            )
        if tag == "shadow_mark":
            total = self._enemy_tag_stacks(tag)
            self.logs.append(f"{self.enemy['name']} 被附加 **{stacks}** 層影痕，目前 **{total}** 層。")

    def _clear_enemy_tag(self, tag: str) -> None:
        self.enemy_debuffs = [
            debuff for debuff in self.enemy_debuffs if debuff.get("tag") != tag
        ]

    def _shadow_damage_multiplier(self) -> float:
        return 1.0 + self._enemy_tag_stacks("shadow_mark") * 0.08

    def _skill_damage_context(
        self,
        skill: Dict[str, Any],
        player_stats: Dict[str, float],
    ) -> Dict[str, float]:
        effect = str(skill.get("effect") or "")
        flat_bonus = 0.0
        damage_multiplier = self.next_skill_multiplier
        crit_chance_bonus = 0.0
        crit_damage_bonus = 0.0
        multiplier_override: float | None = None
        post_damage_bonus = 0.0

        def_match = re.search(r"DEF×(\d+(?:\.\d+)?)%\s*加入本次傷害", effect)
        if def_match:
            flat_bonus += float(player_stats["def"]) * float(def_match.group(1)) / 100

        crit_match = re.search(r"本次暴擊率\s*\+(\d+(?:\.\d+)?)%", effect)
        if crit_match:
            crit_chance_bonus += float(crit_match.group(1))
        if skill.get("type") == "魔法" and self.next_magic_crit_damage_rounds > 0:
            crit_damage_bonus += self.next_magic_crit_damage_bonus
        if skill.get("type") == "魔法" and self.magic_crit_damage_rounds > 0:
            crit_damage_bonus += self.magic_crit_damage_bonus
        if skill.get("type") == "魔法" and self._enemy_has_tag("magic_mark"):
            crit_damage_bonus += 45.0
        if skill.get("type") == "魔法" and self.magic_damage_buff_rounds > 0:
            damage_multiplier *= 1.0 + self.magic_damage_buff
        if "本次暴擊傷害" in effect:
            crit_damage_match = re.search(r"本次暴擊傷害\s*\+(\d+(?:\.\d+)?)%", effect)
            if crit_damage_match:
                crit_damage_bonus += float(crit_damage_match.group(1))

        conditional_multiplier = 0.0
        if re.search(r"若(?:自己|自身)(?:已)?有護盾", effect) and self.player_shield > 0:
            match = re.search(r"額外\s*\+(\d+(?:\.\d+)?)%\s*倍率", effect)
            if match:
                conditional_multiplier += float(match.group(1)) / 100
        if "上一回合受到傷害" in effect and self.was_damaged_last_round:
            match = re.search(r"額外\s*\+(\d+(?:\.\d+)?)%", effect)
            if match:
                conditional_multiplier += float(match.group(1)) / 100
        if "自身 DEF 高於 ATK" in effect and float(player_stats["def"]) > float(player_stats["atk"]):
            match = re.search(r"額外\s*\+(\d+(?:\.\d+)?)%\s*倍率", effect)
            if match:
                conditional_multiplier += float(match.group(1)) / 100
        if self._enemy_has_debuff() and float(player_stats.get("debuffed_damage", 0)) > 0:
            conditional_multiplier += float(player_stats["debuffed_damage"]) / 100
        shadow_stacks = self._enemy_tag_stacks("shadow_mark")
        if shadow_stacks:
            conditional_multiplier += shadow_stacks * 0.08
        if "魔紋刻印" in effect and self._enemy_has_tag("magic_mark"):
            total_match = re.search(r"額外\s*\+(\d+(?:\.\d+)?)%\s*總傷害", effect)
            if total_match:
                conditional_multiplier += float(total_match.group(1)) / 100

        if "SPD×0.4%" in effect:
            base = float(player_stats["atk"])
            extra_stat = data.SKILL_TYPE_DAMAGE_STAT.get(str(skill.get("type", "")))
            if extra_stat:
                base += float(player_stats.get(extra_stat, 0))
            post_damage_bonus += base * float(player_stats.get("spd", 0)) * 0.004

        if shadow_stacks and "消耗" in effect and "影痕" in effect:
            per_stack = 0.60 if "每層額外 +60%" in effect else 0.45
            conditional_multiplier += shadow_stacks * per_stack
            self._clear_enemy_tag("shadow_mark")
            self.logs.append(f"{skill['name']} 消耗 **{shadow_stacks}** 層影痕。")

        low_hp_range_match = re.search(
            r"HP\s*低於\s*(\d+(?:\.\d+)?)%\s*時，?\s*技能倍率變為\s*(\d+(?:\.\d+)?)\s*(?:到|~|-)\s*(\d+(?:\.\d+)?)\s*倍",
            effect,
        )
        if low_hp_range_match:
            threshold = float(low_hp_range_match.group(1)) / 100
            max_hp = max(1.0, float(self.player_stats["hp"]))
            hp_ratio = max(0.0, float(self.player_hp)) / max_hp
            if hp_ratio < threshold:
                low = float(low_hp_range_match.group(2))
                high = float(low_hp_range_match.group(3))
                rolled_multiplier = random.uniform(min(low, high), max(low, high))
                damage_multiplier *= rolled_multiplier
                self.logs.append(
                    f"{skill['name']} 低血效果觸發，本次技能倍率變為 **{rolled_multiplier:.2f}** 倍。"
                )

        if "先獲得1層劍氣後" in effect:
            self.sword_aura += 1
            self.logs.append(f"{skill['name']} 先獲得 **1** 層劍氣，目前 **{self.sword_aura}** 層。")
        consumed_aura = self._consume_sword_aura(skill)
        base_multiplier = float(skill.get("multiplier") or 1.0)
        if consumed_aura and "175%×消耗劍氣" in effect and base_multiplier > 0:
            damage_multiplier *= (base_multiplier + consumed_aura * 1.75) / base_multiplier
            self.logs.append(f"消耗 **{consumed_aura}** 層劍氣。")

        if conditional_multiplier:
            damage_multiplier *= 1.0 + conditional_multiplier

        return {
            "damage_multiplier": damage_multiplier,
            "flat_bonus": flat_bonus,
            "crit_chance_bonus": crit_chance_bonus,
            "crit_damage_bonus": crit_damage_bonus,
            "multiplier_override": multiplier_override,
            "post_damage_bonus": post_damage_bonus,
        }

    def _settle_dot_debuffs(self) -> None:
        total = 0
        kept = []
        for debuff in self.enemy_debuffs:
            dot_damage = int(debuff.get("dot_damage", 0))
            if dot_damage > 0:
                total += dot_damage * max(1, int(debuff.get("rounds", 1)))
                continue
            kept.append(debuff)
        if total > 0:
            self.enemy_hp = max(0, self.enemy_hp - total)
            self.enemy_debuffs = kept
            self.logs.append(f"混沌星核立即結算持續傷害，共 **{total}** 傷害。")

    def _apply_damage_skill_aftereffects(
        self,
        skill: Dict[str, Any],
        damage: int,
        player_stats: Dict[str, float],
    ) -> None:
        effect = str(skill.get("effect") or "")

        shield_pct = 0.0
        shield_matches = list(re.finditer(r"(?:本次傷害的|獲得本次傷害)\s*(\d+(?:\.\d+)?)%.*?護盾", effect))
        if shield_matches:
            requires_existing_shield = re.search(r"若(?:自己|自身)(?:已)?有護盾，額外獲得", effect) is not None
            if not requires_existing_shield or self.player_shield > 0:
                shield_pct = max(float(match.group(1)) for match in shield_matches) / 100
        if shield_pct > 0:
            shield = max(1, int(round(damage * shield_pct)))
            self.player_shield += shield
            self.logs.append(f"{skill['name']} 讓你獲得 **{shield}** 護盾。")

        self_def_match = re.search(r"自身 DEF\s*\+(\d+(?:\.\d+)?)%", effect)
        if self_def_match:
            self.player_buffs.append(
                {
                    "name": skill["name"],
                    "rounds": _effect_duration(effect),
                    "multipliers": {"def": 1.0 + float(self_def_match.group(1)) / 100},
                    "add": {},
                }
            )
            self.logs.append(f"你的 DEF 因 {skill['name']} 提升。")

        reduction_match = re.search(r"自身 DEF 每有 100 點，額外獲得 1% 傷害減免，最高 20%", effect)
        if reduction_match:
            reduction = min(0.20, math.floor(float(player_stats["def"]) / 100) * 0.01)
            if reduction > 0:
                self.player_buffs.append(
                    {
                        "name": skill["name"],
                        "rounds": _effect_duration(effect),
                        "multipliers": {},
                        "add": {},
                        "damage_taken_multiplier": 1.0 - reduction,
                    }
                )
                self.logs.append(f"{skill['name']} 提供 **{reduction * 100:.0f}%** 傷害減免。")

        if "刷新自身 DEF 增益 1 回合" in effect:
            for buff in self.player_buffs:
                if float(buff.get("multipliers", {}).get("def", 1.0)) > 1.0:
                    buff["rounds"] = int(buff.get("rounds", 0)) + 1

        if "冷卻-1" in effect or "冷卻 -1" in effect:
            self.skill_cooldowns = {
                skill_id: max(0, turns - 1)
                for skill_id, turns in self.skill_cooldowns.items()
                if max(0, turns - 1) > 0
            }
            self.logs.append(f"{skill['name']} 使所有技能冷卻 -1。")

        next_magic_crit = re.search(r"下一次魔法暴擊傷害\s*\+(\d+(?:\.\d+)?)%", effect)
        if next_magic_crit:
            self.next_magic_crit_damage_bonus = float(next_magic_crit.group(1))
            self.next_magic_crit_damage_rounds = _effect_duration(effect)
            self.logs.append(f"下一次魔法暴擊傷害 +{self.next_magic_crit_damage_bonus:g}%。")

        self_magic_crit = re.search(r"自身魔法暴擊傷害\s*\+(\d+(?:\.\d+)?)%", effect)
        if self_magic_crit:
            self.magic_crit_damage_bonus = float(self_magic_crit.group(1))
            self.magic_crit_damage_rounds = _effect_duration(effect)
            self.logs.append(f"自身魔法暴擊傷害 +{self.magic_crit_damage_bonus:g}%。")

    def _apply_player_buff(self, skill: Dict[str, Any], skill_level: int = 0) -> None:
        effect = str(skill.get("effect") or "")
        duration = _effect_duration(effect)
        effect_multiplier = _skill_enhance_effect_multiplier(skill_level)
        multipliers: Dict[str, float] = {}
        add: Dict[str, float] = {}
        for labels, pct in _effect_stat_groups(effect):
            scaled_pct = _scale_positive_percent(pct, effect_multiplier)
            for label in labels:
                key = {
                    "ATK": "atk",
                    "DEF": "def",
                    "SPD": "spd",
                    "MATK": "magic_damage",
                    "RATK": "ranged_damage",
                }.get(label)
                if key:
                    multipliers[key] = multipliers.get(key, 1.0) * (1.0 + scaled_pct)
                elif label == "暴擊率":
                    add["crit_chance"] = add.get("crit_chance", 0.0) + scaled_pct * 100
                elif label == "暴擊傷害":
                    add["crit_damage"] = add.get("crit_damage", 0.0) + scaled_pct * 100

        shield_match = re.search(r"最大 HP\s*(\d+(?:\.\d+)?)%\s*的護盾", effect)
        if shield_match:
            shield_pct = float(shield_match.group(1)) / 100
            shield = int(round(float(self.player_stats["hp"]) * shield_pct * effect_multiplier))
            self.player_shield += shield
            self.logs.append(f"{skill['name']} 產生 **{shield}** 護盾。")

        sword_aura_match = re.search(r"立刻獲得\s*(\d+)\s*層劍氣", effect)
        if sword_aura_match:
            gained = _scale_positive_int(int(sword_aura_match.group(1)), effect_multiplier)
            self.sword_aura += gained
            self.logs.append(f"{skill['name']} 讓你獲得 **{gained}** 層劍氣，目前 **{self.sword_aura}** 層。")

        next_skill_match = re.search(r"下一(?:個|次).*?技能傷害\s*\+(\d+(?:\.\d+)?)%", effect)
        if next_skill_match:
            bonus = float(next_skill_match.group(1)) / 100 * effect_multiplier
            self.next_skill_multiplier = max(
                self.next_skill_multiplier,
                1.0 + bonus,
            )

        cooldown_match = re.search(r"冷卻\s*-(\d+)", effect)
        if cooldown_match:
            reduction = _scale_positive_int(int(cooldown_match.group(1)), effect_multiplier)
            self.skill_cooldowns = {
                skill_id: max(0, turns - reduction)
                for skill_id, turns in self.skill_cooldowns.items()
                if max(0, turns - reduction) > 0
            }

        hp_loss_match = re.search(r"每回合失去最大 HP\s*(\d+(?:\.\d+)?)%", effect)
        buff: Dict[str, Any] = {
            "name": skill["name"],
            "rounds": duration,
            "multipliers": multipliers,
            "add": add,
            "skill_level": skill_level,
            "effect_multiplier": effect_multiplier,
        }
        if hp_loss_match:
            buff["hp_loss_pct"] = float(hp_loss_match.group(1)) / 100

        damage_taken_match = re.search(r"受到傷害\s*([+-])(\d+(?:\.\d+)?)%", effect)
        if damage_taken_match:
            sign = damage_taken_match.group(1)
            pct = float(damage_taken_match.group(2)) / 100
            if sign == "-":
                reduction = min(0.95, pct * effect_multiplier)
                if reduction > 0:
                    buff["damage_taken_multiplier"] = min(
                        float(buff.get("damage_taken_multiplier", 1.0)),
                        1.0 - reduction,
                    )
            else:
                buff["damage_taken_multiplier"] = max(
                    float(buff.get("damage_taken_multiplier", 1.0)),
                    1.0 + pct,
                )

        if "納刀狀態" in effect or skill.get("name") == "光之太刀":
            aura_match = re.search(r"每回合獲得\s*(\d+)\s*層劍氣", effect)
            base_match = re.search(
                r"造成\s*(\d+(?:\.\d+)?)%\s*\+\s*(\d+(?:\.\d+)?)%\s*[×x*]\s*目前劍氣",
                effect,
            )
            release_match = re.search(r"第(?:三|3)回合", effect)
            buff["light_tachi"] = True
            buff["light_tachi_ticks"] = 0
            buff["light_tachi_release_tick"] = 3 if release_match else max(1, duration)
            buff["light_tachi_aura_per_round"] = _scale_positive_int(
                int(aura_match.group(1)) if aura_match else 5,
                effect_multiplier,
            )
            buff["light_tachi_base_multiplier"] = (
                (float(base_match.group(1)) / 100) * effect_multiplier if base_match else 5.0 * effect_multiplier
            )
            buff["light_tachi_per_aura_multiplier"] = (
                (float(base_match.group(2)) / 100) * effect_multiplier if base_match else 1.6 * effect_multiplier
            )

        if (
            multipliers
            or add
            or buff.get("hp_loss_pct")
            or bool(buff.get("light_tachi"))
            or float(buff.get("damage_taken_multiplier", 1.0)) != 1.0
        ):
            self.player_buffs.append(buff)
        enhance_text = f"（強化+{skill_level} 效果×{effect_multiplier:.2f}）" if skill_level > 0 else ""
        self.logs.append(f"你使用 **{skill['name']}**{enhance_text}。")

    def _apply_enemy_effects(self, skill: Dict[str, Any], player_stats: Dict[str, float], skill_level: int = 0) -> None:
        effect = str(skill.get("effect") or "")
        duration = _effect_duration(effect)
        multipliers: Dict[str, float] = {}
        for match in re.finditer(r"敵人\s*(ATK|DEF|SPD)\s*([+-]\d+(?:\.\d+)?)%", effect):
            key = {"ATK": "atk", "DEF": "def", "SPD": "spd"}[match.group(1)]
            pct = float(match.group(2)) / 100
            multipliers[key] = multipliers.get(key, 1.0) * (1.0 + pct)
        if multipliers:
            self.enemy_debuffs.append(
                {"name": skill["name"], "rounds": duration, "multipliers": multipliers}
            )
            self.logs.append(f"{self.enemy['name']} 受到 {skill['name']} 的弱化（{duration} 回合）。")
        dot_match = re.search(
            r"(灼燒|燃燒|中毒|黑火).*?每回合\s*[（(]?ATK\+MATK[）)]?\s*[×x*]\s*(\d+(?:\.\d+)?)%",
            effect,
        )
        if dot_match:
            dot_type = dot_match.group(1)
            dot_pct = float(dot_match.group(2)) / 100
            dot_base = float(player_stats["atk"]) + float(player_stats.get("magic_damage", 0))
            dot_damage = max(1, int(round(dot_base * dot_pct * _skill_enhance_damage_multiplier(skill_level))))
            self.enemy_debuffs.append(
                {
                    "name": dot_type,
                    "rounds": duration,
                    "multipliers": {},
                    "dot_damage": dot_damage,
                    "tag": dot_type,
                }
            )
            self.logs.append(f"{self.enemy['name']} 被附加 {dot_type}（每回合 {dot_damage} 傷害，{duration} 回合）。")
        if "巨獸標記" in effect:
            self.enemy_debuffs.append(
                {
                    "name": "巨獸標記",
                    "rounds": duration,
                    "multipliers": {},
                    "tag": "giant_mark",
                }
            )
            self.logs.append(f"{self.enemy['name']} 被標上巨獸標記（{duration} 回合）。")
        if "附加魔紋刻印" in effect:
            self._add_enemy_tag(name="魔紋刻印", tag="magic_mark", stacks=1, rounds=duration)
            self.logs.append(f"{self.enemy['name']} 被附加魔紋刻印（{duration} 回合）。")
        if skill.get("school") == "刺殺流":
            stacks = 2 if "給敵人 2 層影痕" in effect else 1
            self._add_enemy_tag(name="影痕", tag="shadow_mark", stacks=stacks, rounds=4)
        if "下一回合不能行動" in effect:
            self.enemy_skip_turns = max(self.enemy_skip_turns, 1)
        if "立即結算所有傷害" in effect and "移除debuff" in effect:
            self._settle_dot_debuffs()

    def _heal_from_skill_effect(self, skill: Dict[str, Any], damage: int) -> None:
        effect = str(skill.get("effect") or "")
        match = re.search(r"回復.*?(\d+(?:\.\d+)?)%\s*HP", effect)
        if not match:
            return
        heal = int(round(damage * float(match.group(1)) / 100))
        if heal <= 0:
            return
        max_hp = int(self.player_stats["hp"])
        self.player_hp = min(max_hp, self.player_hp + heal)
        self.logs.append(f"{skill['name']} 回復你 **{heal}** HP。")

    def _choose_enemy_skill(self) -> Optional[Dict[str, Any]]:
        skills = list(self.enemy.get("skills") or [])
        available = [
            skill for skill in skills
            if int(self.enemy_skill_cooldowns.get(int(skill["id"]), 0)) <= 0
        ]
        return random.choice(available) if available else None

    def _apply_enemy_skill_cost(self, skill: Dict[str, Any]) -> None:
        pct = _parse_percent(skill.get("self_hp_cost"))
        if pct <= 0:
            return
        cost = max(1, int(round(float(self.enemy["stats"]["hp"]) * pct)))
        self.enemy_hp = max(1, self.enemy_hp - cost)
        self.logs.append(f"{self.enemy['name']} 使用 {skill['name']} 失去 **{cost}** HP。")

    def _enemy_skill_max_hp_extra_damage(self, skill: Dict[str, Any]) -> int:
        effect = str(skill.get("effect") or "")
        match = re.search(r"目標最大 HP\s*(\d+(?:\.\d+)?)%", effect)
        if not match:
            return 0
        extra = float(self.player_stats["hp"]) * float(match.group(1)) / 100
        return max(1, int(round(extra)))

    def _enemy_skill_attack(self, skill: Dict[str, Any]) -> None:
        self._apply_enemy_skill_cost(skill)
        self.enemy_skill_cooldowns[int(skill["id"])] = int(skill.get("cooldown") or 0)
        extra = self._enemy_skill_max_hp_extra_damage(skill)
        damage, text, _ = _skill_attack_once(
            self.enemy["name"],
            self._enemy_current_stats(),
            "你",
            self._player_current_stats(),
            skill,
            post_mitigation_damage=extra,
        )
        damage = max(1, int(round(damage * self._player_damage_taken_multiplier())))
        if self.player_shield > 0:
            blocked = min(self.player_shield, damage)
            self.player_shield -= blocked
            damage -= blocked
            text += f"（護盾吸收 {blocked}）"
        self.player_hp -= damage
        if damage > 0:
            self.damaged_this_round = True
        self.logs.append(text)
        if extra:
            self.logs.append(f"{skill['name']} 追加最大生命值傷害 **{extra}**。")

    def _enemy_auto_actions(self) -> None:
        if self.finished or self.enemy_hp <= 0 or self.player_hp <= 0:
            return
        enemy_stats = self._enemy_current_stats()
        player_stats = self._player_current_stats()
        if self.enemy_skip_turns > 0:
            self.enemy_skip_turns -= 1
            self.logs.append(f"{self.enemy['name']} 受到控制，本回合無法行動。")
            self._reset_action_queue()
            return
        enemy_hits = _attack_count(enemy_stats, player_stats)
        if enemy_hits == 2:
            self.logs.append(f"{self.enemy['name']} 的 SPD 高於你 2 倍，本回合會追加攻擊。")
        for _ in range(enemy_hits):
            if self.enemy.get("battle_type") == "boss_trial":
                skill = self._choose_enemy_skill()
                if skill:
                    self._enemy_skill_attack(skill)
                    if self.player_hp <= 0:
                        return
                    continue
            damage, text = _attack_once(self.enemy["name"], enemy_stats, "你", self._player_current_stats())
            mark = "（暴擊）" if "（暴擊）" in text else ""
            damage = max(1, int(round(damage * self._player_damage_taken_multiplier())))
            if self.player_shield > 0:
                blocked = min(self.player_shield, damage)
                self.player_shield -= blocked
                damage -= blocked
                mark += f"（護盾吸收 {blocked}）"
            self.player_hp -= damage
            if damage > 0:
                self.damaged_this_round = True
            self.logs.append(f"{self.enemy['name']} 對 你 造成 **{damage}** 傷害{mark}")
            if self.player_hp <= 0:
                return
        self._reset_action_queue()

    async def _resolve_action(
        self,
        interaction: discord.Interaction,
        *,
        skill_slot: int | None = None,
    ) -> None:
        if self.finished:
            await interaction.response.defer()
            return

        if self.player_actions_left <= 0:
            self._reset_action_queue()
            if self.enemy_hp <= 0:
                await self._finish(interaction, True)
                return
            if self.player_hp <= 0:
                await self._finish(interaction, False)
                return
        if self.player_actions_left <= 0:
            await interaction.response.send_message("現在無法行動。", ephemeral=True)
            return

        enemy_stats = self._enemy_current_stats()
        player_stats = self._player_current_stats()

        if skill_slot is None:
            damage, text = _attack_once("你", player_stats, self.enemy["name"], enemy_stats)
            shadow_multiplier = self._shadow_damage_multiplier()
            if shadow_multiplier > 1:
                damage = max(1, int(round(damage * shadow_multiplier)))
                text += f"（影痕增傷 {shadow_multiplier:.2f}x）"
            self.enemy_hp -= damage
            self.logs.append(text)
        else:
            skill = _skill_by_id(self.skill_slots[skill_slot])
            if not skill:
                await interaction.response.send_message("這格沒有裝備技能。", ephemeral=True)
                return
            cooldown = int(self.skill_cooldowns.get(skill["id"], 0))
            if cooldown > 0:
                await interaction.response.send_message(f"技能冷卻中，還有 {cooldown} 回合。", ephemeral=True)
                return
            if skill["type"] != "Buff" and self._has_light_tachi_stance():
                await interaction.response.send_message("納刀狀態期間不能使用傷害技能，可以使用 Buff。", ephemeral=True)
                return
            skill_level = self._battle_skill_level(skill["id"])
            self._apply_self_hp_cost(skill)
            self.skill_cooldowns[skill["id"]] = int(skill.get("cooldown") or 0)
            if skill["type"] == "Buff":
                self._apply_player_buff(skill, skill_level)
            else:
                context = self._skill_damage_context(skill, self._player_current_stats())
                post_damage = self._max_hp_extra_damage(skill, self._player_current_stats())
                damage, text, crit = _skill_attack_once(
                    "你",
                    self._player_current_stats(),
                    self.enemy["name"],
                    enemy_stats,
                    skill,
                    damage_multiplier=context["damage_multiplier"],
                    flat_bonus=context["flat_bonus"],
                    crit_chance_bonus=context["crit_chance_bonus"],
                    crit_damage_bonus=context["crit_damage_bonus"],
                    post_mitigation_damage=post_damage + context["post_damage_bonus"],
                    multiplier_override=context["multiplier_override"],
                )
                damage, text = self._apply_skill_enhance_damage(damage, text, skill_level)
                self.next_skill_multiplier = 1.0
                self.enemy_hp -= damage
                self.logs.append(text)
                if crit and "額外造成 210% 倍率可暴擊傷害" in str(skill.get("effect", "")):
                    extra_damage, extra_text, _ = _skill_attack_once(
                        "你",
                        self._player_current_stats(),
                        self.enemy["name"],
                        enemy_stats,
                        skill,
                        crit_damage_bonus=context["crit_damage_bonus"],
                        multiplier_override=2.10,
                    )
                    extra_damage, extra_text = self._apply_skill_enhance_damage(extra_damage, extra_text, skill_level)
                    self.enemy_hp -= extra_damage
                    self.logs.append(extra_text)
                if crit and "額外造成一次可暴擊的410%倍率傷害" in str(skill.get("effect", "")):
                    if "技能傷害增加40%" in str(skill.get("effect", "")):
                        bonus_damage = max(1, int(round(damage * 0.40)))
                        self.enemy_hp -= bonus_damage
                        self.logs.append(f"{skill['name']} 暴擊後技能傷害增加 **{bonus_damage}**。")
                    self.magic_damage_buff = max(self.magic_damage_buff, 0.40)
                    self.magic_damage_buff_rounds = max(self.magic_damage_buff_rounds, 3)
                    extra_damage, extra_text, _ = _skill_attack_once(
                        "你",
                        self._player_current_stats(),
                        self.enemy["name"],
                        enemy_stats,
                        skill,
                        crit_damage_bonus=context["crit_damage_bonus"],
                        multiplier_override=4.10,
                    )
                    extra_damage, extra_text = self._apply_skill_enhance_damage(extra_damage, extra_text, skill_level)
                    self.enemy_hp -= extra_damage
                    self.logs.append(extra_text)
                if post_damage:
                    self.logs.append(f"{skill['name']} 追加最大生命值傷害 **{post_damage}**。")
                if (
                    skill.get("type") == "魔法"
                    and self.next_magic_crit_damage_rounds > 0
                    and "下一次魔法暴擊傷害" not in str(skill.get("effect", ""))
                ):
                    self.next_magic_crit_damage_bonus = 0.0
                    self.next_magic_crit_damage_rounds = 0
                self._heal_from_skill_effect(skill, damage)
                self._apply_damage_skill_aftereffects(skill, damage, self._player_current_stats())
                self._gain_sword_aura(skill)
                self._apply_enemy_effects(skill, self._player_current_stats(), skill_level)

        self.player_actions_left = max(0, self.player_actions_left - 1)
        if self.enemy_hp <= 0:
            await self._finish(interaction, True)
            return
        if self.player_actions_left <= 0:
            self._enemy_auto_actions()
            if self.player_hp <= 0:
                await self._finish(interaction, False)
                return
            if self.enemy_hp <= 0:
                await self._finish(interaction, True)
                return

        await interaction.response.edit_message(embed=self._embed(), view=self)

    async def _finish(self, interaction: discord.Interaction, won: bool) -> None:
        self.finished = True
        self.won = won
        is_reward_dungeon = self.enemy.get("battle_type") == "reward_dungeon"
        is_boss_trial = self.enemy.get("battle_type") == "boss_trial"

        def mutate(user: Dict[str, Any]) -> Dict[str, Any]:
            profile = _ensure_profile(user)
            result = {
                "won": won,
                "exp": 0,
                "coins": 0,
                "levels": [],
                "material": None,
                "balance": int(user.get("balance", 0)),
                "highest": int(profile["towers"].get(self.enemy.get("tower_id", ""), 0)),
                "battle_type": self.enemy.get("battle_type", "tower"),
                "reward_type": self.enemy.get("reward_type"),
            }
            if won:
                profile["battles"]["wins"] = int(profile["battles"].get("wins", 0)) + 1
                if is_reward_dungeon:
                    reward_type = str(self.enemy["reward_type"])
                    reward_amount = int(self.enemy["reward_amount"])
                    if reward_type == "coins":
                        result["coins"] = reward_amount
                        user["balance"] = int(user.get("balance", 0)) + reward_amount
                    else:
                        result["exp"] = reward_amount
                        result["levels"] = _add_exp(profile, reward_amount)
                elif not is_boss_trial:
                    profile["towers"][self.enemy["tower_id"]] = max(
                        int(profile["towers"].get(self.enemy["tower_id"], 0)),
                        int(self.enemy["floor"]),
                    )
                if (not is_reward_dungeon) and (not is_boss_trial) and self.enemy["is_boss"]:
                    if random.random() < data.BOSS_MATERIAL_DROP_RATE:
                        rarity = str(self.enemy["material_rarity"])
                        mat_id = _material_id(self.enemy["tower_id"], int(self.enemy["floor"]), rarity)
                        profile["materials"][mat_id] = int(profile["materials"].get(mat_id, 0)) + 1
                        result["material"] = mat_id
                elif is_boss_trial:
                    if random.random() < BOSS_TRIAL_MATERIAL_DROP_RATE:
                        mat_id = _boss_trial_material_id(str(self.enemy["boss_id"]))
                        profile["materials"][mat_id] = int(profile["materials"].get(mat_id, 0)) + 1
                        result["material"] = mat_id
                elif (not is_reward_dungeon) and (not is_boss_trial):
                    result["exp"], result["coins"] = _tower_floor_rewards(
                        str(self.enemy["tower_id"]),
                        int(self.enemy["floor"]),
                    )
                    result["levels"] = _add_exp(profile, result["exp"])
                    user["balance"] = int(user.get("balance", 0)) + result["coins"]
            else:
                profile["battles"]["losses"] = int(profile["battles"].get("losses", 0)) + 1
            result["balance"] = int(user.get("balance", 0))
            result["highest"] = int(profile["towers"].get(self.enemy.get("tower_id", ""), 0))
            return result

        result = await db.mutate_user(interaction.user.id, mutate)
        if won:
            if is_reward_dungeon:
                reward_label = _reward_type_label(str(result["reward_type"]))
                reward = f"{reward_label}副本獎勵："
                if result["coins"]:
                    reward += f"金錢 +**{result['coins']:,}**"
                else:
                    reward += f"EXP +**{result['exp']:,}**"
            elif is_boss_trial:
                reward = f"Boss 戰勝利！素材掉落率 {BOSS_TRIAL_MATERIAL_DROP_RATE * 100:g}%。"
                if result["material"]:
                    reward += f"\n獲得素材：**{_material_name(result['material'])}**"
                else:
                    reward += "\n這次沒有掉落 Boss 戰素材。"
            else:
                reward = f"Boss 不給金錢與經驗，素材掉落率 {data.BOSS_MATERIAL_DROP_RATE * 100:g}%。"
            if (not is_reward_dungeon) and (not is_boss_trial) and result["material"]:
                reward += f"\n獲得素材：**{_material_name(result['material'])}**"
            elif (not is_reward_dungeon) and (not is_boss_trial) and self.enemy["is_boss"]:
                reward += "\n這次沒有掉落素材。"
            elif (not is_reward_dungeon) and (not is_boss_trial) and (result["exp"] or result["coins"]):
                reward = f"EXP +**{result['exp']}**，金錢 +**{result['coins']}**"
            if result["levels"]:
                reward += f"\n升級到 **Lv.{result['levels'][-1]}**，獲得 **{len(result['levels']) * LEVEL_POINTS}** 點"
            self.logs.append("勝利！" + reward.replace("\n", " "))
        else:
            self.logs.append("敗北，沒有獲得獎勵。")

        self._sync_buttons()
        embed = self._embed()
        embed.color = config.WIN_COLOR if won else config.LOSE_COLOR
        if is_reward_dungeon:
            progress_text = "獎勵副本不影響爬塔進度"
        elif is_boss_trial:
            progress_text = "Boss 戰不影響爬塔進度"
        else:
            progress_text = f"目前進度：**{result['highest']}F**"
        embed.add_field(
            name="結算",
            value=(
                f"結果：**{'勝利' if won else '敗北'}**\n"
                f"{progress_text}\n"
                f"目前餘額：**{result['balance']:,}**"
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _start_floor(self, interaction: discord.Interaction, floor: int) -> None:
        user_data = await db.get_user_data(self.player_id)
        profile = _ensure_profile(user_data)
        self.enemy = _enemy_for_floor(self.enemy["tower_id"], floor)
        self.class_id = profile.get("class_id")
        self.class_info = data.CLASSES.get(self.class_id, {})
        self.class_battle = dict(self.class_info.get("battle", {}))
        self.player_stats = _total_stats(profile)
        self.player_hp = int(self.player_stats["hp"])
        self.enemy_hp = int(self.enemy["stats"]["hp"])
        self.skill_slots = list(profile["skills"].get("equipped", []))
        self.skill_slots = (self.skill_slots + [None] * data.SKILL_SLOT_COUNT)[: data.SKILL_SLOT_COUNT]
        self.skill_cooldowns = {}
        self.enemy_skill_cooldowns = {}
        self.player_buffs = []
        self.enemy_debuffs = []
        self.player_shield = 0
        self.next_skill_multiplier = 1.0
        self.next_magic_crit_damage_bonus = 0.0
        self.next_magic_crit_damage_rounds = 0
        self.magic_crit_damage_bonus = 0.0
        self.magic_crit_damage_rounds = 0
        self.magic_damage_buff = 0.0
        self.magic_damage_buff_rounds = 0
        self.enemy_skip_turns = 0
        self.sword_aura = int(self.class_battle.get("starting_sword_aura", 0))
        self.was_damaged_last_round = False
        self.damaged_this_round = False
        self.player_actions_left = 0
        self.logs = []
        self.round_no = 0
        self.finished = False
        self.won = False
        if self.sword_aura:
            self.logs.append(f"{self.class_info.get('name', '職業')}效果：開場獲得 **{self.sword_aura}** 層劍氣。")
        self._reset_action_queue()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    async def _start_boss_trial(self, interaction: discord.Interaction) -> None:
        user_data = await db.get_user_data(self.player_id)
        profile = _ensure_profile(user_data)
        self.enemy = _enemy_for_boss_trial(str(self.enemy["boss_id"]))
        self.class_id = profile.get("class_id")
        self.class_info = data.CLASSES.get(self.class_id, {})
        self.class_battle = dict(self.class_info.get("battle", {}))
        self.player_stats = _total_stats(profile)
        self.player_hp = int(self.player_stats["hp"])
        self.enemy_hp = int(self.enemy["stats"]["hp"])
        self.skill_slots = list(profile["skills"].get("equipped", []))
        self.skill_slots = (self.skill_slots + [None] * data.SKILL_SLOT_COUNT)[: data.SKILL_SLOT_COUNT]
        self.skill_cooldowns = {}
        self.enemy_skill_cooldowns = {}
        self.player_buffs = []
        self.enemy_debuffs = []
        self.player_shield = 0
        self.next_skill_multiplier = 1.0
        self.next_magic_crit_damage_bonus = 0.0
        self.next_magic_crit_damage_rounds = 0
        self.magic_crit_damage_bonus = 0.0
        self.magic_crit_damage_rounds = 0
        self.magic_damage_buff = 0.0
        self.magic_damage_buff_rounds = 0
        self.enemy_skip_turns = 0
        self.sword_aura = int(self.class_battle.get("starting_sword_aura", 0))
        self.was_damaged_last_round = False
        self.damaged_this_round = False
        self.player_actions_left = 0
        self.logs = []
        self.round_no = 0
        self.finished = False
        self.won = False
        if self.sword_aura:
            self.logs.append(f"{self.class_info.get('name', '職業')}開場：獲得 **{self.sword_aura}** 層劍氣。")
        self._reset_action_queue()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="普通攻擊", style=discord.ButtonStyle.danger, row=0, custom_id="rpg_normal_attack")
    async def step_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._resolve_action(interaction)

    async def _use_skill_button(self, interaction: discord.Interaction, slot: int) -> None:
        await self._resolve_action(interaction, skill_slot=slot)

    @discord.ui.button(label="技能1", style=discord.ButtonStyle.primary, row=2, custom_id="rpg_skill_0")
    async def skill_button_1(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._use_skill_button(interaction, 0)

    @discord.ui.button(label="技能2", style=discord.ButtonStyle.primary, row=2, custom_id="rpg_skill_1")
    async def skill_button_2(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._use_skill_button(interaction, 1)

    @discord.ui.button(label="技能3", style=discord.ButtonStyle.primary, row=2, custom_id="rpg_skill_2")
    async def skill_button_3(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._use_skill_button(interaction, 2)

    @discord.ui.button(label="技能4", style=discord.ButtonStyle.primary, row=3, custom_id="rpg_skill_3")
    async def skill_button_4(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._use_skill_button(interaction, 3)

    @discord.ui.button(label="技能5", style=discord.ButtonStyle.primary, row=3, custom_id="rpg_skill_4")
    async def skill_button_5(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._use_skill_button(interaction, 4)

    @discord.ui.button(label="技能6", style=discord.ButtonStyle.primary, row=3, custom_id="rpg_skill_5")
    async def skill_button_6(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._use_skill_button(interaction, 5)

    @discord.ui.button(label="逃跑", style=discord.ButtonStyle.secondary, row=0, custom_id="rpg_flee")
    async def flee_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.finished:
            await interaction.response.defer()
            return
        self.logs.append("你撤退了。")
        await self._finish(interaction, False)

    @discord.ui.button(label="下一層", style=discord.ButtonStyle.primary, row=1, custom_id="rpg_next_floor")
    async def next_floor_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not (self.finished and self.won):
            await interaction.response.send_message("請先結束目前戰鬥。", ephemeral=True)
            return
        floor = int(self.enemy["floor"]) + 1
        if floor > 50:
            await interaction.response.send_message("這座塔已經到 50F，可用 `/rpg tower floor:1` 重新挑戰。", ephemeral=True)
            return
        await self._start_floor(interaction, floor)

    @discord.ui.button(label="重打本層", style=discord.ButtonStyle.secondary, row=1, custom_id="rpg_retry_floor")
    async def retry_floor_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not self.finished:
            await interaction.response.send_message("請先結束目前戰鬥，再重打本層。", ephemeral=True)
            return
        if self.enemy.get("battle_type") == "boss_trial":
            await self._start_boss_trial(interaction)
            return
        await self._start_floor(interaction, int(self.enemy["floor"]))

    @discord.ui.button(label="跳到下個Boss", style=discord.ButtonStyle.success, row=1, custom_id="rpg_jump_boss")
    async def jump_boss_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        current_floor = int(self.enemy["floor"])
        if self.enemy["is_boss"] and not (self.finished and self.won):
            await interaction.response.send_message("請先擊敗目前 Boss，才能跳到下一個 Boss。", ephemeral=True)
            return
        floor = min(((current_floor // 10) + 1) * 10, 50)
        if floor <= current_floor:
            await interaction.response.send_message("已經沒有下一個 Boss。", ephemeral=True)
            return
        await self._start_floor(interaction, floor)

    async def on_timeout(self) -> None:
        self.finished = True
        for child in self.children:
            child.disabled = True


class InventoryCleanupView(discord.ui.View):
    def __init__(self, player_id: int, rarity: str) -> None:
        super().__init__(timeout=120.0)
        self.player_id = player_id
        self.rarity = rarity

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("這不是你的背包操作。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="分解此稀有度", style=discord.ButtonStyle.danger)
    async def confirm_delete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        def mutate(user: Dict[str, Any]) -> tuple[int, int]:
            profile = _ensure_profile(user)
            equipped_ids = {
                item_id
                for item_id in [
                    profile["equipped"].get("weapon"),
                    profile["equipped"].get("armor"),
                    *profile["equipped"].get("accessories", []),
                ]
                if item_id
            }
            kept = []
            removed = 0
            for item in profile["inventory"]:
                if item.get("rarity") == self.rarity and item.get("id") not in equipped_ids:
                    removed += 1
                    continue
                kept.append(item)
            profile["inventory"] = kept
            stones = removed * data.FORGE_STONES_BY_RARITY[self.rarity]
            profile["forge_stones"] = int(profile.get("forge_stones", 0)) + stones
            return removed, stones

        removed, stones = await db.mutate_user(interaction.user.id, mutate)
        for child in self.children:
            child.disabled = True
        embed = discord.Embed(
            title="♻️ 裝備分解完成",
            description=(
                f"分解稀有度：**{self.rarity}**\n"
                f"分解數量：**{removed}**\n"
                f"獲得鍛造石：**{stones:,}**"
            ),
            color=config.WIN_COLOR,
        )
        embed.set_footer(text="已裝備中的物品會保留，不會被一鍵分解。")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="已取消分解。", embed=None, view=self)


def _sort_skills(skills: List[Dict[str, Any]], sort_by: str) -> List[Dict[str, Any]]:
    if sort_by == "id_asc":
        return sorted(skills, key=lambda skill: int(skill["id"]))
    if sort_by == "id_desc":
        return sorted(skills, key=lambda skill: int(skill["id"]), reverse=True)
    if sort_by == "school":
        return sorted(
            skills,
            key=lambda skill: (str(skill["school"]), -_skill_rarity_index(skill["rarity"]), int(skill["id"])),
        )
    return sorted(
        skills,
        key=lambda skill: (_skill_rarity_index(skill["rarity"]), int(skill["id"])),
        reverse=True,
    )


def _skills_embed(
    profile: Dict[str, Any],
    page: int,
    sort_by: str = "rarity_desc",
    school_filter: str = "all",
) -> tuple[discord.Embed, int, int]:
    equipped_lines = []
    for idx, skill_id in enumerate(profile["skills"]["equipped"], start=1):
        skill = _skill_by_id(skill_id)
        level = _skill_enhance_level(profile, skill_id)
        equipped_lines.append(_skill_line(skill, equipped_slot=idx, level=level) if skill else f"{idx}. 未裝備")

    owned = [
        skill
        for skill_id in profile["skills"]["owned"]
        if (skill := _skill_by_id(skill_id)) is not None
    ]
    if school_filter != "all":
        owned = [skill for skill in owned if skill["school"] == school_filter]
    owned = _sort_skills(owned, sort_by)
    total_pages = max(1, math.ceil(len(owned) / SKILL_ITEMS_PER_PAGE))
    current = min(max(1, int(page)), total_pages)
    start = (current - 1) * SKILL_ITEMS_PER_PAGE
    visible = owned[start : start + SKILL_ITEMS_PER_PAGE]

    embed = discord.Embed(title="📖 RPG 技能", color=config.EMBED_COLOR)
    embed.add_field(name="已裝備技能欄", value="\n".join(equipped_lines), inline=False)
    if visible:
        filter_text = f"；流派：{school_filter}" if school_filter != "all" else ""
        sort_text = {
            "rarity_desc": "稀有度",
            "id_asc": "ID 小到大",
            "id_desc": "ID 大到小",
            "school": "流派",
        }.get(sort_by, "稀有度")
        embed.description = f"**已擁有技能 ({len(owned)} 個；排序：{sort_text}{filter_text})**\n" + "\n\n".join(
            _skill_detail_line(skill, level=_skill_enhance_level(profile, skill["id"])) for skill in visible
        )[:3800]
    else:
        embed.description = "目前沒有符合條件的技能。用 `/rpg skillgacha` 抽技能，或換一個流派篩選。"
    embed.set_footer(text=f"第 {current}/{total_pages} 頁；/rpg skillequip skill_id slot，skill_id 輸入 0 可清空該格")
    return embed, total_pages, current


def _all_skills_embed(
    page: int,
    sort_by: str = "rarity_desc",
    school_filter: str = "all",
    rarity_filter: str = "all",
) -> tuple[discord.Embed, int, int]:
    skills = list(rpg_skills.SKILLS)
    if school_filter != "all":
        skills = [skill for skill in skills if skill["school"] == school_filter]
    if rarity_filter != "all":
        skills = [skill for skill in skills if skill["rarity"] == rarity_filter]
    skills = _sort_skills(skills, sort_by)
    total_pages = max(1, math.ceil(len(skills) / SKILL_ITEMS_PER_PAGE))
    current = min(max(1, int(page)), total_pages)
    start = (current - 1) * SKILL_ITEMS_PER_PAGE
    visible = skills[start : start + SKILL_ITEMS_PER_PAGE]

    sort_text = {
        "rarity_desc": "稀有度",
        "id_asc": "ID 小到大",
        "id_desc": "ID 大到小",
        "school": "流派",
    }.get(sort_by, "稀有度")
    filters = []
    if school_filter != "all":
        filters.append(f"流派：{school_filter}")
    if rarity_filter != "all":
        filters.append(f"稀有度：{rarity_filter}")
    filter_text = f"；{'；'.join(filters)}" if filters else ""

    embed = discord.Embed(
        title="📚 RPG 全技能列表",
        color=config.EMBED_COLOR,
        description=(
            f"**全部技能 ({len(skills)} 個；排序：{sort_text}{filter_text})**\n"
            + ("\n\n".join(_skill_detail_line(skill) for skill in visible)[:3800] if visible else "沒有符合條件的技能。")
        ),
    )
    embed.set_footer(text=f"第 {current}/{total_pages} 頁；秘密技能不會從 /rpg skillgacha 抽出，可用 /rpg craft skill 製作或強化")
    return embed, total_pages, current


def _inventory_embed(inventory: List[Dict[str, Any]], page: int) -> tuple[discord.Embed, int, int]:
    total_pages = max(1, math.ceil(len(inventory) / ITEMS_PER_PAGE))
    current = min(max(1, int(page)), total_pages)
    start = (current - 1) * ITEMS_PER_PAGE
    items = inventory[start : start + ITEMS_PER_PAGE]

    embed = discord.Embed(
        title=f"🎒 RPG 背包 ({len(inventory)}/{MAX_INVENTORY})",
        color=config.EMBED_COLOR,
    )
    if items:
        embed.description = "\n\n".join(_item_line(item) for item in items)
    else:
        embed.description = "背包是空的。用 `/rpg gacha` 抽武器或盔甲。"
    embed.set_footer(text=f"第 {current}/{total_pages} 頁")
    return embed, total_pages, current


class SkillPageView(discord.ui.View):
    def __init__(
        self,
        player_id: int,
        profile: Dict[str, Any],
        page: int,
        sort_by: str = "rarity_desc",
        school_filter: str = "all",
    ) -> None:
        super().__init__(timeout=180.0)
        self.player_id = player_id
        self.profile = profile
        self.page = int(page)
        self.sort_by = sort_by
        self.school_filter = school_filter
        self.total_pages = 1
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("這不是你的技能列表。", ephemeral=True)
            return False
        return True

    def build_embed(self) -> discord.Embed:
        embed, total_pages, current = _skills_embed(self.profile, self.page, self.sort_by, self.school_filter)
        self.total_pages = total_pages
        self.page = current
        self._sync_buttons()
        return embed

    def _sync_buttons(self) -> None:
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            if child.custom_id == "rpg_skills_prev":
                child.disabled = self.page <= 1
            elif child.custom_id == "rpg_skills_next":
                child.disabled = self.page >= self.total_pages

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary, custom_id="rpg_skills_prev")
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.page = max(1, self.page - 1)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.primary, custom_id="rpg_skills_next")
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.page = min(self.total_pages, self.page + 1)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


class AllSkillPageView(discord.ui.View):
    def __init__(
        self,
        player_id: int,
        page: int,
        sort_by: str = "rarity_desc",
        school_filter: str = "all",
        rarity_filter: str = "all",
    ) -> None:
        super().__init__(timeout=180.0)
        self.player_id = player_id
        self.page = int(page)
        self.sort_by = sort_by
        self.school_filter = school_filter
        self.rarity_filter = rarity_filter
        self.total_pages = 1
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("這不是你的技能列表。", ephemeral=True)
            return False
        return True

    def build_embed(self) -> discord.Embed:
        embed, total_pages, current = _all_skills_embed(
            self.page,
            self.sort_by,
            self.school_filter,
            self.rarity_filter,
        )
        self.total_pages = total_pages
        self.page = current
        self._sync_buttons()
        return embed

    def _sync_buttons(self) -> None:
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            if child.custom_id == "rpg_all_skills_prev":
                child.disabled = self.page <= 1
            elif child.custom_id == "rpg_all_skills_next":
                child.disabled = self.page >= self.total_pages

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary, custom_id="rpg_all_skills_prev")
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.page = max(1, self.page - 1)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.primary, custom_id="rpg_all_skills_next")
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.page = min(self.total_pages, self.page + 1)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


class InventoryPageView(discord.ui.View):
    def __init__(self, player_id: int, inventory: List[Dict[str, Any]], page: int) -> None:
        super().__init__(timeout=180.0)
        self.player_id = player_id
        self.inventory = inventory
        self.page = int(page)
        self.total_pages = 1
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player_id:
            await interaction.response.send_message("這不是你的背包列表。", ephemeral=True)
            return False
        return True

    def build_embed(self) -> discord.Embed:
        embed, total_pages, current = _inventory_embed(self.inventory, self.page)
        self.total_pages = total_pages
        self.page = current
        self._sync_buttons()
        return embed

    def _sync_buttons(self) -> None:
        for child in self.children:
            if not isinstance(child, discord.ui.Button):
                continue
            if child.custom_id == "rpg_inventory_prev":
                child.disabled = self.page <= 1
            elif child.custom_id == "rpg_inventory_next":
                child.disabled = self.page >= self.total_pages

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary, custom_id="rpg_inventory_prev")
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.page = max(1, self.page - 1)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.primary, custom_id="rpg_inventory_next")
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.page = min(self.total_pages, self.page + 1)
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True


class RPG(commands.GroupCog, name="rpg"):
    skill = app_commands.Group(name="skill", description="RPG 技能列表")
    craft = app_commands.Group(name="craft", description="RPG 製作")
    enhance = app_commands.Group(name="enhance", description="RPG 裝備強化")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="profile", description="查看 RPG 角色與裝備")
    @app_commands.describe(user="要查看的玩家")
    async def profile(
        self,
        interaction: discord.Interaction,
        user: discord.User | None = None,
    ) -> None:
        target = user or interaction.user
        user_data = await db.get_user_data(target.id)
        embed = _profile_embed(target, user_data)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="classroll", description="抽取或重抽 RPG 職業")
    async def classroll(self, interaction: discord.Interaction) -> None:
        def mutate(user: Dict[str, Any]) -> tuple[bool, str, int, str, bool]:
            profile = _ensure_profile(user)
            had_class = profile.get("class_id") in data.CLASSES
            cost = data.CLASS_REROLL_COST if had_class else 0
            if int(user.get("balance", 0)) < cost:
                return False, f"餘額不足，重抽職業需要 {cost:,}。", int(user.get("balance", 0)), "", had_class
            if cost:
                user["balance"] = int(user["balance"]) - cost
            class_id = random.choice(list(data.CLASSES))
            profile["class_id"] = class_id
            return True, "", int(user.get("balance", 0)), class_id, had_class

        ok, reason, balance, class_id, had_class = await db.mutate_user(interaction.user.id, mutate)
        if not ok:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        class_info = data.CLASSES[class_id]
        cost_text = f"花費 **{data.CLASS_REROLL_COST:,}**，" if had_class else "首次抽取免費，"
        embed = discord.Embed(
            title="🧭 職業抽取結果",
            description=(
                f"{cost_text}餘額 **{balance:,}**\n"
                f"職業：**{class_info['name']}**（{class_info['rarity']}）\n"
                f"效果：{class_info['description']}"
            ),
            color=config.WIN_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="train", description="使用升級點數提升屬性")
    @app_commands.describe(stat="要提升的屬性", points="投入點數")
    @app_commands.choices(
        stat=[
            app_commands.Choice(name="HP (+1% 總值)", value="hp"),
            app_commands.Choice(name="ATK (+1% 總值)", value="atk"),
            app_commands.Choice(name="DEF (+1% 總值)", value="def"),
            app_commands.Choice(name="SPD (+0.8)", value="spd"),
            app_commands.Choice(name="Crit Chance (+1%)", value="crit_chance"),
            app_commands.Choice(name="Crit Damage (+1.8%)", value="crit_damage"),
        ]
    )
    async def train(
        self,
        interaction: discord.Interaction,
        stat: app_commands.Choice[str],
        points: app_commands.Range[int, 1, 999],
    ) -> None:
        def mutate(user: Dict[str, Any]) -> tuple[bool, str, Dict[str, Any]]:
            profile = _ensure_profile(user)
            if int(profile["stat_points"]) < int(points):
                return False, "可用點數不足。", profile
            profile["stat_points"] = int(profile["stat_points"]) - int(points)
            profile["allocated"][stat.value] = int(profile["allocated"].get(stat.value, 0)) + int(points)
            return True, "", profile

        ok, reason, profile = await db.mutate_user(interaction.user.id, mutate)
        if not ok:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        if stat.value in PERCENT_STAT_POINT_KEYS:
            gain_text = f"+{int(points)}% 總值"
        else:
            gained = data.POINT_GAINS[stat.value] * int(points)
            gain_text = _format_stat_value(stat.value, gained)
        embed = discord.Embed(
            title="✅ 配點完成",
            description=(
                f"{_stat_label(stat.value)} 增加 "
                f"**{gain_text}**\n"
                f"剩餘點數：**{profile['stat_points']}**"
            ),
            color=config.WIN_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="reset_stats", description="重製 RPG 屬性配點並返還所有點數")
    async def reset_stats(self, interaction: discord.Interaction) -> None:
        def mutate(user: Dict[str, Any]) -> tuple[int, int]:
            profile = _ensure_profile(user)
            refunded = sum(int(profile["allocated"].get(key, 0)) for key in data.POINT_GAINS)
            profile["stat_points"] = int(profile.get("stat_points", 0)) + refunded
            profile["allocated"] = {key: 0 for key in data.POINT_GAINS}
            return refunded, int(profile["stat_points"])

        refunded, stat_points = await db.mutate_user(interaction.user.id, mutate)
        embed = discord.Embed(
            title="🔄 屬性點已重製",
            description=(
                f"返還點數：**{refunded}**\n"
                f"目前可用點數：**{stat_points}**"
            ),
            color=config.WIN_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="gacha", description="每 100 元抽一件武器或盔甲")
    @app_commands.describe(slot="抽取類型", count="抽取次數", auto_dismantle="自動分解此稀有度以下的本次抽裝")
    @app_commands.choices(
        slot=[
            app_commands.Choice(name="武器", value="weapon"),
            app_commands.Choice(name="盔甲", value="armor"),
            app_commands.Choice(name="武器/盔甲隨機", value="random"),
        ],
        count=[
            app_commands.Choice(name="單抽", value=1),
            app_commands.Choice(name="十抽", value=10),
            app_commands.Choice(name="百抽", value=100),
        ],
        auto_dismantle=AUTO_DISMANTLE_CHOICES,
    )
    async def gacha(
        self,
        interaction: discord.Interaction,
        slot: app_commands.Choice[str],
        count: app_commands.Choice[int],
        auto_dismantle: app_commands.Choice[str] | None = None,
    ) -> None:
        draw_count = int(count.value)
        cost = draw_count * GACHA_COST
        auto_threshold = auto_dismantle.value if auto_dismantle else "none"

        def mutate(
            user: Dict[str, Any],
        ) -> tuple[bool, str, int, List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], int]:
            profile = _ensure_profile(user)
            if int(user.get("balance", 0)) < cost:
                return False, "餘額不足。", int(user.get("balance", 0)), [], [], [], 0
            items = []
            for _ in range(draw_count):
                item_slot = random.choice(data.EQUIPMENT_SLOTS) if slot.value == "random" else slot.value
                item = _generate_item(item_slot)
                items.append(item)
            kept_items, dismantled_items, stones = _auto_dismantle_split(items, auto_threshold)
            if len(profile["inventory"]) + len(kept_items) > MAX_INVENTORY:
                free_slots = MAX_INVENTORY - len(profile["inventory"])
                return (
                    False,
                    f"背包空間不足，這次會保留 {len(kept_items)} 件裝備，目前只剩 {free_slots} 格。可提高自動分解稀有度。",
                    int(user.get("balance", 0)),
                    [],
                    [],
                    [],
                    0,
                )
            user["balance"] = int(user["balance"]) - cost
            profile["inventory"].extend(kept_items)
            if stones:
                profile["forge_stones"] = int(profile.get("forge_stones", 0)) + stones
            return True, "", int(user["balance"]), items, kept_items, dismantled_items, stones

        ok, reason, balance, items, kept_items, dismantled_items, stones = await db.mutate_user(interaction.user.id, mutate)
        if not ok:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        counts = {rarity: 0 for rarity in data.RARITY_RATES}
        for item in items:
            counts[item["rarity"]] += 1
        best_items = sorted(
            kept_items,
            key=lambda item: (_rarity_index(item["rarity"]), item["score"]),
            reverse=True,
        )[:12]
        summary = " ".join(f"{rarity}:{amount}" for rarity, amount in counts.items() if amount)
        embed = discord.Embed(
            title=f"🎁 RPG 抽裝結果：{draw_count} 抽",
            description=(
                f"花費 **{cost:,}**，餘額 **{balance:,}**\n"
                f"機率：N 60 / R 30 / SR 6.105 / SSR 2.605 / 3SR 1 / UR 0.25 / Secret 0.04\n"
                f"結果統計：{summary}\n"
                f"保留裝備：**{len(kept_items)}** 件"
            ),
            color=max((data.RARITY_COLORS[item["rarity"]] for item in items), default=config.EMBED_COLOR),
        )
        embed.add_field(
            name="保留裝備亮點",
            value=("\n\n".join(_item_line(item) for item in best_items)[:1024] if best_items else "這次沒有保留裝備。"),
            inline=False,
        )
        if dismantled_items:
            embed.add_field(
                name="自動分解",
                value=(
                    f"門檻：**{auto_threshold} 以下**\n"
                    f"分解統計：{_equipment_rarity_summary(dismantled_items)}\n"
                    f"獲得鍛造石：**{stones:,}**"
                ),
                inline=False,
            )
        embed.set_footer(text="用 /rpg inventory 查看背包；用 /rpg equip item_id 裝備")
        await interaction.response.send_message(embed=embed)

    @skill.command(name="list", description="查看所有 RPG 技能效果")
    @app_commands.describe(page="頁數", sort="排序方式", school="篩選流派，輸入全部或完整流派名", rarity="篩選稀有度")
    @app_commands.choices(sort=SKILL_SORT_CHOICES, rarity=SKILL_RARITY_CHOICES)
    async def skill_list(
        self,
        interaction: discord.Interaction,
        page: app_commands.Range[int, 1, 99] = 1,
        sort: app_commands.Choice[str] | None = None,
        school: str | None = None,
        rarity: app_commands.Choice[str] | None = None,
    ) -> None:
        view = AllSkillPageView(
            interaction.user.id,
            int(page),
            sort.value if sort else "rarity_desc",
            school if school and school != "全部" else "all",
            rarity.value if rarity else "all",
        )
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    @craft.command(name="skill", description="消耗 Boss 戰素材製作或強化秘密終極技能")
    @app_commands.describe(skill_id="要製作的秘密終極技能")
    @app_commands.choices(skill_id=ULTIMATE_SKILL_CHOICES)
    async def craft_skill(
        self,
        interaction: discord.Interaction,
        skill_id: app_commands.Choice[int],
    ) -> None:
        chosen_id = int(skill_id.value)
        skill = _skill_by_id(chosen_id)
        if not skill or not skill.get("craft_only"):
            await interaction.response.send_message("這個技能不是可製作的秘密終極技能。", ephemeral=True)
            return

        def mutate(user: Dict[str, Any]) -> tuple[bool, str, List[tuple[str, int]], int, Optional[Dict[str, Any]]]:
            profile = _ensure_profile(user)
            if chosen_id in profile["skills"]["owned"] and _skill_enhance_level(profile, chosen_id) >= SKILL_ENHANCE_MAX_LEVEL:
                return False, f"這個技能已經強化到 +{SKILL_ENHANCE_MAX_LEVEL}。", [], _boss_trial_material_total(profile), None
            total_materials = _boss_trial_material_total(profile)
            if total_materials < ULTIMATE_SKILL_CRAFT_COST:
                return (
                    False,
                    f"Boss 戰素材不足，需要 {ULTIMATE_SKILL_CRAFT_COST} 個，目前只有 {total_materials} 個。",
                    [],
                    total_materials,
                    None,
                )
            consumed = _consume_boss_trial_materials(profile, ULTIMATE_SKILL_CRAFT_COST)
            result = _grant_or_enhance_skill(profile, skill)
            return True, "", consumed, _boss_trial_material_total(profile), result

        ok, reason, consumed, remaining, result = await db.mutate_user(interaction.user.id, mutate)
        if not ok:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        consumed_text = "\n".join(
            f"{_material_name(mat_id)} × **{amount}**" for mat_id, amount in consumed
        )
        assert result is not None
        title = "✨ 終極技能製作完成" if result.get("new") else "✨ 終極技能強化完成"
        result_status = "新增" if result.get("new") else f"強化到 +{int(result.get('level', 0))}"
        embed = discord.Embed(
            title=title,
            description=f"{_skill_detail_line(skill, level=int(result.get('level', 0)))}\n取得結果：**{result_status}**\n\n消耗素材：\n{consumed_text}",
            color=data.SKILL_RARITY_COLORS.get(skill["rarity"], config.WIN_COLOR),
        )
        embed.set_footer(text=f"剩餘 Boss 戰素材：{remaining}；用 /rpg skillequip 裝備技能")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="skillgacha", description="每 400 元抽取或強化一個 RPG 技能")
    @app_commands.describe(count="抽取次數")
    @app_commands.choices(
        count=[
            app_commands.Choice(name="單抽", value=1),
            app_commands.Choice(name="十抽", value=10),
        ]
    )
    async def skillgacha(
        self,
        interaction: discord.Interaction,
        count: app_commands.Choice[int],
    ) -> None:
        draw_count = int(count.value)
        cost = draw_count * data.SKILL_GACHA_COST

        def mutate(user: Dict[str, Any]) -> tuple[bool, str, int, List[Dict[str, Any]]]:
            profile = _ensure_profile(user)
            if int(user.get("balance", 0)) < cost:
                return False, "餘額不足。", int(user.get("balance", 0)), []
            user["balance"] = int(user["balance"]) - cost
            return True, "", int(user["balance"]), _grant_skill_draws(profile, draw_count)

        ok, reason, balance, results = await db.mutate_user(interaction.user.id, mutate)
        if not ok:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        counts = {rarity: 0 for rarity in data.SKILL_RARITY_RATES}
        new_count = 0
        enhanced_count = 0
        maxed_count = 0
        for result in results:
            skill = result["skill"]
            counts[skill["rarity"]] += 1
            if result.get("new"):
                new_count += 1
            elif result.get("enhanced"):
                enhanced_count += 1
            elif result.get("maxed"):
                maxed_count += 1
        highlights = sorted(
            results,
            key=lambda result: (_skill_rarity_index(result["skill"]["rarity"]), result["skill"]["id"]),
            reverse=True,
        )[:12]
        summary = " ".join(f"{rarity}:{amount}" for rarity, amount in counts.items() if amount)
        embed = discord.Embed(
            title=f"📚 RPG 技能抽取：{draw_count} 抽",
            description=(
                f"花費 **{cost:,}**，餘額 **{balance:,}**\n"
                f"機率：普通 50 / 稀有 30 / 史詩 15 / 傳說 4 / 神話 1\n"
                f"結果統計：{summary}\n"
                f"新增技能 **{new_count}** 個，技能強化 **{enhanced_count}** 次，滿級重複 **{maxed_count}** 個。"
            ),
            color=max(
                (data.SKILL_RARITY_COLORS[result["skill"]["rarity"]] for result in highlights),
                default=config.EMBED_COLOR,
            ),
        )
        embed.add_field(
            name="本次技能",
            value="\n".join(_skill_result_line(result) for result in highlights)[:1024],
            inline=False,
        )
        embed.set_footer(text="用 /rpg skills 查看；用 /rpg skillequip skill_id slot 裝到 6 格技能欄")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="skills", description="查看已擁有與已裝備的 RPG 技能")
    @app_commands.describe(page="頁數", sort="排序方式", school="篩選流派，輸入全部或完整流派名")
    @app_commands.choices(sort=SKILL_SORT_CHOICES)
    async def skills(
        self,
        interaction: discord.Interaction,
        page: app_commands.Range[int, 1, 99] = 1,
        sort: app_commands.Choice[str] | None = None,
        school: str | None = None,
    ) -> None:
        user_data = await db.get_user_data(interaction.user.id)
        profile = _ensure_profile(user_data)
        view = SkillPageView(
            interaction.user.id,
            profile,
            int(page),
            sort.value if sort else "rarity_desc",
            school if school and school != "全部" else "all",
        )
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    @app_commands.command(name="skillequip", description="把 RPG 技能放進 1-6 技能欄")
    @app_commands.describe(skill_id="技能 ID；輸入 0 可清空該格", slot="技能欄位置 1-6")
    async def skillequip(
        self,
        interaction: discord.Interaction,
        skill_id: app_commands.Range[int, 0, 999],
        slot: app_commands.Range[int, 1, 6],
    ) -> None:
        def mutate(user: Dict[str, Any]) -> tuple[bool, str, Optional[Dict[str, Any]], List[Optional[int]], Dict[str, int]]:
            profile = _ensure_profile(user)
            levels = dict(profile["skills"].get("levels", {}))
            idx = int(slot) - 1
            if int(skill_id) == 0:
                profile["skills"]["equipped"][idx] = None
                return True, "", None, profile["skills"]["equipped"], levels
            skill = _skill_by_id(skill_id)
            if not skill:
                return False, "找不到這個技能 ID。", None, profile["skills"]["equipped"], levels
            if skill["id"] not in profile["skills"]["owned"]:
                return False, "你還沒有抽到這個技能。", None, profile["skills"]["equipped"], levels
            for old_idx, old_skill_id in enumerate(profile["skills"]["equipped"]):
                if old_idx != idx and old_skill_id == skill["id"]:
                    profile["skills"]["equipped"][old_idx] = None
            profile["skills"]["equipped"][idx] = skill["id"]
            return True, "", skill, profile["skills"]["equipped"], levels

        ok, reason, skill, equipped, skill_levels = await db.mutate_user(interaction.user.id, mutate)
        if not ok:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        equipped_lines = []
        for idx, current_id in enumerate(equipped, start=1):
            current = _skill_by_id(current_id)
            level = int(skill_levels.get(str(current_id), 0)) if current_id is not None else 0
            equipped_lines.append(_skill_line(current, equipped_slot=idx, level=level) if current else f"{idx}. 未裝備")
        title = "✅ 技能欄已清空" if skill is None else "✅ 技能已裝備"
        description = f"位置：**{int(slot)}**"
        if skill is not None:
            description += f"\n{_skill_line(skill, level=int(skill_levels.get(str(skill['id']), 0)))}"
        embed = discord.Embed(
            title=title,
            description=description,
            color=data.SKILL_RARITY_COLORS.get(skill["rarity"], config.WIN_COLOR) if skill else config.WIN_COLOR,
        )
        embed.add_field(name="目前技能欄", value="\n".join(equipped_lines), inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="inventory", description="查看 RPG 裝備背包")
    @app_commands.describe(page="頁數")
    async def inventory(
        self,
        interaction: discord.Interaction,
        page: app_commands.Range[int, 1, 99] = 1,
    ) -> None:
        user_data = await db.get_user_data(interaction.user.id)
        profile = _ensure_profile(user_data)
        inventory = sorted(
            profile["inventory"],
            key=lambda item: (_rarity_index(item["rarity"]), item["score"]),
            reverse=True,
        )
        view = InventoryPageView(interaction.user.id, inventory, int(page))
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    @app_commands.command(name="transmute_weapon", description="置換武器類型，只改技能攻擊屬性")
    @app_commands.describe(item_id="背包中的武器 ID，可輸入前幾碼", target="目標武器類型")
    @app_commands.choices(
        target=[
            app_commands.Choice(name="劍（近戰技能）", value="sword"),
            app_commands.Choice(name="弓（遠程技能）", value="bow"),
            app_commands.Choice(name="法杖（魔法技能）", value="staff"),
        ]
    )
    async def transmute_weapon(
        self,
        interaction: discord.Interaction,
        item_id: str,
        target: app_commands.Choice[str],
    ) -> None:
        def mutate(user: Dict[str, Any]) -> tuple[bool, str, Optional[Dict[str, Any]], Dict[str, Any]]:
            profile = _ensure_profile(user)
            item = _find_item(profile, item_id)
            if not item:
                return False, "找不到這件裝備。", None, {}
            ok, reason, info = _transmute_weapon_kind(item, target.value)
            if not ok:
                return False, reason, None, info
            return True, "", item, info

        ok, reason, item, info = await db.mutate_user(interaction.user.id, mutate)
        if not ok or item is None:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        old_kind = data.WEAPON_KIND_LABELS.get(str(info["old_kind"]), str(info["old_kind"]))
        new_kind = data.WEAPON_KIND_LABELS.get(str(info["new_kind"]), str(info["new_kind"]))
        old_skill_text = _format_stats(dict(info.get("old_skill_stats") or {})) or "無"
        new_skill_stat = str(info["new_skill_stat"])
        new_skill_text = f"{_stat_label(new_skill_stat)} +{_format_stat_value(new_skill_stat, float(info['new_skill_value']))}"
        embed = discord.Embed(
            title="🔁 武器置換完成",
            description=(
                f"類型：**{old_kind} → {new_kind}**\n"
                f"名稱：**{info['old_name']} → {info['new_name']}**\n"
                f"技能攻擊：{old_skill_text} → {new_skill_text}\n\n"
                f"{_item_line(item)}"
            ),
            color=data.RARITY_COLORS.get(str(item["rarity"]), config.WIN_COLOR),
        )
        embed.set_footer(text="置換只會改武器類型與技能攻擊欄位，ATK、SPD、暴擊、強化等級不變。")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="dismantle", description="一鍵分解指定稀有度裝備並獲得鍛造石")
    @app_commands.describe(rarity="要分解的稀有度")
    @app_commands.choices(
        rarity=[
            app_commands.Choice(name="N", value="N"),
            app_commands.Choice(name="R", value="R"),
            app_commands.Choice(name="SR", value="SR"),
            app_commands.Choice(name="SSR", value="SSR"),
            app_commands.Choice(name="3SR", value="3SR"),
            app_commands.Choice(name="UR", value="UR"),
            app_commands.Choice(name="Secret", value="Secret"),
        ]
    )
    async def dismantle(
        self,
        interaction: discord.Interaction,
        rarity: app_commands.Choice[str],
    ) -> None:
        user_data = await db.get_user_data(interaction.user.id)
        profile = _ensure_profile(user_data)
        equipped_ids = {
            item_id
            for item_id in [
                profile["equipped"].get("weapon"),
                profile["equipped"].get("armor"),
                *profile["equipped"].get("accessories", []),
            ]
            if item_id
        }
        count = sum(
            1
            for item in profile["inventory"]
            if item.get("rarity") == rarity.value and item.get("id") not in equipped_ids
        )
        stones = count * data.FORGE_STONES_BY_RARITY[rarity.value]
        embed = discord.Embed(
            title="⚠️ 確認分解裝備",
            description=(
                f"將分解所有未裝備的 **{rarity.value}** 裝備。\n"
                f"可分解數量：**{count}**\n"
                f"預計獲得鍛造石：**{stones:,}**"
            ),
            color=config.LOSE_COLOR,
        )
        embed.set_footer(text="已裝備的武器、盔甲、飾品會保留。")
        await interaction.response.send_message(
            embed=embed,
            view=InventoryCleanupView(interaction.user.id, rarity.value),
            ephemeral=True,
        )

    @app_commands.command(name="materials", description="查看 RPG 特殊素材")
    async def materials(self, interaction: discord.Interaction) -> None:
        user_data = await db.get_user_data(interaction.user.id)
        profile = _ensure_profile(user_data)
        lines = [
            f"`{mat_id}` {_material_name(mat_id)} × **{count}**"
            for mat_id, count in sorted(profile["materials"].items())
            if int(count) > 0
        ]
        embed = discord.Embed(
            title="🧪 RPG 特殊素材",
            description=(
                "\n".join(lines)
                if lines
                else (
                    f"目前沒有素材。爬塔 Boss 每 10 層有 {data.BOSS_MATERIAL_DROP_RATE * 100:g}% 機率掉落素材；"
                    f"/rpg boss 有 {BOSS_TRIAL_MATERIAL_DROP_RATE * 100:g}% 機率掉落終極技能素材。"
                )
            ),
            color=config.EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="equip", description="裝備背包中的物品")
    @app_commands.describe(item_id="背包中的裝備 ID，可輸入前幾碼", accessory_slot="飾品格 1-4")
    async def equip(
        self,
        interaction: discord.Interaction,
        item_id: str,
        accessory_slot: app_commands.Range[int, 1, 4] | None = None,
    ) -> None:
        def mutate(user: Dict[str, Any]) -> tuple[bool, str, Optional[Dict[str, Any]], Dict[str, Any], Optional[int]]:
            profile = _ensure_profile(user)
            item = _find_item(profile, item_id)
            if not item:
                return False, "找不到這件裝備。", None, profile, None
            equipped_slot = None
            if item["slot"] == "accessory":
                accessories = profile["equipped"]["accessories"]
                if accessory_slot is None:
                    try:
                        idx = accessories.index(None)
                    except ValueError:
                        idx = 0
                else:
                    idx = int(accessory_slot) - 1
                accessories[idx] = item["id"]
                equipped_slot = idx + 1
            else:
                profile["equipped"][item["slot"]] = item["id"]
            return True, "", item, profile, equipped_slot

        ok, reason, item, profile, equipped_slot = await db.mutate_user(interaction.user.id, mutate)
        if not ok or item is None:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        title = "✅ 裝備完成"
        if equipped_slot:
            title += f"：飾品{equipped_slot}"
        embed = discord.Embed(
            title=title,
            description=_item_line(item),
            color=data.RARITY_COLORS.get(item["rarity"], config.WIN_COLOR),
        )
        embed.add_field(name="目前總屬性", value=_format_total_stats(_total_stats(profile)), inline=False)
        await interaction.response.send_message(embed=embed)

    @enhance.command(name="once", description="消耗鍛造石強化裝備一次")
    @app_commands.describe(item_id="要強化的裝備 ID，可輸入前幾碼")
    async def enhance_once(self, interaction: discord.Interaction, item_id: str) -> None:
        def mutate(user: Dict[str, Any]) -> tuple[bool, str, Optional[Dict[str, Any]], bool, float, int, Dict[str, str]]:
            profile = _ensure_profile(user)
            item = _find_item(profile, item_id)
            if not item:
                return False, "找不到這件裝備。", None, False, 0.0, int(profile.get("forge_stones", 0)), {}
            level = int(item.get("enhance", 0))
            if level >= data.ENHANCE_MAX_LEVEL:
                return False, "這件裝備已經強化到上限。", item, False, 0.0, int(profile.get("forge_stones", 0)), {}
            if int(profile.get("forge_stones", 0)) < data.ENHANCE_COST:
                return False, f"鍛造石不足，每次強化需要 {data.ENHANCE_COST} 個。", item, False, 0.0, int(profile.get("forge_stones", 0)), {}
            profile["forge_stones"] = int(profile.get("forge_stones", 0)) - data.ENHANCE_COST
            rate = _enhance_success_rate(level)
            success = random.random() < rate
            break_stats: Dict[str, str] = {}
            if success:
                item["enhance"] = level + 1
                new_level = int(item["enhance"])
                stat_choices = [key for key in item.get("stats", {}) if key in data.BASE_PLAYER_STATS]
                if new_level >= data.ENHANCE_LIMIT_BREAK_LEVEL and not item.get("limit_break_stat"):
                    if stat_choices:
                        limit_break_stat = random.choice(stat_choices)
                        item["limit_break_stat"] = limit_break_stat
                        break_stats["limit"] = limit_break_stat
                if new_level >= data.ENHANCE_FINAL_BREAK_LEVEL and not item.get("final_break_stat"):
                    if stat_choices:
                        final_choices = [
                            key for key in stat_choices if key != item.get("limit_break_stat")
                        ] or stat_choices
                        final_break_stat = random.choice(final_choices)
                        item["final_break_stat"] = final_break_stat
                        break_stats["final"] = final_break_stat
            item["score"] = round(
                sum(_item_stat_value(item, key, float(value)) for key, value in item.get("stats", {}).items()),
                1,
            )
            return True, "", item, success, rate, int(profile.get("forge_stones", 0)), break_stats

        ok, reason, item, success, rate, stones, break_stats = await db.mutate_user(interaction.user.id, mutate)
        if not ok:
            await interaction.response.send_message(reason, ephemeral=True)
            return
        assert item is not None

        level = int(item.get("enhance", 0))
        break_lines = []
        if success and break_stats.get("limit"):
            break_lines.append(
                f"+15突破屬性：**{_stat_label(break_stats['limit'])} ×{data.ENHANCE_LIMIT_BREAK_MULTIPLIER:g}**"
            )
        if success and break_stats.get("final"):
            break_lines.append(
                f"+20突破屬性：**{_stat_label(break_stats['final'])} ×{data.ENHANCE_FINAL_BREAK_MULTIPLIER:g}**"
            )
        limit_break_text = "\n" + "\n".join(break_lines) if break_lines else ""
        embed = discord.Embed(
            title="🔨 強化結果",
            description=(
                f"{_item_line(item)}\n\n"
                f"結果：**{'成功' if success else '失敗'}**\n"
                f"本次成功率：**{rate * 100:.1f}%**\n"
                f"目前強化：**+{level}/{data.ENHANCE_MAX_LEVEL}**\n"
                f"剩餘鍛造石：**{stones:,}**"
                f"{limit_break_text}"
            ),
            color=config.WIN_COLOR if success else config.LOSE_COLOR,
        )
        if level < data.ENHANCE_MAX_LEVEL:
            embed.set_footer(text=f"下一次成功率：{_enhance_success_rate(level) * 100:.1f}%")
        await interaction.response.send_message(embed=embed)

    @enhance.command(name="auto", description="一鍵連續強化裝備")
    @app_commands.describe(
        item_id="要強化的裝備 ID，可輸入前幾碼",
        target_level="目標強化等級，預設 +20",
    )
    async def enhance_auto(
        self,
        interaction: discord.Interaction,
        item_id: str,
        target_level: app_commands.Range[int, 1, 20] = data.ENHANCE_MAX_LEVEL,
    ) -> None:
        target = min(data.ENHANCE_MAX_LEVEL, int(target_level))

        def mutate(user: Dict[str, Any]) -> tuple[bool, str, Optional[Dict[str, Any]], Dict[str, Any]]:
            profile = _ensure_profile(user)
            item = _find_item(profile, item_id)
            if not item:
                return False, "找不到這件裝備。", None, {}

            start_level = int(item.get("enhance", 0))
            if start_level >= data.ENHANCE_MAX_LEVEL:
                return False, "這件裝備已經強化到上限。", item, {}
            if target <= start_level:
                return False, f"這件裝備目前已經是 +{start_level}，請選更高的目標等級。", item, {}
            if int(profile.get("forge_stones", 0)) < data.ENHANCE_COST:
                return False, f"鍛造石不足，每次強化需要 {data.ENHANCE_COST} 個。", item, {}

            attempts = 0
            successes = 0
            failures = 0
            spent = 0
            break_stats: Dict[str, str] = {}
            stop_reason = "已達目標等級"
            max_attempts = 1000

            while int(item.get("enhance", 0)) < target:
                if int(profile.get("forge_stones", 0)) < data.ENHANCE_COST:
                    stop_reason = "鍛造石不足"
                    break
                if attempts >= max_attempts:
                    stop_reason = "已達單次一鍵強化安全上限"
                    break

                level = int(item.get("enhance", 0))
                profile["forge_stones"] = int(profile.get("forge_stones", 0)) - data.ENHANCE_COST
                spent += data.ENHANCE_COST
                attempts += 1
                if random.random() < _enhance_success_rate(level):
                    successes += 1
                    item["enhance"] = level + 1
                    new_level = int(item["enhance"])
                    stat_choices = [key for key in item.get("stats", {}) if key in data.BASE_PLAYER_STATS]
                    if new_level >= data.ENHANCE_LIMIT_BREAK_LEVEL and not item.get("limit_break_stat"):
                        if stat_choices:
                            limit_break_stat = random.choice(stat_choices)
                            item["limit_break_stat"] = limit_break_stat
                            break_stats["limit"] = limit_break_stat
                    if new_level >= data.ENHANCE_FINAL_BREAK_LEVEL and not item.get("final_break_stat"):
                        if stat_choices:
                            final_choices = [
                                key for key in stat_choices if key != item.get("limit_break_stat")
                            ] or stat_choices
                            final_break_stat = random.choice(final_choices)
                            item["final_break_stat"] = final_break_stat
                            break_stats["final"] = final_break_stat
                else:
                    failures += 1

            if int(item.get("enhance", 0)) >= target:
                stop_reason = "已達目標等級"
            item["score"] = round(
                sum(_item_stat_value(item, key, float(value)) for key, value in item.get("stats", {}).items()),
                1,
            )
            summary = {
                "start_level": start_level,
                "end_level": int(item.get("enhance", 0)),
                "target": target,
                "attempts": attempts,
                "successes": successes,
                "failures": failures,
                "spent": spent,
                "stones": int(profile.get("forge_stones", 0)),
                "stop_reason": stop_reason,
                "break_stats": break_stats,
            }
            return True, "", item, summary

        ok, reason, item, summary = await db.mutate_user(interaction.user.id, mutate)
        if not ok:
            await interaction.response.send_message(reason, ephemeral=True)
            return
        assert item is not None

        break_lines = []
        break_stats = summary.get("break_stats", {})
        if break_stats.get("limit"):
            break_lines.append(
                f"+15突破：**{_stat_label(break_stats['limit'])} ×{data.ENHANCE_LIMIT_BREAK_MULTIPLIER:g}**"
            )
        if break_stats.get("final"):
            break_lines.append(
                f"+20突破：**{_stat_label(break_stats['final'])} ×{data.ENHANCE_FINAL_BREAK_MULTIPLIER:g}**"
            )
        break_text = "\n".join(break_lines) if break_lines else "本次沒有新的突破屬性。"

        embed = discord.Embed(
            title="🔨 一鍵強化完成",
            description=_item_line(item),
            color=config.WIN_COLOR if int(summary["end_level"]) > int(summary["start_level"]) else config.INFO_COLOR,
        )
        embed.add_field(
            name="強化統計",
            value=(
                f"目標：**+{summary['target']}**\n"
                f"強化：**+{summary['start_level']} → +{summary['end_level']}**\n"
                f"嘗試：**{summary['attempts']}** 次\n"
                f"成功：**{summary['successes']}**，失敗：**{summary['failures']}**\n"
                f"消耗鍛造石：**{summary['spent']:,}**\n"
                f"剩餘鍛造石：**{summary['stones']:,}**\n"
                f"停止原因：**{summary['stop_reason']}**"
            ),
            inline=False,
        )
        embed.add_field(name="突破結果", value=break_text, inline=False)
        if int(summary["end_level"]) < data.ENHANCE_MAX_LEVEL:
            embed.set_footer(text=f"下一次成功率：{_enhance_success_rate(int(summary['end_level'])) * 100:.1f}%")
        await interaction.response.send_message(embed=embed)

    @craft.command(name="accessory", description="使用 Boss 素材製作飾品")
    @app_commands.describe(rarity="要製作的飾品稀有度", kind="飾品類型")
    @app_commands.choices(
        rarity=[
            app_commands.Choice(name="N", value="N"),
            app_commands.Choice(name="R", value="R"),
            app_commands.Choice(name="SR", value="SR"),
            app_commands.Choice(name="SSR", value="SSR"),
            app_commands.Choice(name="3SR", value="3SR"),
            app_commands.Choice(name="UR", value="UR"),
        ],
        kind=[
            app_commands.Choice(name="純生命", value="pure_hp"),
            app_commands.Choice(name="純攻擊", value="pure_atk"),
            app_commands.Choice(name="純防禦", value="pure_def"),
            app_commands.Choice(name="純暴擊率", value="pure_crit_chance"),
            app_commands.Choice(name="純暴擊傷害", value="pure_crit_damage"),
            app_commands.Choice(name="防禦型 HP+DEF", value="defense"),
            app_commands.Choice(name="攻擊型 ATK+Crit", value="attack"),
            app_commands.Choice(name="綜合型", value="balanced"),
        ],
    )
    async def craft_accessory(
        self,
        interaction: discord.Interaction,
        rarity: app_commands.Choice[str],
        kind: app_commands.Choice[str],
    ) -> None:
        def mutate(user: Dict[str, Any]) -> tuple[bool, str, Optional[Dict[str, Any]], Optional[str]]:
            profile = _ensure_profile(user)
            if len(profile["inventory"]) >= MAX_INVENTORY:
                return False, f"背包空間不足，最多 {MAX_INVENTORY} 件。", None, None
            chosen_material = None
            for mat_id, count in profile["materials"].items():
                parts = mat_id.split(":")
                if len(parts) == 3 and parts[2] == rarity.value and int(count) > 0:
                    chosen_material = mat_id
                    break
            if not chosen_material:
                return False, f"沒有可製作 **{rarity.value}** 飾品的 Boss 素材。", None, None
            profile["materials"][chosen_material] = int(profile["materials"][chosen_material]) - 1
            item = _generate_accessory(rarity.value, kind.value)
            profile["inventory"].append(item)
            return True, "", item, chosen_material

        ok, reason, item, material = await db.mutate_user(interaction.user.id, mutate)
        if not ok or item is None:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        embed = discord.Embed(
            title="🛠️ 飾品製作完成",
            description=f"消耗：**{_material_name(material)}**\n\n{_item_line(item)}",
            color=data.RARITY_COLORS[item["rarity"]],
        )
        embed.set_footer(text="製作飾品能力有 ±15% 浮動；可用 /rpg equip 裝備到 1-4 格。")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="boss", description="挑戰五種高難度 Boss 戰")
    @app_commands.describe(boss="選擇 Boss 流派")
    @app_commands.choices(
        boss=[
            app_commands.Choice(name="血誓暴君（自殘流）", value="blood"),
            app_commands.Choice(name="神壁巨像（戰士流）", value="aegis"),
            app_commands.Choice(name="巨獸獵主（最大生命值流）", value="colossus"),
            app_commands.Choice(name="瘟疫星核（Debuff流）", value="plague"),
            app_commands.Choice(name="弒神神射（基礎射手流）", value="deadeye"),
        ]
    )
    async def boss(
        self,
        interaction: discord.Interaction,
        boss: app_commands.Choice[str],
    ) -> None:
        user_data = await db.get_user_data(interaction.user.id)
        profile = _ensure_profile(user_data)
        enemy = _enemy_for_boss_trial(boss.value)
        view = TowerBattleView(interaction.user.id, profile, enemy)
        await interaction.response.send_message(embed=view._embed(), view=view)

    @app_commands.command(name="tower", description="手動回合制爬塔戰鬥")
    @app_commands.describe(
        tower="選擇塔",
        skip_to_boss="直接跳到下一個 Boss 層",
        floor="指定要重複挑戰的已解鎖樓層，可省略",
    )
    @app_commands.choices(
        tower=[
            app_commands.Choice(name="翠影塔（簡單）", value="verdant"),
            app_commands.Choice(name="硫火塔（普通）", value="brimstone"),
            app_commands.Choice(name="星神塔（困難）", value="astral"),
            app_commands.Choice(name="終焉塔（極難）", value="abyssal"),
        ]
    )
    async def tower(
        self,
        interaction: discord.Interaction,
        tower: app_commands.Choice[str],
        skip_to_boss: bool = False,
        floor: app_commands.Range[int, 1, 50] | None = None,
    ) -> None:
        user_data = await db.get_user_data(interaction.user.id)
        profile = _ensure_profile(user_data)
        target_floor = _next_tower_floor(profile, tower.value, skip_to_boss, int(floor) if floor else None)
        if target_floor is None:
            highest = int(profile["towers"].get(tower.value, 0))
            await interaction.response.send_message(
                f"尚未解鎖這個樓層。目前最高進度：{highest}F，可挑戰到 {min(highest + 1, 50)}F。",
                ephemeral=True,
            )
            return
        enemy = _enemy_for_floor(tower.value, target_floor)
        view = TowerBattleView(interaction.user.id, profile, enemy)
        await interaction.response.send_message(embed=view._embed(), view=view)

    @app_commands.command(name="reward", description="每 12 小時挑戰一次金幣或經驗副本")
    @app_commands.describe(reward="選擇獎勵類型", difficulty="選擇副本難度")
    @app_commands.choices(
        reward=[
            app_commands.Choice(name="金幣副本", value="coins"),
            app_commands.Choice(name="經驗副本", value="exp"),
        ],
        difficulty=[
            app_commands.Choice(name="簡單（約翠影塔 30F Boss）", value="verdant"),
            app_commands.Choice(name="普通（約硫火塔 30F Boss）", value="brimstone"),
            app_commands.Choice(name="困難（約星神塔 30F Boss）", value="astral"),
            app_commands.Choice(name="極難（約終焉塔 30F Boss）", value="abyssal"),
        ],
    )
    async def reward(
        self,
        interaction: discord.Interaction,
        reward: app_commands.Choice[str],
        difficulty: app_commands.Choice[str],
    ) -> None:
        now = int(time.time())

        def mutate(user: Dict[str, Any]) -> tuple[bool, str, Dict[str, Any]]:
            profile = _ensure_profile(user)
            last = int(profile.get("reward_dungeon_last", 0))
            elapsed = now - last
            if elapsed < data.REWARD_DUNGEON_COOLDOWN_SECONDS:
                remain = data.REWARD_DUNGEON_COOLDOWN_SECONDS - elapsed
                return False, f"獎勵副本冷卻中，請於 **{_format_cooldown(remain)}** 後再挑戰。", profile
            profile["reward_dungeon_last"] = now
            return True, "", profile

        ok, reason, profile = await db.mutate_user(interaction.user.id, mutate)
        if not ok:
            await interaction.response.send_message(reason, ephemeral=True)
            return

        dungeon = data.REWARD_DUNGEONS[difficulty.value]
        amount = int(dungeon[reward.value])
        enemy = _enemy_for_reward_dungeon(difficulty.value, reward.value)
        view = TowerBattleView(interaction.user.id, profile, enemy)
        embed = view._embed()
        embed.add_field(
            name="副本獎勵",
            value=(
                f"類型：**{_reward_type_label(reward.value)}**\n"
                f"難度：**{dungeon['difficulty_name']}**\n"
                f"勝利獎勵：**{amount:,}**\n"
                f"冷卻：**12 小時**，進入副本時開始計算。"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="rates", description="查看 RPG 抽裝機率與能力公式")
    async def rates(self, interaction: discord.Interaction) -> None:
        rows = []
        for rarity, rate in data.RARITY_RATES.items():
            rows.append(f"**{rarity}**：{rate * 100:g}%　倍率 `{_rarity_multiplier(rarity):.2f}x`")
        embed = discord.Embed(
            title="📜 RPG 抽裝機率",
            description=(
                "\n".join(rows)
                + "\n\n武器、盔甲各稀有度各有 8 個名稱。"
                + "\n武器暴擊詞條：每稀有度 3 件只有 Crit Chance、3 件只有 Crit Damage、2 件兩者都有。"
                + "\n能力公式：`Stat = Stat_base × (1 / P)^0.45 × 隨機浮動`"
                + "\n武器與飾品來源的 Crit Damage 已做裝備端減半；超過 100% 的 Crit Chance 轉 Crit Damage 時轉換量 ×0.5。"
                + "\n裝備 SPD 已套用 ×1.4；飾品 ATK 已套用 ×1.3。"
                + "\n/rpg gacha 可選自動分解門檻，本次抽到該稀有度以下的裝備會直接換鍛造石。"
                + "\n職業：普通職業目前等機率抽取；首次免費，重抽花費 "
                + f"`{data.CLASS_REROLL_COST:,}`。"
                + "\n職業池：戰士、狂戰士、盜賊、刺客、獵人、武士、法師。"
                + "\n分解鍛造石：N 1 / R 2 / SR 4 / SSR 6 / 3SR 8 / UR 16 / Secret 64。"
                + "\n強化：每次消耗 10 鍛造石，成功時裝備數值 +4%，最高 +20。"
                + "\n+14 衝 +15 成功率 5%，之後一路降到 +19 衝 +20 的 2%。"
                + "\n+15 隨機 1 個屬性突破為 1.5 倍；+20 再隨機 1 個屬性突破為 1.75 倍。"
                + f"\n技能抽卡：每抽 {data.SKILL_GACHA_COST}，普通 50 / 稀有 30 / 史詩 15 / 傳說 4 / 神話 1；重複技能會強化，最高 +{SKILL_ENHANCE_MAX_LEVEL}。"
                + "\n每次技能強化：傷害技能最終傷害 ×1.10；Buff 技能正面效果 ×1.10。秘密終極技能不會抽出。"
                + "\n/rpg boss 勝利時 50% 掉落 Boss 戰素材，5 個可用 /rpg craft skill 自選製作或強化秘密技能。"
                + "\n技能欄 6 格；技能攻擊視為一次攻擊，近戰/遠程/魔法技能吃武器額外技能傷害。"
                + "\n屬性點：HP/ATK/DEF 每點 +1% 總值；SPD +0.8 / Crit Chance +1% / Crit Damage +1.8%。"
                + "\n玩家、裝備、飾品與敵人的 HP 已套用 2.2 倍縮放。"
                + f"\n/rpg boss 的 Boss HP ×{BOSS_TRIAL_HP_MULTIPLIER:g}，ATK ×{BOSS_TRIAL_ATK_MULTIPLIER:g}。"
                + f"\nBoss 素材掉落率 {data.BOSS_MATERIAL_DROP_RATE * 100:g}%；終焉塔 Boss 掉落 UR 素材，可製作 UR 飾品。"
                + "\n獎勵副本：/rpg reward 每 12 小時挑戰一次，可選金幣或經驗。"
                + "\n金幣副本：簡單 10000 / 普通 20000 / 困難 30000 / 極難 50000。"
                + "\n經驗副本：簡單 1000 / 普通 2500 / 困難 3500 / 極難 5000。"
            ),
            color=config.EMBED_COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RPG(bot))
