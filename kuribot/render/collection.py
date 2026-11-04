"""Rendering binder pages, support cards, support decks and teams."""

from __future__ import annotations

import io
import math
import sqlite3
from typing import List, Sequence, Tuple

from PIL import Image, ImageDraw

from kuribot.config import (
    BINDER_PANEL_ALPHA, SUPPORT_DECK_SIZE, TEAM_ACTIVE_MODE_HIGHLIGHT_COLORS,
    TEAM_ACTIVE_MODE_LABELS,
)
from kuribot.database import db
from kuribot.render.fonts import FONT_M_BOLD, FONT_S, FONT_S_BOLD, FONT_XL, FONT_XS, FONT_XS_BOLD
from kuribot.render.themes import element_color, get_ui_theme_palette
from kuribot.render.images import (
    apply_rounded_mask, open_binder_canvas_background, open_binder_panel_background,
    open_card_image, open_card_image_contain, open_support_card_template, paste_binder_panel,
)
from kuribot.render.text import ellipsize_text, fit_wrapped_text, wrap_text
from kuribot.monsters import decode_monster_types, resolve_row_combat_stats
from kuribot.cards.support_cards import support_card_compact_label


def _draw_monster_stat_chip(draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], label: str, value: object, fill: Tuple[int, int, int, int], outline: Tuple[int, int, int], text_fill: Tuple[int, int, int]):
    draw.rounded_rectangle(box, 10, fill=fill, outline=outline, width=2)
    cx = (box[0] + box[2]) / 2
    cy = (box[1] + box[3]) / 2

    label_text = str(label)
    value_text = str(value)
    label_bbox = draw.textbbox((0, 0), label_text, font=FONT_XS_BOLD, anchor="lt")
    value_bbox = draw.textbbox((0, 0), value_text, font=FONT_S_BOLD, anchor="lt")
    label_h = label_bbox[3] - label_bbox[1]
    value_h = value_bbox[3] - value_bbox[1]
    line_gap = 4
    content_h = label_h + line_gap + value_h
    content_top = cy - (content_h / 2)

    label_center_y = content_top + (label_h / 2)
    value_center_y = content_top + label_h + line_gap + (value_h / 2)

    draw.text((cx, label_center_y), label_text, font=FONT_XS_BOLD, fill=text_fill, anchor="mm")
    draw.text((cx, value_center_y), value_text, font=FONT_S_BOLD, fill=text_fill, anchor="mm")


