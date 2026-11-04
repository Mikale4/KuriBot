"""Rendering summon banners and pull results."""

from __future__ import annotations

import io
import math
import sqlite3
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

import discord
from PIL import Image, ImageDraw

from kuribot.config import SUPPORT_CARD_IMAGE_PATH
from kuribot.models import SupportCardDef
from kuribot.render.fonts import FONT_S, FONT_S_BOLD, FONT_XL, FONT_XL_BOLD, FONT_XS, FONT_XS_BOLD
from kuribot.render.themes import element_color, get_ui_theme_palette
from kuribot.render.images import (
    open_binder_canvas_background, open_card_image, open_card_image_contain_tight,
    open_support_card_template, paste_binder_panel,
)
from kuribot.render.text import _fit_text_block_fonts, ellipsize_text, fit_wrapped_text, wrap_text
from kuribot.monsters import format_monster_types
from kuribot.render.collection import _draw_monster_stat_chip


def render_gacha_banner_featured(featured: Sequence[sqlite3.Row], user_name: str = "", theme_name: str = "gray") -> io.BytesIO:
    width, height = 960, 454
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_ui_theme_palette(theme_name)

    header = "Featured Monsters"
    subheader = f"{user_name}'s Summon Banner" if user_name else "Current Summon Banner"
    draw.text((34, 24), header, font=FONT_XL_BOLD, fill=palette["title"])
    draw.text((34, 72), subheader, font=FONT_S, fill=palette["subtitle"])

    outer = (24, 112, 936, 430)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    card_w = 192
    gap = 20
    total_card_width = (card_w * 4) + (gap * 3)
    start_x = outer[0] + max(16, ((outer[2] - outer[0]) - total_card_width) // 2)
    image_size = (150, 150)
    panel_top = outer[1] + 18

    for idx, row in enumerate(list(featured)[:4]):
        x = start_x + idx * (card_w + gap)
        card_box = (x, panel_top, x + card_w, outer[3] - 18)
        draw.rounded_rectangle(card_box, 24, fill=palette["panel_fill"], outline=palette["panel_outline"], width=3)

        center_x = x + card_w // 2
        attribute = str(row["attribute"])
        portrait_x = x + (card_w - image_size[0]) // 2
        portrait_y = panel_top + 34

        card_img = open_card_image(str(row["image_path"]), image_size, radius=18)
        img.paste(card_img, (portrait_x, portrait_y), card_img)
        draw.rounded_rectangle(
            (portrait_x, portrait_y, portrait_x + image_size[0], portrait_y + image_size[1]),
            18,
            outline=element_color(attribute),
            width=3,
        )

        badge_text = attribute
        tw = draw.textlength(badge_text, font=FONT_XS)
        badge_x1 = center_x - tw / 2 - 8
        badge_y1 = portrait_y - 18
        badge_x2 = center_x + tw / 2 + 8
        badge_y2 = portrait_y - 2
        draw.rounded_rectangle(
            (badge_x1, badge_y1, badge_x2, badge_y2),
            8,
            fill=palette["card_fill"],
            outline=element_color(attribute),
            width=2,
        )
        draw.text(
            ((badge_x1 + badge_x2) / 2, (badge_y1 + badge_y2) / 2 + 1),
            badge_text,
            font=FONT_XS,
            fill=(242, 242, 244),
            anchor="mm",
        )

        name_lines = wrap_text(draw, str(row["name"]), FONT_S_BOLD, card_w - 22)[:2]
        name_y = portrait_y + image_size[1] + 14
        for line_index, line in enumerate(name_lines):
            draw.text((center_x, name_y + line_index * 21), line, font=FONT_S_BOLD, fill=palette["title"], anchor="ma")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


def make_gacha_banner_file(featured: Sequence[sqlite3.Row], user_name: str = "", theme_name: str = "gray") -> Optional[discord.File]:
    if not featured:
        return None
    return discord.File(render_gacha_banner_featured(featured, user_name, theme_name), filename="banner_featured.png")


def render_gacha_results_file(pulls: Sequence[Dict[str, object]], user_name: str = "", theme_name: str = "gray") -> io.BytesIO:
    palette = get_ui_theme_palette(theme_name)
    pulls = list(pulls)
    count = len(pulls)
    cols = 1 if count <= 1 else (2 if count <= 4 else 5)
    rows = max(1, math.ceil(max(1, count) / cols))
    width = 360 if cols == 1 else 1536
    header_h = 110
    footer_h = 34
    gap_x = 16 if cols >= 5 else 20
    gap_y = 18 if cols >= 5 else 20
    outer_margin = 24
    tile_w = 300 if cols == 1 else 280
    tile_h = 430 if cols == 1 else (300 if cols <= 2 else 255)
    image_h = 218 if cols == 1 else (128 if cols <= 2 else 96)
    height = header_h + footer_h + outer_margin * 2 + rows * tile_h + max(0, rows - 1) * gap_y

    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    outer = (18, 18, width - 18, height - 18)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, 34, outline=palette["outer_outline"], width=3)

    title = f"{user_name}'s Summon Result" if count == 1 else f"{user_name}'s Summon x{count} Result"
    draw.text((34, 28), title, font=FONT_XL, fill=palette["title"])
    draw.text((34, 74), "", font=FONT_S, fill=palette["subtitle"])

    total_grid_w = cols * tile_w + max(0, cols - 1) * gap_x
    grid_left = outer[0] + ((outer[2] - outer[0]) - total_grid_w) / 2
    grid_top = header_h + outer_margin

    compact_layout = cols >= 5

    for idx, pulled in enumerate(pulls):
        col = idx % cols
        row = idx // cols
        x = int(round(grid_left + col * (tile_w + gap_x)))
        y = int(round(grid_top + row * (tile_h + gap_y)))
        tile_box = (x, y, x + tile_w, y + tile_h)

        attribute = str(pulled.get("attribute", ""))
        accent = element_color(attribute)
        monster_types = tuple(pulled.get("monster_types") or ())
        hp = int(pulled.get("hp", 0) or 0)
        atk = int(pulled.get("atk", 0) or 0)
        defense = int(pulled.get("def", 0) or 0)
        spd = int(pulled.get("spd", 0) or 0)

        draw.rounded_rectangle(tile_box, 20, fill=palette["card_fill"], outline=palette["panel_outline"], width=2)

        art_x = x + 14
        art_top_pad = 34 if not compact_layout else 32
        art_y = y + art_top_pad
        art_w = tile_w - 28
        card = open_card_image(str(pulled.get("image_path", "")), (art_w, image_h), radius=16)
        img.paste(card, (art_x, art_y), card)
        draw.rounded_rectangle((art_x, art_y, art_x + art_w, art_y + image_h), 16, outline=accent, width=3)

        badge_text = attribute or "?"
        badge_left = x + 14
        badge_right_pad = 14
        badge_inner_pad_x = 11
        badge_gap = 12
        badge_h = 20
        header_top = y + 10
        header_bottom = art_y - 8
        header_center_y = (header_top + header_bottom) / 2
        tw = draw.textlength(badge_text, font=FONT_XS_BOLD)
        badge_box = (
            badge_left,
            int(round(header_center_y - (badge_h / 2))),
            badge_left + int(math.ceil(tw)) + badge_inner_pad_x * 2,
            int(round(header_center_y + (badge_h / 2))),
        )
        draw.rounded_rectangle(badge_box, 8, fill=palette["panel_fill"], outline=accent, width=2)
        draw.text(((badge_box[0] + badge_box[2]) / 2, header_center_y), badge_text, font=FONT_XS_BOLD, fill=palette["title"], anchor="mm")

        stats_grid_gap_x = 8
        stats_grid_gap_y = 8
        stats_cols = 2
        stats_rows = 2
        stat_chip_h = 40 if not compact_layout else 36
        stats_grid_h = stats_rows * stat_chip_h + stats_grid_gap_y
        bottom_pad = 14
        text_side_pad = 14
        type_gap_after_art = 3 if not compact_layout else 2
        type_gap_before_stats = 4 if not compact_layout else 3

        stats_left = x + text_side_pad
        stats_width = tile_w - text_side_pad * 2
        chip_w = int((stats_width - stats_grid_gap_x) / stats_cols)

        content_top = art_y + image_h + type_gap_after_art
        content_bottom = y + tile_h - bottom_pad - stats_grid_h - type_gap_before_stats
        if content_bottom <= content_top:
            content_bottom = content_top + 4
        stats_top = y + tile_h - bottom_pad - stats_grid_h

        name_left = badge_box[2] + badge_gap
        name_right = x + tile_w - badge_right_pad
        name_max_width = max(10, name_right - name_left)
        name_font = FONT_S_BOLD if not compact_layout else FONT_XS_BOLD
        if draw.textlength(str(pulled.get("name", "Unknown")), font=name_font) > name_max_width:
            name_font = FONT_XS_BOLD
        name_text = ellipsize_text(draw, str(pulled.get("name", "Unknown")), name_font, name_max_width)
        draw.text((name_left, header_center_y), name_text, font=name_font, fill=palette["title"], anchor="lm")

        if not compact_layout:
            type_text = format_monster_types(monster_types)
            type_lines = fit_wrapped_text(draw, type_text, FONT_XS, tile_w - (text_side_pad * 2), 2)
            if type_lines:
                available_box = (x + text_side_pad, int(content_top), x + tile_w - text_side_pad, int(content_bottom))
                type_fonts, _ = _fit_text_block_fonts(draw, available_box, type_lines, [FONT_XS] * len(type_lines), line_gap=2, min_font_size=11)
                line_metrics = [draw.textbbox((0, 0), line, font=font, anchor="lt") for line, font in zip(type_lines, type_fonts)]
                line_heights = [bbox[3] - bbox[1] for bbox in line_metrics]
                line_gap = 2
                block_h = sum(line_heights) + max(0, len(type_lines) - 1) * line_gap
                center_x = (available_box[0] + available_box[2]) / 2
                start_y = available_box[1]
                max_stats_top = y + tile_h - bottom_pad - stats_grid_h
                stats_top = min(max_stats_top, int(math.ceil(start_y + block_h + type_gap_before_stats)))
                for line, font, line_h in zip(type_lines, type_fonts, line_heights):
                    draw.text((center_x, start_y + line_h / 2), line, font=font, fill=palette["title"], anchor="mm")
                    start_y += line_h + line_gap

        stat_items = (("HP", hp), ("ATK", atk), ("DEF", defense), ("SPD", spd))
        for stat_i, (label, value) in enumerate(stat_items):
            stat_row = stat_i // stats_cols
            stat_col = stat_i % stats_cols
            left = stats_left + stat_col * (chip_w + stats_grid_gap_x)
            top = stats_top + stat_row * (stat_chip_h + stats_grid_gap_y)
            _draw_monster_stat_chip(draw, (left, top, left + chip_w, top + stat_chip_h), label, value, palette["panel_fill"], accent, palette["title"])

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


