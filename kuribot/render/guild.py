"""Rendering guild boss and guild shop panels."""

from __future__ import annotations

import io
import sqlite3
from typing import Optional

from PIL import Image, ImageDraw

from kuribot.config import (
    GUILD_BOSS_MAX_PARTICIPATION_CHANCES, GUILD_BOSS_MAX_ROUNDS,
    GUILD_BOSS_SUPPORT_DISABLED_ROUNDS, GUILD_SHOP_ITEMS,
)
from kuribot.timeutil import reset_schedule_text
from kuribot.database import db
from kuribot.render.fonts import (
    FONT_L_BOLD, FONT_M, FONT_M_BOLD, FONT_S, FONT_XL_BOLD, FONT_XS_BOLD, get_bold_font,
)
from kuribot.render.images import open_card_image_square_cover
from kuribot.render.text import (
    _fit_text_block_fonts, draw_evenly_spaced_text_block, ellipsize_text, fit_wrapped_text,
)
from kuribot.monsters import decode_monster_types, format_monster_types
from kuribot.render.battle import hp_percent_text


def render_guild_boss_panel(guild_name: str, boss_row: sqlite3.Row, participants: int = 0, attempts_left: Optional[int] = None) -> io.BytesIO:
    width, height = 1120, 720
    img = Image.new("RGBA", (width, height), (18, 18, 24, 255))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        shade = int(18 + (y / height) * 28)
        draw.line((0, y, width, y), fill=(shade, 18, 34 + shade // 2, 255))
    draw.rounded_rectangle((28, 28, width - 28, height - 28), 32, outline=(225, 185, 70, 255), width=4, fill=(25, 25, 34, 225))

    title = ellipsize_text(draw, f"{guild_name} • Guild Boss", FONT_XL_BOLD, width - 112)
    draw.text((56, 54), title, font=FONT_XL_BOLD, fill=(255, 230, 140, 255))
    draw.text((58, 104), "Guild members must defeat this boss together.", font=FONT_M, fill=(235, 235, 245, 255))

    # Tightly wrapped 1:1 boss portrait. ImageOps.fit fills the square instead of letterboxing with black padding.
    art_box = (64, 156, 484, 576)
    art_size = (art_box[2] - art_box[0], art_box[3] - art_box[1])
    try:
        boss_art = open_card_image_square_cover(str(boss_row["image_path"] or ""), art_size, radius=26)
    except Exception:
        boss_art = Image.new("RGBA", art_size, (60, 60, 70, 255))
    img.alpha_composite(boss_art, (art_box[0], art_box[1]))
    draw.rounded_rectangle(art_box, 26, outline=(255, 215, 90, 255), width=4)

    current_hp = max(0, int(boss_row["current_hp"] or 0))
    max_hp = max(1, int(boss_row["max_hp"] or 1))
    hp_ratio = max(0.0, min(1.0, current_hp / max_hp))
    info_x = 530
    info_right = width - 78
    text_width = info_right - info_x

    boss_name_lines = fit_wrapped_text(draw, str(boss_row["monster_name"]), FONT_XL_BOLD, text_width, 2) or [str(boss_row["monster_name"])]
    name_y = 164
    for line in boss_name_lines:
        draw.text((info_x, name_y), line, font=FONT_XL_BOLD, fill=(255, 255, 255, 255))
        name_y += 44
    meta_text = f"{boss_row['monster_attribute']} • {format_monster_types(decode_monster_types(str(boss_row['monster_types'] or '')))}"
    draw.text((info_x, name_y + 8), ellipsize_text(draw, meta_text, FONT_M, text_width), font=FONT_M, fill=(205, 210, 230, 255))

    hp_y = max(278, name_y + 62)
    draw.text((info_x, hp_y), f"HP: {current_hp:,} / {max_hp:,}", font=FONT_L_BOLD, fill=(255, 235, 170, 255))
    bar = (info_x, hp_y + 52, info_right, hp_y + 98)
    draw.rounded_rectangle(bar, 20, fill=(55, 22, 35, 255), outline=(155, 75, 90, 255), width=2)
    fill_w = int((bar[2] - bar[0]) * hp_ratio)
    if fill_w > 0:
        draw.rounded_rectangle((bar[0], bar[1], bar[0] + fill_w, bar[3]), 20, fill=(210, 55, 76, 255))
    draw.text(((bar[0] + bar[2]) // 2, (bar[1] + bar[3]) // 2), hp_percent_text(current_hp, max_hp), font=FONT_M_BOLD, fill=(255, 255, 255, 255), anchor="mm")

    if attempts_left is None:
        attempts_text = f"Attempts left: {GUILD_BOSS_MAX_PARTICIPATION_CHANCES}"
    else:
        attempts_text = f"Attempts left: {max(0, int(attempts_left))}"

    stats = [
        f"ATK: {int(boss_row['atk']):,} • HP: {max_hp:,}",
        "No hearts • No deck • Starts with IMMUNITY",
        f"Support disabled for rounds 1-{GUILD_BOSS_SUPPORT_DISABLED_ROUNDS}",
        "Every 2 rounds: cleanse; heal 15% only if debuffs removed",
        "Every 2 rounds: STUN chance",
        f"Max battle length: {GUILD_BOSS_MAX_ROUNDS} rounds",
        attempts_text,
    ]

    # Keep every badge and every badge label fully inside the main gold panel.
    # The lower rules area is tight on long boss names, so badge height, gap,
    # and font size are all calculated from the remaining inner panel space.
    panel_bottom = height - 72
    y = min(bar[3] + 18, panel_bottom - 190)
    y = max(bar[3] + 8, y)
    badge_gap = 5
    min_badge_h = 26
    available_h = max(min_badge_h * len(stats), panel_bottom - y)
    badge_h = max(min_badge_h, min(38, int((available_h - (badge_gap * (len(stats) - 1))) / max(1, len(stats)))))
    total_badges_h = (badge_h * len(stats)) + (badge_gap * (len(stats) - 1))
    if y + total_badges_h > panel_bottom:
        y = max(bar[3] + 6, panel_bottom - total_badges_h)
    if y + total_badges_h > panel_bottom:
        badge_gap = 3
        badge_h = max(22, int((panel_bottom - y - (badge_gap * (len(stats) - 1))) / max(1, len(stats))))
        total_badges_h = (badge_h * len(stats)) + (badge_gap * (len(stats) - 1))
        if y + total_badges_h > panel_bottom:
            y = max(bar[3] + 4, panel_bottom - total_badges_h)

    def _badge_font_for(line: str, box_w: int, box_h: int):
        for size in (17, 16, 15, 14, 13, 12, 11, 10):
            font = get_bold_font(size)
            candidate = ellipsize_text(draw, line, font, box_w)
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if (bbox[3] - bbox[1]) <= max(6, box_h - 8):
                return font, candidate, bbox
        font = get_bold_font(10)
        candidate = ellipsize_text(draw, line, font, box_w)
        return font, candidate, draw.textbbox((0, 0), candidate, font=font)

    for line in stats:
        badge_bottom = min(panel_bottom, y + badge_h)
        draw.rounded_rectangle((info_x, y, info_right, badge_bottom), 12, fill=(38, 38, 50, 230))
        box_w = text_width - 36
        box_h = max(1, badge_bottom - y)
        badge_font, badge_text, bbox = _badge_font_for(line, box_w, box_h)
        text_h = bbox[3] - bbox[1]
        text_y = y + ((box_h - text_h) / 2) - bbox[1]
        draw.text((info_x + 18, text_y), badge_text, font=badge_font, fill=(238, 238, 248, 255))
        y += badge_h + badge_gap
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", compress_level=3)
    buf.seek(0)
    return buf


def guild_boss_panel_text(owner_id: int) -> str:
    membership = db.get_user_guild_membership(owner_id)
    if not membership:
        return "You are not in a guild yet."
    boss = db.get_or_create_guild_boss(int(membership["guild_id"]))
    if boss is None:
        return "No monsters are available to create a Guild Boss."
    return (
        f"**{membership['name']} Guild Boss**\n"
        f"Defeat **{boss['monster_name']}** together. Current HP: **{int(boss['current_hp']):,}/{int(boss['max_hp']):,}**."
    )


def render_guild_shop_page(user_name: str, owner_id: int) -> io.BytesIO:
    user_row = db.get_user(owner_id)
    width, height = 1120, 720
    img = Image.new("RGBA", (width, height), (18, 20, 28, 255))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        shade = int(18 + (y / height) * 24)
        draw.line((0, y, width, y), fill=(18, shade, 34 + shade // 2, 255))
    draw.rounded_rectangle((28, 28, width - 28, height - 28), 32, outline=(90, 210, 150, 255), width=4, fill=(25, 28, 36, 230))
    draw.text((56, 54), f"{user_name}'s Guild Shop", font=FONT_XL_BOLD, fill=(225, 255, 235, 255))
    draw.text((58, 106), f"Guild Coins: {int(user_row['guild_coins'] or 0):,} • {reset_schedule_text()}", font=FONT_M, fill=(220, 235, 230, 255))
    y = 168
    for idx, item in enumerate(GUILD_SHOP_ITEMS, start=1):
        sold = db.is_guild_shop_item_sold_out(owner_id, str(item['key']))
        box = (64, y, width - 64, y + 108)
        outline = (120, 120, 130, 255) if sold else (105, 220, 160, 255)
        draw.rounded_rectangle(box, 22, fill=(38, 42, 52, 235), outline=outline, width=3)
        badge = "SOLD OUT" if sold else f"{int(item['cost'])} GC"
        draw.rounded_rectangle((box[0] + 18, box[1] + 18, box[0] + 158, box[1] + 52), 14, fill=(24, 30, 36, 255), outline=outline, width=2)
        draw.text((box[0] + 88, box[1] + 36), badge, font=FONT_XS_BOLD, fill=(245, 245, 245, 255), anchor="mm")
        content_box = (box[0] + 184, box[1] + 14, box[2] - 22, box[3] - 14)
        title = ellipsize_text(draw, f"{idx}. {item['name']}", FONT_L_BOLD, content_box[2] - content_box[0])
        desc_lines = fit_wrapped_text(draw, str(item['description']), FONT_S, content_box[2] - content_box[0], 2)
        info_lines = [title, *desc_lines]
        fonts = [FONT_L_BOLD, *([FONT_S] * len(desc_lines))]
        fills = [(255, 255, 255, 255), *([(210, 220, 220, 255)] * len(desc_lines))]
        fitted_fonts, _ = _fit_text_block_fonts(draw, content_box, info_lines, fonts, line_gap=6, min_font_size=12)
        draw_evenly_spaced_text_block(draw, content_box, info_lines, fitted_fonts, fills, min_gap=5)
        y += 122
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", compress_level=3)
    buf.seek(0)
    return buf
