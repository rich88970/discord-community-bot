from __future__ import annotations


RARITY_RATES = {
    "N": 0.600,
    "R": 0.300,
    "SR": 0.06105,
    "SSR": 0.02605,
    "3SR": 0.010,
    "UR": 0.0025,
    "Secret": 0.0004,
}

RARITY_COLORS = {
    "N": 0x95A5A6,
    "R": 0x3498DB,
    "SR": 0x9B59B6,
    "SSR": 0xF1C40F,
    "3SR": 0xE67E22,
    "UR": 0xE74C3C,
    "Secret": 0xFFFFFF,
}

FORGE_STONES_BY_RARITY = {
    "N": 1,
    "R": 2,
    "SR": 4,
    "SSR": 6,
    "3SR": 8,
    "UR": 16,
    "Secret": 64,
}

ENHANCE_MAX_LEVEL = 20
ENHANCE_LIMIT_BREAK_LEVEL = 15
ENHANCE_LIMIT_BREAK_MULTIPLIER = 1.5
ENHANCE_FINAL_BREAK_LEVEL = 20
ENHANCE_FINAL_BREAK_MULTIPLIER = 1.75
ENHANCE_COST = 10
HP_SCALE = 2.2
BOSS_MATERIAL_DROP_RATE = 0.15
REWARD_DUNGEON_COOLDOWN_SECONDS = 12 * 60 * 60
SKILL_SLOT_COUNT = 6
SKILL_GACHA_COST = 400

SKILL_RARITY_RATES = {
    "普通": 0.50,
    "稀有": 0.30,
    "史詩": 0.15,
    "傳說": 0.04,
    "神話": 0.01,
    "秘密": 0.0,
}

SKILL_RARITY_COLORS = {
    "普通": 0x95A5A6,
    "稀有": 0x3498DB,
    "史詩": 0x9B59B6,
    "傳說": 0xE67E22,
    "神話": 0xE74C3C,
    "秘密": 0xFFFFFF,
}

RARITY_EMOJIS = {
    "N": "N",
    "R": "R",
    "SR": "SR",
    "SSR": "SSR",
    "3SR": "3SR",
    "UR": "UR",
    "Secret": "Secret",
}

BASE_PLAYER_STATS = {
    "hp": 220.0,
    "atk": 12.0,
    "def": 5.0,
    "spd": 10.0,
    "crit_chance": 5.0,
    "crit_damage": 50.0,
    "melee_damage": 0.0,
    "ranged_damage": 0.0,
    "magic_damage": 0.0,
    "def_pen": 0.0,
    "debuffed_damage": 0.0,
}

POINT_GAINS = {
    "hp": 0.01,
    "atk": 0.01,
    "def": 0.01,
    "spd": 0.8,
    "crit_chance": 1.0,
    "crit_damage": 1.8,
}

STAT_LABELS = {
    "hp": "HP",
    "atk": "ATK",
    "def": "DEF",
    "spd": "SPD",
    "crit_chance": "Crit Chance",
    "crit_damage": "Crit Damage",
    "melee_damage": "Melee Skill",
    "ranged_damage": "Ranged Skill",
    "magic_damage": "Magic Skill",
    "def_pen": "Def Pen",
    "debuffed_damage": "Debuffed Damage",
}

STAT_EMOJIS = {
    "hp": "❤️",
    "atk": "⚔️",
    "def": "🛡️",
    "spd": "💨",
    "crit_chance": "🎯",
    "crit_damage": "💥",
    "melee_damage": "🗡️",
    "ranged_damage": "🏹",
    "magic_damage": "🔮",
    "def_pen": "🪓",
    "debuffed_damage": "🧪",
}

EQUIPMENT_BASE_STATS = {
    "weapon": {
        "atk": 8.0,
        "spd": 0.4,
        "crit_chance": 1.2,
        "crit_damage": 2.5,
    },
    "armor": {
        "hp": 154.0,
        "def": 7.0,
        "spd": 0.2,
    },
}

EQUIPMENT_SLOTS = ("weapon", "armor")
ITEM_SLOTS = ("weapon", "armor", "accessory")
ACCESSORY_SLOT_COUNT = 4