def make_gacha_results_file(pulls: Sequence[Dict[str, object]], user_name: str = "", theme_name: str = "gray") -> Optional[discord.File]:
    if not pulls:
        return None
    return discord.File(render_gacha_results_file(pulls, user_name, theme_name), filename="pulls.png")


def _draw_pack_support_result_tile(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    tile_box: Tuple[int, int, int, int],
    card: SupportCardDef,
    palette: Dict[str, object],
):
    x, y, x2, y2 = tile_box
    tile_w = x2 - x
    tile_h = y2 - y
    accent = element_color(str(getattr(card, "attribute", "") or ""))
    draw.rounded_rectangle(tile_box, 20, fill=palette["card_fill"], outline=palette["panel_outline"], width=2)

    badge_text = str(getattr(card, "attribute", "") or "SUPPORT")
    badge_left = x + 14
    badge_h = 20
    header_center_y = y + 22
    badge_inner_pad_x = 11
    tw = draw.textlength(badge_text, font=FONT_XS_BOLD)
    badge_box = (
        badge_left,
        int(round(header_center_y - (badge_h / 2))),
        badge_left + int(math.ceil(tw)) + badge_inner_pad_x * 2,
        int(round(header_center_y + (badge_h / 2))),
    )
    draw.rounded_rectangle(badge_box, 8, fill=palette["panel_fill"], outline=accent, width=2)
    draw.text(((badge_box[0] + badge_box[2]) / 2, header_center_y), badge_text, font=FONT_XS_BOLD, fill=palette["title"], anchor="mm")

    name_left = badge_box[2] + 12
    name_right = x + tile_w - 14
    name_text = ellipsize_text(draw, str(getattr(card, "name", "Support Card") or "Support Card"), FONT_XS_BOLD, max(10, name_right - name_left))
    draw.text((name_left, header_center_y), name_text, font=FONT_XS_BOLD, fill=palette["title"], anchor="lm")

    stat_type = str(getattr(card, "stat_type", "") or "").upper()
    bonus_pct = int(getattr(card, "bonus_pct", 0) or 0)
    detail = "Special Support" if stat_type == "SPECIAL" else (f"+{bonus_pct}% {stat_type}" if bonus_pct else stat_type or "Support Card")
    detail = ellipsize_text(draw, detail, FONT_XS_BOLD, tile_w - 28)
    detail_box = (x + 14, y + tile_h - 42, x + tile_w - 14, y + tile_h - 14)

    art_w = min(tile_w - 40, 150)
    art_h = 190
    art_x = x + (tile_w - art_w) // 2
    art_region_top = int(round(header_center_y + (badge_h / 2) + 12))
    art_region_bottom = detail_box[1] - 12
    art_y = art_region_top
    if art_region_bottom > art_region_top:
        art_y = int(round(art_region_top + max(0, (art_region_bottom - art_region_top - art_h)) / 2))
    try:
        support_img = open_card_image_contain_tight(str(SUPPORT_CARD_IMAGE_PATH), (art_w, art_h), radius=14)
    except Exception:
        support_img = open_support_card_template((art_w, art_h), radius=14)
    paste_x = art_x + (art_w - support_img.width) // 2
    paste_y = art_y + (art_h - support_img.height) // 2
    img.paste(support_img, (paste_x, paste_y), support_img)
    draw.rounded_rectangle((paste_x, paste_y, paste_x + support_img.width, paste_y + support_img.height), 14, outline=accent, width=3)

    draw.rounded_rectangle(detail_box, 10, fill=palette["panel_fill"], outline=accent, width=2)
    draw.text(((detail_box[0] + detail_box[2]) / 2, (detail_box[1] + detail_box[3]) / 2), detail, font=FONT_XS_BOLD, fill=palette["title"], anchor="mm")


