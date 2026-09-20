from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple
from uuid import uuid4

import discord
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
ASSET_DIR = ROOT / "assets"
CARD_DIR = ASSET_DIR / "cards"
DICE_DIR = ASSET_DIR / "dice"

CARD_SIZE = (180, 252)
DIE_SIZE = (128, 128)

SUIT_TO_CODE = {
    "\u2660": "S",
    "\u2665": "H",
    "\u2666": "D",
    "\u2663": "C",
}

SUIT_META = {
    "S": ("\u2660", (35, 39, 47)),
    "H": ("\u2665", (200, 38, 62)),
    "D": ("\u2666", (200, 38, 62)),
    "C": ("\u2663", (35, 39, 47)),
}

RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates.extend(
            [
                "C:/Windows/Fonts/arialbd.ttf",
                "C:/Windows/Fonts/seguisb.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ]
        )
    candidates.extend(
        [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/seguisym.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _center_text(
    draw: ImageDraw.ImageDraw,
    xy: Tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: Tuple[int, int, int],
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    draw.text((xy[0] - w / 2, xy[1] - h / 2), text, font=font, fill=fill)


def _card_path(rank: str, suit: str) -> Path:
    suit_code = SUIT_TO_CODE.get(suit, suit)
    return CARD_DIR / f"{rank}{suit_code}.png"


def _make_card(rank: str, suit_code: str) -> Image.Image:
    w, h = CARD_SIZE
    img = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    shadow = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((8, 10, w - 5, h - 4), radius=18, fill=(0, 0, 0, 45))
    img.alpha_composite(shadow)

    draw.rounded_rectangle((5, 4, w - 9, h - 10), radius=18, fill=(250, 248, 240), outline=(204, 191, 168), width=3)
    draw.rounded_rectangle((15, 14, w - 19, h - 20), radius=13, outline=(230, 219, 199), width=2)

    symbol, color = SUIT_META[suit_code]
    rank_font = _font(36, bold=True)
    suit_font = _font(42, bold=True)
    center_font = _font(96, bold=True)

    draw.text((23, 18), rank, font=rank_font, fill=color)
    draw.text((24, 55), symbol, font=suit_font, fill=color)

    small = Image.new("RGBA", (68, 92), (0, 0, 0, 0))
    small_draw = ImageDraw.Draw(small)
    small_draw.text((0, 0), rank, font=rank_font, fill=color)
    small_draw.text((2, 37), symbol, font=suit_font, fill=color)
    small = small.rotate(180, expand=True)
    img.alpha_composite(small, (w - 91, h - 109))

    _center_text(draw, (w // 2, h // 2 + 4), symbol, center_font, color)
    return img


def _make_card_back() -> Image.Image:
    w, h = CARD_SIZE
    img = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((7, 9, w - 4, h - 4), radius=18, fill=(0, 0, 0, 48))
    draw.rounded_rectangle((5, 4, w - 9, h - 10), radius=18, fill=(29, 76, 138), outline=(205, 179, 92), width=3)
    draw.rounded_rectangle((18, 17, w - 22, h - 23), radius=12, outline=(239, 217, 145), width=3)
    for offset in range(-h, w, 24):
        draw.line((offset, h - 22, offset + h, 17), fill=(54, 106, 174), width=8)
    draw.rounded_rectangle((45, 72, w - 49, h - 78), radius=12, fill=(20, 54, 108), outline=(239, 217, 145), width=3)
    cx, cy = w // 2, h // 2
    points = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        radius = 35 if i % 2 == 0 else 15
        points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    draw.polygon(points, fill=(239, 217, 145))
    draw.line(points + [points[0]], fill=(255, 246, 196), width=2)
    return img


def _make_die(face: Optional[int]) -> Image.Image:
    img = Image.new("RGBA", DIE_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    w, h = DIE_SIZE
    draw.rounded_rectangle((9, 12, w - 6, h - 3), radius=22, fill=(0, 0, 0, 48))
    draw.rounded_rectangle((7, 6, w - 9, h - 10), radius=22, fill=(246, 244, 236), outline=(213, 200, 176), width=3)
    draw.rounded_rectangle((16, 15, w - 18, h - 19), radius=17, outline=(255, 255, 255), width=2)

    if face is None:
        _center_text(draw, (w // 2, h // 2 - 2), "?", _font(72, bold=True), (65, 76, 92))
        return img

    pos = {
        "tl": (39, 39),
        "tc": (64, 39),
        "tr": (89, 39),
        "ml": (39, 64),
        "mc": (64, 64),
        "mr": (89, 64),
        "bl": (39, 89),
        "bc": (64, 89),
        "br": (89, 89),
    }
    patterns = {
        1: ("mc",),
        2: ("tl", "br"),
        3: ("tl", "mc", "br"),
        4: ("tl", "tr", "bl", "br"),
        5: ("tl", "tr", "mc", "bl", "br"),
        6: ("tl", "tr", "ml", "mr", "bl", "br"),
    }
    for key in patterns[face]:
        x, y = pos[key]
        draw.ellipse((x - 9, y - 9, x + 9, y + 9), fill=(36, 41, 50))
    return img


def generate_assets() -> None:
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    DICE_DIR.mkdir(parents=True, exist_ok=True)
    for suit_code in SUIT_META:
        for rank in RANKS:
            _make_card(rank, suit_code).save(CARD_DIR / f"{rank}{suit_code}.png")
    _make_card_back().save(CARD_DIR / "back.png")
    _make_die(None).save(DICE_DIR / "rolling.png")
    for face in range(1, 7):
        _make_die(face).save(DICE_DIR / f"{face}.png")


def ensure_assets() -> None:
    required = [CARD_DIR / "AS.png", CARD_DIR / "10H.png", CARD_DIR / "back.png", DICE_DIR / "1.png", DICE_DIR / "rolling.png"]
    if not all(path.exists() for path in required):
        generate_assets()


def _open_card(rank: str, suit: str) -> Image.Image:
    ensure_assets()
    return Image.open(_card_path(rank, suit)).convert("RGBA")


def _open_die(face: Optional[int]) -> Image.Image:
    ensure_assets()
    name = "rolling.png" if face is None else f"{face}.png"
    return Image.open(DICE_DIR / name).convert("RGBA")


def _table_background(size: Tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", size, (16, 78, 62, 255))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        shade = int(22 * math.sin(y / h * math.pi))
        draw.line((0, y, w, y), fill=(16 + shade, 78 + shade, 62 + shade, 255))
    draw.rounded_rectangle((18, 18, w - 18, h - 18), radius=28, outline=(213, 180, 91, 120), width=3)
    return img


def render_baccarat_table(
    player_cards: Sequence[Tuple[str, str]],
    banker_cards: Sequence[Tuple[str, str]],
    player_pending: int = 0,
    banker_pending: int = 0,
    player_total: Optional[int] = None,
    banker_total: Optional[int] = None,
) -> Image.Image:
    ensure_assets()
    card_w, card_h = CARD_SIZE
    gap = 22
    max_cards = max(3, len(player_cards) + player_pending, len(banker_cards) + banker_pending)
    width = 80 + max_cards * card_w + (max_cards - 1) * gap
    height = 650
    img = _table_background((width, height))
    draw = ImageDraw.Draw(img)
    title_font = _font(34, bold=True)
    meta_font = _font(24, bold=True)

    rows = [
        ("PLAYER", player_cards, player_pending, player_total, 70),
        ("BANKER", banker_cards, banker_pending, banker_total, 365),
    ]
    for label, cards, pending, total, y in rows:
        header = label if total is None else f"{label}   TOTAL {total}"
        draw.text((42, y - 42), header, font=title_font, fill=(247, 235, 204))
        all_cards: list[Image.Image] = [_open_card(rank, suit) for rank, suit in cards]
        all_cards.extend(Image.open(CARD_DIR / "back.png").convert("RGBA") for _ in range(pending))
        for i, card in enumerate(all_cards):
            x = 42 + i * (card_w + gap)
            img.alpha_composite(card, (x, y))
        if not all_cards:
            draw.text((42, y + card_h // 2 - 12), "WAITING", font=meta_font, fill=(212, 224, 213))
    return img


def render_dice_table(faces: Iterable[Optional[int]]) -> Image.Image:
    ensure_assets()
    faces = list(faces)
    gap = 34
    width = 90 + len(faces) * DIE_SIZE[0] + max(0, len(faces) - 1) * gap
    height = 230
    img = _table_background((width, height))
    draw = ImageDraw.Draw(img)
    draw.text((38, 28), "DICE ROLL", font=_font(30, bold=True), fill=(247, 235, 204))
    for i, face in enumerate(faces):
        die = _open_die(face)
        x = 45 + i * (DIE_SIZE[0] + gap)
        img.alpha_composite(die, (x, 78))
    return img


def image_file(image: Image.Image, prefix: str) -> discord.File:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    filename = f"{prefix}_{uuid4().hex}.png"
    return discord.File(buffer, filename=filename)