def render_monster_binder_page(user_name: str, inventory_rows: List[sqlite3.Row], page: int, per_page: int = 9, filter_label: str = "All Monsters", sort_label: str = "Name A-Z", theme_name: str = "gray") -> io.BytesIO:
    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_ui_theme_palette(theme_name)

    title = f"{user_name}'s Monsters"
    draw.text((34, 28), title, font=FONT_XL, fill=palette["title"])

    total_pages = max(1, math.ceil(max(1, len(inventory_rows)) / per_page))
    page = max(1, min(page, total_pages))
    draw.text((34, 76), f"Filter: {filter_label} • Sort: {sort_label} • Page {page}/{total_pages}", font=FONT_S, fill=palette["subtitle"])

    outer = (24, 112, 936, 934)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    start = (page - 1) * per_page
    items = inventory_rows[start:start + per_page]

    grid_padding_x = 24
    grid_padding_y = 20
    grid_left = outer[0] + grid_padding_x
    grid_top = outer[1] + grid_padding_y
    grid_width = (outer[2] - outer[0]) - grid_padding_x * 2
    grid_height = (outer[3] - outer[1]) - grid_padding_y * 2
    cell_width = grid_width / 3
    cell_height = grid_height / 3
    card_margin_x = 10
    card_margin_y = 10
    tile_w = int(cell_width - card_margin_x * 2)
    tile_h = int(cell_height - card_margin_y * 2)
    image_w = tile_w - 28
    image_h = 92
    text_side_pad = 14
    stats_grid_gap_x = 8
    stats_grid_gap_y = 8
    stats_cols = 2
    stats_rows = 2
    stat_chip_h = 40
    stats_grid_h = stats_rows * stat_chip_h + stats_grid_gap_y
    bottom_pad = 14
    art_top_pad = 34
    text_gap_after_art = 12
    text_gap_before_stats = 16

    for idx, row in enumerate(items):
        col = idx % 3
        row_idx = idx // 3
        slot_left = grid_left + col * cell_width
        slot_top = grid_top + row_idx * cell_height
        x = int(round(slot_left + card_margin_x))
        y = int(round(slot_top + card_margin_y))
        tile_box = (x, y, x + tile_w, y + tile_h)

        card_name = str(row["monster_name"])
        attribute = str(row["attribute"])
        monster_types = decode_monster_types(row["monster_types"])
        hp, atk, defense, spd = resolve_row_combat_stats(row, monster_types)
        accent = element_color(attribute)

        draw.rounded_rectangle(tile_box, 20, fill=palette["card_fill"], outline=palette["panel_outline"], width=2)

        art_x = x + 14
        art_y = y + art_top_pad
        card = open_card_image(row["image_path"], (image_w, image_h), radius=16)
        img.paste(card, (art_x, art_y), card)
        draw.rounded_rectangle((art_x, art_y, art_x + image_w, art_y + image_h), 16, outline=accent, width=3)

        badge_text = attribute
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

        stats_top = y + tile_h - bottom_pad - stats_grid_h
        stats_left = x + text_side_pad
        stats_width = tile_w - text_side_pad * 2
        chip_w = int((stats_width - stats_grid_gap_x) / stats_cols)

        content_top = art_y + image_h + text_gap_after_art
        content_bottom = stats_top - text_gap_before_stats
        if content_bottom <= content_top:
            content_bottom = content_top + 4

        name_left = badge_box[2] + badge_gap
        name_right = x + tile_w - badge_right_pad
        name_max_width = max(10, name_right - name_left)
        name_font = FONT_S_BOLD
        if draw.textlength(card_name, font=name_font) > name_max_width:
            name_font = FONT_XS_BOLD
        name_text = ellipsize_text(draw, card_name, name_font, name_max_width)
        name_y = header_center_y
        draw.text((name_left, name_y), name_text, font=name_font, fill=palette["title"], anchor="lm")

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