def render_pack_opening_results_file(result: Dict[str, object], user_name: str = "", theme_name: str = "gray") -> io.BytesIO:
    palette = get_ui_theme_palette(theme_name)
    monsters = list(result.get("monsters", []) or [])[:3]
    support_cards = list(result.get("support_cards", []) or [])[:3]

    width = 1080
    header_h = 106
    footer_h = 30
    outer_margin = 24
    gap_x = 18
    gap_y = 20
    tile_w = 320
    monster_tile_h = 330
    support_tile_h = 312
    rows_h = monster_tile_h + gap_y + support_tile_h
    height = header_h + footer_h + outer_margin * 2 + rows_h

    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    outer = (18, 18, width - 18, height - 18)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, 34, outline=palette["outer_outline"], width=3)

    draw.text((34, 28), "Pack Opening Result", font=FONT_XL, fill=palette["title"])
    pack_name = str(result.get("pack_name") or "Pack")
    subtitle = f"{user_name} • {pack_name} • 3 Monsters + 3 Support Cards" if user_name else f"{pack_name} • 3 Monsters + 3 Support Cards"
    draw.text((34, 74), subtitle, font=FONT_S, fill=palette["subtitle"])

    total_grid_w = 3 * tile_w + 2 * gap_x
    grid_left = outer[0] + int(((outer[2] - outer[0]) - total_grid_w) / 2)
    monster_y = header_h + outer_margin
    support_y = monster_y + monster_tile_h + gap_y

    for idx, pulled in enumerate(monsters):
        x = grid_left + idx * (tile_w + gap_x)
        y = monster_y
        tile_box = (x, y, x + tile_w, y + monster_tile_h)
        attribute = str(pulled.get("attribute", ""))
        accent = element_color(attribute)
        monster_types = tuple(pulled.get("monster_types") or ())
        hp = int(pulled.get("hp", 0) or 0)
        atk = int(pulled.get("atk", 0) or 0)
        defense = int(pulled.get("def", 0) or 0)
        spd = int(pulled.get("spd", 0) or 0)

        draw.rounded_rectangle(tile_box, 20, fill=palette["card_fill"], outline=palette["panel_outline"], width=2)

        badge_text = attribute or "?"
        badge_left = x + 14
        badge_h = 20
        header_center_y = y + 22
        badge_inner_pad_x = 11
        tw = draw.textlength(badge_text, font=FONT_XS_BOLD)
        badge_box = (
            badge_left,
            int(round(header_center_y - (badge_h / 2))),
            badge_left + int(math.ceil(tw)) + badge_inner_pad_x * 2,
            int(round(header_center_y + (badge_h / 2))),
        )
        draw.rounded_rectangle(badge_box, 8, fill=palette["panel_fill"], outline=accent, width=2)
        draw.text(((badge_box[0] + badge_box[2]) / 2, header_center_y), badge_text, font=FONT_XS_BOLD, fill=palette["title"], anchor="mm")

        name_left = badge_box[2] + 12
        name_right = x + tile_w - 14
        name_font = FONT_S_BOLD
        if draw.textlength(str(pulled.get("name", "Unknown")), font=name_font) > max(10, name_right - name_left):
            name_font = FONT_XS_BOLD
        name_text = ellipsize_text(draw, str(pulled.get("name", "Unknown")), name_font, max(10, name_right - name_left))
        draw.text((name_left, header_center_y), name_text, font=name_font, fill=palette["title"], anchor="lm")

        art_x = x + 14
        art_y = y + 42
        art_w = tile_w - 28
        image_h = 150
        card_img = open_card_image(str(pulled.get("image_path", "")), (art_w, image_h), radius=16)
        img.paste(card_img, (art_x, art_y), card_img)
        draw.rounded_rectangle((art_x, art_y, art_x + art_w, art_y + image_h), 16, outline=accent, width=3)

        stat_chip_h = 34
        stats_grid_gap_x = 8
        stats_grid_gap_y = 8
        text_side_pad = 14
        stats_left = x + text_side_pad
        stats_width = tile_w - text_side_pad * 2
        chip_w = int((stats_width - stats_grid_gap_x) / 2)
        stats_top = y + monster_tile_h - 14 - (2 * stat_chip_h + stats_grid_gap_y)

        type_text = format_monster_types(monster_types)
        type_lines = fit_wrapped_text(draw, type_text, FONT_XS, tile_w - 28, 2)
        if type_lines:
            type_line_gap = 3
            type_line_heights = []
            for line in type_lines:
                bbox = draw.textbbox((0, 0), line, font=FONT_XS, anchor="lt")
                type_line_heights.append(max(1, bbox[3] - bbox[1]))
            type_block_h = sum(type_line_heights) + max(0, len(type_lines) - 1) * type_line_gap
            type_region_top = art_y + image_h + 8
            type_region_bottom = stats_top - 8
            type_y = type_region_top
            if type_region_bottom > type_region_top:
                type_y = type_region_top + max(0, (type_region_bottom - type_region_top - type_block_h)) / 2
            for line, line_h in zip(type_lines, type_line_heights):
                draw.text((x + tile_w / 2, type_y + line_h / 2), line, font=FONT_XS, fill=palette["title"], anchor="mm")
                type_y += line_h + type_line_gap
        for stat_i, (label, value) in enumerate((("HP", hp), ("ATK", atk), ("DEF", defense), ("SPD", spd))):
            stat_row = stat_i // 2
            stat_col = stat_i % 2
            left = stats_left + stat_col * (chip_w + stats_grid_gap_x)
            top = stats_top + stat_row * (stat_chip_h + stats_grid_gap_y)
            _draw_monster_stat_chip(draw, (left, top, left + chip_w, top + stat_chip_h), label, value, palette["panel_fill"], accent, palette["title"])

    for idx, card in enumerate(support_cards):
        x = grid_left + idx * (tile_w + gap_x)
        _draw_pack_support_result_tile(img, draw, (x, support_y, x + tile_w, support_y + support_tile_h), card, palette)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


def make_single_summon_portrait_file(pulls: Sequence[Dict[str, object]]) -> Optional[discord.File]:
    if not pulls:
        return None
    image_path = str(pulls[0].get("image_path", "") or "")
    if not image_path or not Path(image_path).exists():
        return None
    return discord.File(image_path, filename="pull.png")


def make_boss_cutscene_file(pulls: Sequence[Dict[str, object]]) -> Optional[discord.File]:
    for pull in pulls:
        if str(pull.get("monster_category") or "").strip().lower() != "boss":
            continue
        image_path = str(pull.get("image_path") or "")
        if image_path and Path(image_path).exists():
            return discord.File(image_path, filename="boss_cutscene.png")
    return None