CLASS_REROLL_COST = 1000
CLASSES = {
    "warrior": {
        "name": "戰士",
        "rarity": "普通",
        "description": "HP +30%，DEF +30%",
        "multipliers": {"hp": 1.30, "def": 1.30},
        "add": {},
        "battle": {},
    },
    "berserker": {
        "name": "狂戰士",
        "rarity": "普通",
        "description": "ATK +35%，每回合失去最大 HP 10%",
        "multipliers": {"atk": 1.35},
        "add": {},
        "battle": {"round_hp_loss_pct": 0.10},
    },
    "thief": {
        "name": "盜賊",
        "rarity": "普通",
        "description": "SPD +25%，ATK +10%",
        "multipliers": {"spd": 1.25, "atk": 1.10},
        "add": {},
        "battle": {},
    },
    "assassin": {
        "name": "刺客",
        "rarity": "普通",
        "description": "SPD +35%，ATK +10%，Crit Chance +7%，Crit Damage +7%",
        "multipliers": {"spd": 1.35, "atk": 1.10},
        "add": {"crit_chance": 7.0, "crit_damage": 7.0},
        "battle": {},
    },
    "hunter": {
        "name": "獵人",
        "rarity": "普通",
        "description": "獲得 45% DEF 穿透",
        "multipliers": {},
        "add": {"def_pen": 45.0},
        "battle": {},
    },
    "samurai": {
        "name": "武士",
        "rarity": "普通",
        "description": "開場獲得 5 層劍氣，SPD +20%",
        "multipliers": {"spd": 1.20},
        "add": {},
        "battle": {"starting_sword_aura": 5},
    },
    "mage": {
        "name": "法師",
        "rarity": "普通",
        "description": "Crit Chance +25%，Crit Damage +25%，對 Debuff 敵人傷害 +30%",
        "multipliers": {},
        "add": {"crit_chance": 25.0, "crit_damage": 25.0, "debuffed_damage": 30.0},
        "battle": {},
    },
}

SLOT_LABELS = {
    "weapon": "武器",
    "armor": "盔甲",
}

# Each rarity has exactly 8 names per equipment type.
# Item-name pools are taken from Terraria and Calamity equipment names.
# Runtime scraping is intentionally avoided so the bot can run without network access.
WEAPON_NAMES_BY_RARITY = {
    "N": [
        "Copper Shortsword",
        "Iron Broadsword",
        "Wooden Bow",
        "Wand of Sparking",
        "Spear",
        "Blowpipe",
        "Rally",
        "Throwing Knife",
    ],
    "R": [
        "Enchanted Sword",
        "Starfury",
        "Minishark",
        "Boomstick",
        "The Bee's Knees",
        "Phoenix Blaster",
        "Space Gun",
        "Flamarang",
    ],
    "SR": [
        "Night's Edge",
        "Dao of Pow",
        "Daedalus Stormbow",
        "Shadowflame Knife",
        "Megashark",
        "Crystal Serpent",
        "Flying Knife",
        "Ice Sickle",
    ],
    "SSR": [
        "True Night's Edge",
        "True Excalibur",
        "Terra Blade",
        "Seedler",
        "Vampire Knives",
        "Razorblade Typhoon",
        "Flairon",
        "Staff of the Frost Hydra",
    ],
    "3SR": [
        "Aegis Blade",
        "Ark of the Ancients",
        "Phantasm",
        "Tsunami",
        "Stardust Dragon Staff",
        "Rainbow Crystal Staff",
        "Last Prism",
        "Lunar Flare",
    ],
    "UR": [
        "Zenith",
        "Murasama",
        "Ark of the Cosmos",
        "Eventide",
        "Aerial Bane",
        "Yharim's Crystal",
        "Event Horizon",
        "S.D.M.G.",
    ],
    "Secret": [
        "Exoblade",
        "Galaxia",
        "Drataliornus",
        "Heavenly Gale",
        "Photoviscerator",
        "Vehemence",
        "Scarlet Devil",
        "Halibut Cannon",
    ],
}

WEAPON_KIND_LABELS = {
    "sword": "劍",
    "bow": "弓",
    "staff": "法杖",
}

WEAPON_KIND_SKILL_STAT = {
    "sword": "melee_damage",
    "bow": "ranged_damage",
    "staff": "magic_damage",
}

SKILL_TYPE_DAMAGE_STAT = {
    "近戰": "melee_damage",
    "遠程": "ranged_damage",
    "魔法": "magic_damage",
}

WEAPON_KIND_BY_NAME = {
    "Copper Shortsword": "sword",
    "Iron Broadsword": "sword",
    "Wooden Bow": "bow",
    "Wand of Sparking": "staff",
    "Spear": "sword",
    "Blowpipe": "bow",
    "Rally": "sword",
    "Throwing Knife": "sword",
    "Enchanted Sword": "sword",
    "Starfury": "sword",
    "Minishark": "bow",
    "Boomstick": "bow",
    "The Bee's Knees": "bow",
    "Phoenix Blaster": "bow",
    "Space Gun": "staff",
    "Flamarang": "sword",
    "Night's Edge": "sword",
    "Dao of Pow": "sword",
    "Daedalus Stormbow": "bow",
    "Shadowflame Knife": "sword",
    "Megashark": "bow",
    "Crystal Serpent": "staff",
    "Flying Knife": "sword",
    "Ice Sickle": "sword",
    "True Night's Edge": "sword",
    "True Excalibur": "sword",
    "Terra Blade": "sword",
    "Seedler": "sword",
    "Vampire Knives": "sword",
    "Razorblade Typhoon": "staff",
    "Flairon": "sword",
    "Staff of the Frost Hydra": "staff",
    "Aegis Blade": "sword",
    "Ark of the Ancients": "sword",
    "Phantasm": "bow",
    "Tsunami": "bow",
    "Stardust Dragon Staff": "staff",
    "Rainbow Crystal Staff": "staff",
    "Last Prism": "staff",
    "Lunar Flare": "staff",
    "Zenith": "sword",
    "Murasama": "sword",
    "Ark of the Cosmos": "sword",
    "Eventide": "bow",
    "Aerial Bane": "bow",
    "Yharim's Crystal": "staff",
    "Event Horizon": "staff",
    "S.D.M.G.": "bow",
    "Exoblade": "sword",
    "Galaxia": "sword",
    "Drataliornus": "bow",
    "Heavenly Gale": "bow",
    "Photoviscerator": "staff",
    "Vehemence": "staff",
    "Scarlet Devil": "staff",
    "Halibut Cannon": "bow",
}

ARMOR_NAMES_BY_RARITY = {
    "N": [
        "Wood armor",
        "Copper armor",
        "Tin armor",
        "Iron armor",
        "Lead armor",
        "Silver armor",
        "Tungsten armor",
        "Gold armor",
    ],
    "R": [
        "Platinum armor",
        "Shadow armor",
        "Crimson armor",
        "Meteor armor",
        "Jungle armor",
        "Necro armor",
        "Fossil armor",
        "Molten armor",
    ],
    "SR": [
        "Cobalt armor",
        "Palladium armor",
        "Mythril armor",
        "Orichalcum armor",
        "Adamantite armor",
        "Titanium armor",
        "Frost armor",
        "Forbidden armor",
    ],
    "SSR": [
        "Hallowed armor",
        "Chlorophyte armor",
        "Turtle armor",
        "Shroomite armor",
        "Spectre armor",
        "Beetle armor",
        "Tiki armor",
        "Spooky armor",
    ],
    "3SR": [
        "Solar Flare armor",
        "Vortex armor",
        "Nebula armor",
        "Stardust armor",
        "Wulfrum armor",
        "Victide armor",
        "Aerospec armor",
        "Statigel armor",
    ],
    "UR": [
        "Daedalus armor",
        "Reaver armor",
        "Hydrothermic armor",
        "Astral armor",
        "Ataxia armor",
        "Tarragon armor",
        "Bloodflare armor",
        "God Slayer armor",
    ],
    "Secret": [
        "Auric Tesla armor",
        "Demonshade armor",
        "Silva armor",
        "Empyrean armor",
        "Fearmonger armor",
        "Omega Blue armor",
        "Prismatic armor",
        "Gem Tech armor",
    ],
}

TOWERS = {
    "verdant": {
        "name": "翠影塔",
        "difficulty": 0.72,
        "color": 0x2ECC71,
        "material_prefix": "翠影",
        "bosses": {
            10: ("King Slime", "N"),
            20: ("Eye of Cthulhu", "N"),
            30: ("Eater of Worlds", "R"),
            40: ("Queen Bee", "R"),
            50: ("Skeletron", "SR"),
        },
    },
    "brimstone": {
        "name": "硫火塔",
        "difficulty": 1.18,
        "color": 0xE67E22,
        "material_prefix": "硫火",
        "bosses": {
            10: ("Wall of Flesh", "R"),
            20: ("The Destroyer", "SR"),
            30: ("Skeletron Prime", "SR"),
            40: ("Plantera", "SSR"),
            50: ("Calamitas Clone", "SSR"),
        },
    },
    "astral": {
        "name": "星神塔",
        "difficulty": 1.72,
        "color": 0x9B59B6,
        "material_prefix": "星神",
        "bosses": {
            10: ("Astrum Aureus", "SR"),
            20: ("Ravager", "SSR"),
            30: ("The Plaguebringer Goliath", "SSR"),
            40: ("Providence", "3SR"),
            50: ("Supreme Calamitas", "3SR"),
        },
    },
    "abyssal": {
        "name": "終焉塔",
        "difficulty": 2.18,
        "color": 0xC0392B,
        "material_prefix": "終焉",
        "bosses": {
            10: ("Polterghast", "UR"),
            20: ("The Old Duke", "UR"),
            30: ("The Devourer of Gods", "UR"),
            40: ("Yharon", "UR"),
            50: ("Exo Mechs", "UR"),
        },
    },
}