def render_support_card_page(user_name: str, card_rows: List[dict], page: int, per_page: int = 8, filter_label: str = "All Cards", sort_label: str = "Name A-Z", theme_name: str = "gray", show_obtained: bool = True) -> io.BytesIO:
    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_ui_theme_palette(theme_name)

    title = f"{user_name}'s Cards"
    draw.text((34, 28), title, font=FONT_XL, fill=palette["title"])

    total_pages = max(1, math.ceil(max(1, len(card_rows)) / per_page))
    page = max(1, min(page, total_pages))
    draw.text((34, 76), f"Filter: {filter_label} • Sort: {sort_label} • Page {page}/{total_pages}", font=FONT_S, fill=palette["subtitle"])

    outer = (24, 112, 936, 934)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    start = (page - 1) * per_page
    items = card_rows[start:start + per_page]
    x_positions = [48, 446]
    y_positions = [138, 318, 498]
    art_size = (96, 120)

    for idx, row in enumerate(items):
        col = idx % 2
        row_idx = idx // 2
        base_x = x_positions[col]
        base_y = y_positions[row_idx]
        box = (base_x, base_y, base_x + 360, base_y + 150)
        attribute = row["attribute"]
        card = row["card"]

        draw.rounded_rectangle(box, 18, fill=palette["card_fill"], outline=element_color(attribute), width=3)
        art = open_support_card_template(art_size, radius=18)
        overlay = Image.new("RGBA", art_size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle((0, 0, art_size[0]-1, art_size[1]-1), 16, outline=element_color(attribute) + (255,), width=3)
        art = apply_rounded_mask(Image.alpha_composite(art, overlay), 18)
        img.paste(art, (base_x + 12, base_y + 12), art)

        text_x = base_x + 146
        text_width = box[2] - text_x - 14
        name_lines = fit_wrapped_text(draw, card.name, FONT_M_BOLD, text_width, 2)
        for i, line in enumerate(name_lines):
            draw.text((text_x, base_y + 12 + i * 22), line, font=FONT_M_BOLD, fill=palette["title"])

        meta_y = base_y + 12 + max(1, len(name_lines)) * 22 + 4
        compact_label = ellipsize_text(draw, support_card_compact_label(card), FONT_XS_BOLD, text_width)
        draw.text((text_x, meta_y), compact_label, font=FONT_XS_BOLD, fill=element_color(attribute))

        desc_top = meta_y + 26
        obtained_y = base_y + 138
        desc_bottom = obtained_y - 6 if show_obtained else box[3] - 12
        desc_height = max(18, desc_bottom - desc_top)
        desc_line_height = 18
        max_desc_lines = max(1, desc_height // desc_line_height)
        desc_lines = fit_wrapped_text(draw, card.description, FONT_XS, text_width, max_desc_lines)
        for i, line in enumerate(desc_lines):
            draw.text((text_x, desc_top + i * desc_line_height), line, font=FONT_XS, fill=palette["info_text"])

        if show_obtained:
            obtained_text = f"Obtained {row['obtained_label']}"
            obtained_line = ellipsize_text(draw, obtained_text, FONT_XS, text_width)
            draw.text((text_x, obtained_y), obtained_line, font=FONT_XS, fill=palette["empty_text"])

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


def team_active_mode_highlight_colors(active_modes: Sequence[str]) -> List[Tuple[str, str, Tuple[int, int, int]]]:
    results: List[Tuple[str, str, Tuple[int, int, int]]] = []
    seen: set[str] = set()
    for raw_mode in active_modes or []:
        mode = str(raw_mode or "").strip().lower()
        if not mode or mode in seen:
            continue
        seen.add(mode)
        label = TEAM_ACTIVE_MODE_LABELS.get(mode, mode.replace("_", " ").title())
        color = TEAM_ACTIVE_MODE_HIGHLIGHT_COLORS.get(mode, (255, 215, 90))
        results.append((mode, label, color))
    return results


def render_support_deck_page(user_name: str, deck_rows: List[dict], page: int, per_page: int = 8, filter_label: str = "All Decks", sort_label: str = "Name A-Z", theme_name: str = "gray") -> io.BytesIO:
    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_ui_theme_palette(theme_name)

    title = f"{user_name}'s Decks"
    draw.text((34, 28), title, font=FONT_XL, fill=palette["title"])

    total_pages = max(1, math.ceil(max(1, len(deck_rows)) / per_page))
    page = max(1, min(page, total_pages))
    draw.text((34, 76), f"Filter: {filter_label} • Sort: {sort_label} • Page {page}/{total_pages}", font=FONT_S, fill=palette["subtitle"])

    outer = (24, 112, 936, 934)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    start = (page - 1) * per_page
    items = deck_rows[start:start + per_page]
    x_positions = [68, 506]
    y_positions = [150, 338, 526, 714]
    art_size = (102, 126)

    for idx, row in enumerate(items):
        col = idx % 2
        row_idx = idx // 2
        base_x = x_positions[col]
        base_y = y_positions[row_idx]
        box = (base_x, base_y, base_x + 360, base_y + 150)

        accent = palette["accent_binder"]
        draw.rounded_rectangle(box, 18, fill=palette["card_fill"], outline=accent, width=3)

        art = open_support_card_template(art_size, radius=18)
        overlay = Image.new("RGBA", art_size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle((0, 0, art_size[0]-1, art_size[1]-1), 16, outline=accent + (255,), width=3)
        art = apply_rounded_mask(Image.alpha_composite(art, overlay), 18)
        img.paste(art, (base_x + 12, base_y + 12), art)

        text_x = base_x + 116
        text_width = box[2] - text_x - 14
        header = str(row["name"])
        header_lines = fit_wrapped_text(draw, header, FONT_M_BOLD, text_width, 2)
        for i, line in enumerate(header_lines):
            draw.text((text_x, base_y + 12 + i * 22), line, font=FONT_M_BOLD, fill=palette["title"])

        status = "Saved Deck"
        status_y = base_y + 12 + max(1, len(header_lines)) * 22 + 2
        draw.text((text_x, status_y), ellipsize_text(draw, status, FONT_S_BOLD, text_width), font=FONT_S_BOLD, fill=accent)
        draw.text((text_x, status_y + 22), f"Unique Cards: {row['card_count']}/{SUPPORT_DECK_SIZE}", font=FONT_S, fill=(214, 220, 242))

        preview_names = row.get("preview_names") or []
        preview_text = ", ".join(preview_names[:3]) if preview_names else "No preview"
        preview_top = status_y + 44
        preview_available_height = base_y + 142 - preview_top
        preview_line_height = 16
        max_preview_lines = max(1, preview_available_height // preview_line_height)
        preview_lines = fit_wrapped_text(draw, f"Preview: {preview_text}", FONT_XS, text_width, max_preview_lines)
        for i, line in enumerate(preview_lines):
            draw.text((text_x, preview_top + i * preview_line_height), line, font=FONT_XS, fill=(204, 204, 210))

        rule_y = base_y + 130
        rule_text = ellipsize_text(draw, "Rule: 1-15 unique support cards", FONT_XS, text_width)
        draw.text((text_x, rule_y), rule_text, font=FONT_XS, fill=(168, 168, 176))

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


def render_team_page(user_name: str, team_rows: List[dict], page: int, per_page: int = 8, filter_label: str = "All Teams", sort_label: str = "Name A-Z", theme_name: str = "gray") -> io.BytesIO:
    def build_team_hand_preview(member_ids: Sequence[str], accent_rgb: Tuple[int, int, int], size: Tuple[int, int]) -> Image.Image:
        preview = open_binder_panel_background(size, radius=18, alpha=min(255, BINDER_PANEL_ALPHA))
        overlay = Image.new("RGBA", size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle((1, 1, size[0] - 2, size[1] - 2), 16, outline=accent_rgb + (255,), width=3)

        content_top = 10
        content_bottom = size[1] - 10
        content_height = max(48, content_bottom - content_top)
        thumb_gap = 10
        inner_margin = 18
        thumb_w = max(70, min(104, (size[0] - inner_margin * 2 - thumb_gap * 2) // 3))
        thumb_h = max(96, content_height - 10)
        total_w = thumb_w * 3 + thumb_gap * 2
        start_x = (size[0] - total_w) // 2
        thumb_y = content_top + max(0, (content_height - thumb_h) // 2)
        corner_radius = 16

        for index in range(3):
            member_id = member_ids[index] if index < len(member_ids) else None
            if member_id:
                member_row = db.get_inventory_instance(member_id)
                thumb_path = str(member_row["image_path"]) if member_row else ""
                attribute = str(member_row["attribute"]) if member_row else ""
                thumb = open_card_image_contain(
                    thumb_path,
                    (thumb_w, thumb_h),
                    radius=corner_radius,
                    background=palette["card_fill"] + (255,),
                )
                outline_rgb = element_color(attribute) if attribute else accent_rgb
            else:
                thumb = open_binder_panel_background((thumb_w, thumb_h), radius=corner_radius, alpha=255)
                td = ImageDraw.Draw(thumb)
                td.rounded_rectangle((0, 0, thumb_w - 1, thumb_h - 1), corner_radius, outline=palette["info_outline"], width=2)
                td.text((thumb_w // 2, thumb_h // 2), "EMPTY", font=FONT_XS_BOLD, fill=palette["info_text"], anchor="mm")
                outline_rgb = palette["info_outline"]

            thumb_overlay = Image.new("RGBA", (thumb_w, thumb_h), (0, 0, 0, 0))
            td = ImageDraw.Draw(thumb_overlay)
            td.rounded_rectangle((0, 0, thumb_w - 1, thumb_h - 1), corner_radius, outline=outline_rgb + (255,), width=3)
            thumb = apply_rounded_mask(Image.alpha_composite(thumb, thumb_overlay), corner_radius)

            card_layer = Image.new("RGBA", (thumb_w + 10, thumb_h + 10), (0, 0, 0, 0))
            shadow_draw = ImageDraw.Draw(card_layer)
            shadow_draw.rounded_rectangle((5, 6, thumb_w + 4, thumb_h + 5), corner_radius + 1, fill=(0, 0, 0, 95))

            card_layer.alpha_composite(thumb, (5, 5))
            paste_x = start_x + index * (thumb_w + thumb_gap) - 5
            preview.alpha_composite(card_layer, (paste_x, thumb_y - 5))

        return Image.alpha_composite(preview, overlay)

    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_ui_theme_palette(theme_name)

    title = f"{user_name}'s Teams"
    draw.text((34, 28), title, font=FONT_XL, fill=palette["title"])

    total_pages = max(1, math.ceil(max(1, len(team_rows)) / per_page))
    page = max(1, min(page, total_pages))
    draw.text((34, 76), f"Filter: {filter_label} • Sort: {sort_label} • Page {page}/{total_pages}", font=FONT_S, fill=palette["subtitle"])

    outer = (24, 112, 936, 934)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    start = (page - 1) * per_page
    items = team_rows[start:start + per_page]
    x_positions = [68, 506]
    y_positions = [160, 352, 544, 736]
    art_size = (362, 136)

    for idx, row in enumerate(items):
        col = idx % 2
        row_idx = idx // 2
        base_x = x_positions[col]
        base_y = y_positions[row_idx]

        active_mode_highlights = team_active_mode_highlight_colors(row.get("active_modes") or [])
        accent = active_mode_highlights[0][2] if active_mode_highlights else palette["accent_binder"]
        member_ids = row.get("member_ids") or []
        name_lines = wrap_text(draw, row["name"], FONT_S_BOLD, art_size[0] - 6)[:2]
        line_step = 18
        name_block_height = max(18, len(name_lines) * line_step)
        name_y = base_y - 4
        for line_idx, line in enumerate(name_lines):
            draw.text((base_x + 12 + art_size[0] // 2, name_y + line_idx * line_step), line, font=FONT_S_BOLD, fill=palette["title"], anchor="ma")

        art = build_team_hand_preview(member_ids, accent, art_size)
        art = apply_rounded_mask(art, 18)
        bubble_y = base_y + name_block_height + 6
        img.paste(art, (base_x + 12, bubble_y), art)

        if active_mode_highlights:
            highlight_box = (base_x + 12, bubble_y, base_x + 12 + art_size[0], bubble_y + art_size[1])
            # A team can now own multiple active modes, but the card should only ever
            # render one clean highlight outline. The badge below still lists every
            # active role, while the outline uses the first active mode's accent.
            draw.rounded_rectangle(highlight_box, 18, outline=accent, width=5)

            label_text = " • ".join(label for _mode, label, _color in active_mode_highlights)
            badge_font = FONT_XS_BOLD
            badge_text = ellipsize_text(draw, label_text, badge_font, art_size[0] - 24)
            badge_w = int(math.ceil(draw.textlength(badge_text, font=badge_font))) + 18
            badge_box = (
                base_x + 12 + (art_size[0] - badge_w) // 2,
                bubble_y + art_size[1] - 27,
                base_x + 12 + (art_size[0] + badge_w) // 2,
                bubble_y + art_size[1] - 7,
            )
            draw.rounded_rectangle(badge_box, 10, fill=palette["panel_fill"], outline=accent, width=2)
            draw.text(((badge_box[0] + badge_box[2]) / 2, (badge_box[1] + badge_box[3]) / 2), badge_text, font=badge_font, fill=palette["title"], anchor="mm")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer
