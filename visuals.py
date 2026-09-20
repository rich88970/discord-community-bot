"""共用視覺工具：卡牌、骰子、進度條、氣球、輪盤等 ASCII / Emoji 圖樣。

所有「需要對齊」的圖樣（卡牌、骰子、氣球、進度條）都建議放進
Discord 的 ``` ``` 程式碼區塊中，才能保留等寬字型。
"""

from __future__ import annotations

import math
from typing import Iterable, List


# -----------------------------------------------------------------------------
# 撲克牌
# -----------------------------------------------------------------------------

CARD_W = 7
CARD_H = 5


def _pad_rank(rank: str) -> str:
    """A → 'A '；10 → '10'。固定 2 字元方便對齊。"""
    return rank if len(rank) == 2 else rank + " "


def render_card(rank: str, suit: str) -> List[str]:
    """傳回 5 行的卡牌 ASCII（寬 7）。"""
    top = _pad_rank(rank)
    bot = _pad_rank(rank)[::-1].rstrip() + ""
    bot = (rank if len(rank) == 2 else " " + rank).rjust(2)
    return [
        "┌─────┐",
        f"│{top}   │",
        f"│  {suit}  │",
        f"│   {bot}│",
        "└─────┘",
    ]


def card_back() -> List[str]:
    return [
        "┌─────┐",
        "│░░░░░│",
        "│░ ? ░│",
        "│░░░░░│",
        "└─────┘",
    ]


def card_placeholder() -> List[str]:
    return [
        "         ",
        "         ",
        "         ",
        "         ",
        "         ",
    ]


def join_cards(cards: Iterable[List[str]]) -> str:
    """把多張卡牌橫向接起來。"""
    cards = list(cards)
    if not cards:
        return ""
    rows: List[str] = []
    for line_idx in range(CARD_H):
        rows.append(" ".join(card[line_idx] for card in cards))
    return "\n".join(rows)


# -----------------------------------------------------------------------------
# 骰子
# -----------------------------------------------------------------------------

DIE_W = 9
DIE_H = 5


_DIE_PATTERNS = {
    1: [
        "       ",
        "   ●   ",
        "       ",
    ],
    2: [
        " ●     ",
        "       ",
        "     ● ",
    ],
    3: [
        " ●     ",
        "   ●   ",
        "     ● ",
    ],
    4: [
        " ●   ● ",
        "       ",
        " ●   ● ",
    ],
    5: [
        " ●   ● ",
        "   ●   ",
        " ●   ● ",
    ],
    6: [
        " ●   ● ",
        " ●   ● ",
        " ●   ● ",
    ],
}


def render_die(face: int) -> List[str]:
    p = _DIE_PATTERNS[face]
    return [
        "┌───────┐",
        f"│{p[0]}│",
        f"│{p[1]}│",
        f"│{p[2]}│",
        "└───────┘",
    ]


def rolling_die() -> List[str]:
    """轉動中的骰子。"""
    return [
        "┌───────┐",
        "│ ░░░░░ │",
        "│ ░ ? ░ │",
        "│ ░░░░░ │",
        "└───────┘",
    ]


def join_dice(dice: Iterable[List[str]]) -> str:
    dice = list(dice)
    if not dice:
        return ""
    rows: List[str] = []
    for line_idx in range(DIE_H):
        rows.append(" ".join(d[line_idx] for d in dice))
    return "\n".join(rows)


# -----------------------------------------------------------------------------
# 拉霸盤
# -----------------------------------------------------------------------------

def render_slot(reels: Iterable[str], spinning_mask: Iterable[bool]) -> str:
    """三圈拉霸畫面。spinning_mask True 的位置顯示問號。"""
    reels = list(reels)
    spinning_mask = list(spinning_mask)
    cells = []
    for sym, spin in zip(reels, spinning_mask):
        cells.append("❓" if spin else sym)
    line = " │ ".join(cells)
    return (
        "╔═════════════════╗\n"
        f"║   {line}   ║\n"
        "╚═════════════════╝"
    )


# -----------------------------------------------------------------------------
# 輪盤
# -----------------------------------------------------------------------------

RED_NUMBERS = {
    1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36
}


def number_color_emoji(n: int) -> str:
    if n == 0:
        return "🟢"
    return "🔴" if n in RED_NUMBERS else "⚫"