REWARD_DUNGEONS = {
    "verdant": {
        "name": "翠影試煉",
        "difficulty_name": "簡單",
        "tower_id": "verdant",
        "floor": 30,
        "coins": 600,
        "exp": 1000,
        "color": 0x2ECC71,
    },
    "brimstone": {
        "name": "硫火試煉",
        "difficulty_name": "普通",
        "tower_id": "brimstone",
        "floor": 30,
        "coins": 1200,
        "exp": 2500,
        "color": 0xE67E22,
    },
    "astral": {
        "name": "星神試煉",
        "difficulty_name": "困難",
        "tower_id": "astral",
        "floor": 30,
        "coins": 2000,
        "exp": 3500,
        "color": 0x9B59B6,
    },
    "abyssal": {
        "name": "終焉試煉",
        "difficulty_name": "極難",
        "tower_id": "abyssal",
        "floor": 30,
        "coins": 3000,
        "exp": 5000,
        "color": 0xC0392B,
    },
}

ACCESSORY_KINDS = {
    "pure_hp": {
        "name": "純生命",
        "stats": ("hp",),
        "budget": 2.70,
    },
    "pure_atk": {
        "name": "純攻擊",
        "stats": ("atk",),
        "budget": 2.70,
    },
    "pure_def": {
        "name": "純防禦",
        "stats": ("def",),
        "budget": 2.70,
    },
    "pure_crit_chance": {
        "name": "純暴擊率",
        "stats": ("crit_chance",),
        "budget": 2.70,
    },
    "pure_crit_damage": {
        "name": "純暴擊傷害",
        "stats": ("crit_damage",),
        "budget": 2.70,
    },
    "defense": {
        "name": "防禦型",
        "stats": ("hp", "def"),
        "budget": 1.35,
    },
    "attack": {
        "name": "攻擊型",
        "stats": ("atk", "crit_chance", "crit_damage"),
        "budget": 0.90,
    },
    "balanced": {
        "name": "綜合型",
        "stats": ("hp", "atk", "def", "spd", "crit_chance", "crit_damage"),
        "budget": 0.45,
    },
}

ACCESSORY_NAMES_BY_KIND = {
    "pure_hp": "Bulwark Charm",
    "pure_atk": "Berserker Charm",
    "pure_def": "Guardian Charm",
    "pure_crit_chance": "Keen Charm",
    "pure_crit_damage": "Ruin Charm",
    "defense": "Aegis Sigil",
    "attack": "Slayer Sigil",
    "balanced": "Harmony Sigil",
}

MONSTER_NAMES_BY_TOWER = {
    "verdant": [
        "Green Slime",
        "Blue Slime",
        "Zombie",
        "Demon Eye",
        "Face Monster",
        "Eater of Souls",
        "Crimera",
        "Hornet",
        "Man Eater",
        "Harpy",
        "Wulfrum Drone",
        "Wulfrum Gyrator",
        "Sea Floaty",
        "Cnidrion",
        "Aero Slime",
        "Crawler",
    ],
    "brimstone": [
        "Armored Skeleton",
        "Chaos Elemental",
        "Clinger",
        "Ichor Sticker",
        "Floaty Gross",
        "Giant Tortoise",
        "Ice Golem",
        "Wyvern",
        "Angry Nimbus",
        "Granite Elemental",
        "Calamity Eye",
        "Despair Stone",
        "Heat Spirit",
        "Scryllar",
        "Soul Slurper",
        "Charred Slime",
    ],
    "astral": [
        "Astral Slime",
        "Astral Probe",
        "Atlas",
        "Fusion Feeder",
        "Nova",
        "Hadarian",
        "Impious Immolator",
        "Profaned Energy Body",
        "Soul Seeker",
        "Devourer Spawn",
        "Plague Charger",
        "Reaper Shark",
        "Colossal Squid",
        "Eidolon Wyrm",
        "Gamma Slime",
        "Phantom Spirit",
    ],
    "abyssal": [
        "Storm Weaver",
        "Ceaseless Void",
        "Signus",
        "Cosmic Guardian",
        "Phantom Spirit",
        "Devourer Spawn",
        "Profaned Energy Body",
        "Impious Immolator",
        "Soul Seeker",
        "Reaper Shark",
        "Eidolon Wyrm",
        "Colossal Squid",
        "Gamma Slime",
        "Miracle Matter",
        "Thanatos",
        "Ares",
    ],
}