def render_roulette(highlight: int, around: int = 5) -> str:
    """畫出焦點號碼附近的輪盤片段。"""
    items: List[str] = []
    for offset in range(-around, around + 1):
        n = (highlight + offset) % 37
        marker = "►" if offset == 0 else " "
        items.append(f"{marker}{number_color_emoji(n)} {n:02d} {marker}")
    inner = "\n".join(items[around - 2: around + 3])  # 5 行
    return (
        "╔═══════════╗\n"
        f"{inner}\n"
        "╚═══════════╝"
    )


def render_roulette_strip(numbers: Iterable[int]) -> str:
    """以橫向跑馬燈方式顯示一段號碼序列，中間那個是當前停留位。"""
    nums = list(numbers)
    cells = [f"{number_color_emoji(n)}{n:02d}" for n in nums]
    mid = len(cells) // 2
    cells[mid] = f"[{cells[mid]}]"
    return " ".join(cells)


# -----------------------------------------------------------------------------
# 氣球（隨打氣變大）
# -----------------------------------------------------------------------------

_BALLOON_FRAMES = [
    # 0：未打氣
    """\
        
   .    
        
   |    """,
    # 1
    """\
   _    
  ( )   
   v    
   |    """,
    # 2
    """\
  ___   
 (   )  
  \\_/   
   v    
   |    """,
    # 3
    """\
  ___   
 /   \\  
(     ) 
 \\___/  
   v    
   |    """,
    # 4
    """\
  ____  
 /    \\ 
|      |
(      )
 \\____/ 
   v    
   |    """,
    # 5
    """\
  _____  
 /     \\ 
/       \\
|       |
\\       /
 \\_____/ 
    v    
    |    """,
    # 6
    """\
   ______   
  /      \\  
 /        \\ 
|          |
|          |
\\          /
 \\________/ 
     v      
     |      """,
    # 7（瀕臨爆破）
    """\
   _______    
  /       \\   
 /         \\  
|           | 
|     !     | 
|           | 
\\           / 
 \\_________/  
      v       
      |       """,
]


def render_balloon(pumps: int, danger: bool = False) -> str:
    idx = min(len(_BALLOON_FRAMES) - 1, pumps)
    art = _BALLOON_FRAMES[idx]
    if danger:
        art = art.replace("(", "/").replace(")", "\\")
    return art


def render_balloon_pop() -> str:
    return """\
    *  *  *
   *  💥💥  *
  *  💥💥💥  *
   *  💥💥  *
    *  *  *
       v
       |"""


# -----------------------------------------------------------------------------
# Crash 進度條 / 高度計
# -----------------------------------------------------------------------------

def crash_meter(multiplier: float, width: int = 20) -> str:
    """以對數刻度顯示倍率高度條，回傳兩行：條 + 標籤。"""
    if multiplier <= 1.0:
        ratio = 0.0
    else:
        ratio = math.log(multiplier, 10) / math.log(50, 10)
    ratio = max(0.0, min(1.0, ratio))
    filled = int(round(ratio * width))

    if multiplier < 1.5:
        block = "🟩"
    elif multiplier < 3.0:
        block = "🟨"
    elif multiplier < 10.0:
        block = "🟧"
    else:
        block = "🟥"

    bar = block * filled + "⬜" * (width - filled)
    return bar


def crash_rocket(multiplier: float) -> str:
    """火箭高度示意，依倍率把火箭往上推。"""
    height = 0
    if multiplier >= 1.5:
        height = 1
    if multiplier >= 2.0:
        height = 2
    if multiplier >= 3.0:
        height = 3
    if multiplier >= 5.0:
        height = 4
    if multiplier >= 10.0:
        height = 5
    if multiplier >= 25.0:
        height = 6

    rocket = "🚀"
    cloud = "☁️"
    star = "✨"
    layers = [
        f"{star}            ",
        f"   {star}        ",
        f"        {star}   ",
        f"     {cloud}      ",
        f"  {cloud}    {cloud} ",
        f"  ─────────  ",
    ]
    rocket_line_idx = max(0, len(layers) - 1 - height)
    out = layers.copy()
    out[rocket_line_idx] = "      " + rocket + "      "
    return "\n".join(out)
