"""Rendering profile pages and rank badges."""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path
from typing import Dict, Optional, Tuple

import discord
from PIL import Image, ImageDraw, ImageOps

from kuribot.config import LEVELING_MAX_LEVEL
from kuribot.database import db
from kuribot.render.fonts import (
    FONT_L_BOLD, FONT_M_BOLD, FONT_S, FONT_S_BOLD, FONT_XL, FONT_XS_BOLD,
)
from kuribot.render.themes import get_ui_theme_palette, UI_THEME_PALETTES
from kuribot.render.images import (
    apply_rounded_mask, open_binder_canvas_background, paste_binder_panel, with_panel_user_context,
)
from kuribot.render.text import ellipsize_text, fit_wrapped_text, wrap_text
from kuribot.players import xp_required_for_level
from kuribot.portraits import load_player_avatar_image, paste_avatar_with_outline
from kuribot.ranked import ranked_gxe_from_row
from kuribot.arena import get_user_arena_rank_position
from kuribot.profile import load_profile_tag_image


def draw_profile_stat_card(draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], label: str, value: str, accent: Tuple[int, int, int], palette: Optional[Dict[str, Tuple[int, int, int]]] = None):
    palette = palette or UI_THEME_PALETTES["gray"]
    draw.rounded_rectangle(box, 20, fill=palette["card_fill"], outline=accent, width=3)
    left = box[0] + 18
    draw.text((left, box[1] + 14), label.upper(), font=FONT_XS_BOLD, fill=accent)
    lines = wrap_text(draw, str(value), FONT_M_BOLD, (box[2] - box[0]) - 36)
    for idx, line in enumerate(lines[:2]):
        draw.text((left, box[1] + 40 + idx * 26), line, font=FONT_M_BOLD, fill=palette["card_text"])


@with_panel_user_context(lambda user_name, avatar_bytes, user_row, *args, **kwargs: int(user_row["user_id"]))
def render_profile_page(
    user_name: str,
    avatar_bytes: bytes,
    user_row: sqlite3.Row,
    binder_size: int,
    support_card_count: int,
    daily_text: str,
    signature_text: str,
    guild_text: str,
    page: int,
    theme_name: str = "gray",
    unread_mail: int = 0,
) -> io.BytesIO:
    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_ui_theme_palette(theme_name)

    draw.text((34, 28), f"{user_name}'s Profile", font=FONT_XL, fill=palette["title"])
    draw.text((34, 76), f"Page {page}/2", font=FONT_S, fill=palette["subtitle"])

    outer = (24, 136, 936, 934)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    if page == 1:
        # Keep page 1 centered within the outer frame so the profile panels do not
        # visually lean toward the top-left corner.
        left_panel = (73, 169, 371, 511)
        draw.rounded_rectangle(left_panel, 26, fill=palette["panel_fill"], outline=palette["panel_outline"], width=3)

        avatar_size = (220, 220)
        avatar_x = left_panel[0] + (left_panel[2] - left_panel[0] - avatar_size[0]) // 2
        avatar_y = left_panel[1] + 24
        if avatar_bytes:
            try:
                avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
                contained = ImageOps.contain(avatar, avatar_size, method=Image.Resampling.LANCZOS)
                avatar = Image.new("RGBA", avatar_size, (0, 0, 0, 0))
                avatar.alpha_composite(contained, ((avatar_size[0] - contained.width) // 2, (avatar_size[1] - contained.height) // 2))
                paste_avatar_with_outline(img, avatar, (avatar_x, avatar_y), (*palette["avatar_outline"], 255), outline_width=4)
            except Exception:
                pass
        draw.text(((left_panel[0] + left_panel[2]) // 2, avatar_y + avatar_size[1] + 28), ellipsize_text(draw, user_name, FONT_M_BOLD, left_panel[2] - left_panel[0] - 40), font=FONT_M_BOLD, fill=palette["title"], anchor="mm")
        guild_label = ellipsize_text(draw, f"Guild: {guild_text}", FONT_S_BOLD, left_panel[2] - left_panel[0] - 44)
        draw.text(((left_panel[0] + left_panel[2]) // 2, avatar_y + avatar_size[1] + 58), guild_label, font=FONT_S_BOLD, fill=palette["subtitle"], anchor="mm")
        if unread_mail > 0:
            ping_text = f"NEW MAIL ({unread_mail})"
            draw.text(((left_panel[0] + left_panel[2]) // 2, avatar_y + avatar_size[1] + 84), ping_text, font=FONT_XS_BOLD, fill=(230, 64, 64), anchor="mm")

        tag_box = (left_panel[0], 563, left_panel[2], 901)
        draw.rounded_rectangle(tag_box, 26, fill=palette["panel_fill"], outline=palette["signature_outline"], width=3)
        draw.text(((tag_box[0] + tag_box[2]) // 2, tag_box[1] + 18), "Showcase", font=FONT_M_BOLD, fill=palette["title"], anchor="ma")
        tag_image = load_profile_tag_image(int(user_row["user_id"]), (tag_box[2] - tag_box[0] - 28, tag_box[3] - tag_box[1] - 70))
        image_area = (tag_box[0] + 14, tag_box[1] + 52, tag_box[2] - 14, tag_box[3] - 14)
        if tag_image is not None:
            tag_image = apply_rounded_mask(tag_image, 22)
            img.paste(tag_image, (image_area[0], image_area[1]), tag_image)
        else:
            draw.rounded_rectangle(image_area, 20, outline=palette["panel_outline"], width=2)
            draw.text(((image_area[0] + image_area[2]) // 2, image_area[1] + 70), "No image", font=FONT_M_BOLD, fill=palette["empty_text"], anchor="mm")
            draw.multiline_text(((image_area[0] + image_area[2]) // 2, image_area[1] + 110), "Use the Showcase button below\nto upload one for review.", font=FONT_S, fill=palette["signature_hint"], anchor="mm", align="center", spacing=6)

        level_value = int(user_row["level"] or 1)
        next_level_xp = 0 if level_value >= LEVELING_MAX_LEVEL else xp_required_for_level(level_value)
        level_progress = "MAX" if level_value >= LEVELING_MAX_LEVEL else f"{int(user_row['level_xp'] or 0)}/{next_level_xp} XP"
        stat_boxes = [
            ((407, 173, 633, 269), "Level", str(level_value), palette["accent_support"]),
            ((661, 173, 887, 269), "Level XP", level_progress, palette["accent_support"]),
            ((407, 297, 633, 393), "Packs", str(sum(int(row["quantity"] or 0) for row in db.user_packs(int(user_row["user_id"])))), (255, 215, 90)),
            ((661, 297, 887, 393), "Gold", str(user_row["gold"]), (255, 196, 76)),
        ]
        for box, label, value, accent in stat_boxes:
            draw_profile_stat_card(draw, box, label, value, accent, palette)

        signature_box = (407, 433, 887, 901)
        draw.rounded_rectangle(signature_box, 26, fill=palette["panel_fill"], outline=palette["signature_outline"], width=3)
        draw.text(((signature_box[0] + signature_box[2]) // 2, signature_box[1] + 18), "Signature", font=FONT_L_BOLD, fill=palette["title"], anchor="ma")
        sig_lines = fit_wrapped_text(draw, signature_text, FONT_S, signature_box[2] - signature_box[0] - 56, 13)
        if sig_lines:
            y = signature_box[1] + 76
            for line in sig_lines:
                draw.text(((signature_box[0] + signature_box[2]) // 2, y), line, font=FONT_S, fill=palette["signature_text"], anchor="ma")
                y += 28
        else:
            draw.text(((signature_box[0] + signature_box[2]) // 2, signature_box[1] + 108), "This panel is empty.", font=FONT_S, fill=palette["empty_text"], anchor="ma")
    else:
        # Keep page 2 centered within the outer frame so the stat grid and info
        # panels do not visually lean toward the top-left corner.
        level_value = int(user_row["level"] or 1)
        arena_rank = get_user_arena_rank_position(int(user_row["user_id"]))
        stat_boxes = [
            ((74, 163, 334, 259), "Level", str(level_value), palette["accent_support"]),
            ((350, 163, 610, 259), "Packs", str(sum(int(row["quantity"] or 0) for row in db.user_packs(int(user_row["user_id"])))), (255, 215, 90)),
            ((626, 163, 886, 259), "Gold", str(user_row["gold"]), (255, 196, 76)),
            ((74, 287, 334, 383), "Wins", str(user_row["wins"]), (100, 190, 120)),
            ((350, 287, 610, 383), "Losses", str(user_row["losses"]), (220, 110, 110)),
            ((626, 287, 886, 383), "Ranked ELO", str(user_row["ranked_elo"]), palette["accent_support"]),
            ((74, 411, 334, 507), "GXE", f"{ranked_gxe_from_row(user_row):.1f}%", palette["accent_support"]),
            ((350, 411, 610, 507), "Binder Size", str(binder_size), palette["accent_binder"]),
            ((626, 411, 886, 507), "Support Cards", str(support_card_count), palette["accent_support"]),
            ((74, 535, 334, 631), "Arena Rank", f"#{arena_rank}", palette["accent_support"]),
            ((350, 535, 610, 631), "Peak ELO", str(user_row["ranked_peak_elo"]), palette["accent_support"]),
            ((626, 535, 886, 631), "Daily", daily_text, (255, 225, 110)),
        ]
        for box, label, value, accent in stat_boxes:
            draw_profile_stat_card(draw, box, label, value, accent, palette)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


@with_panel_user_context(lambda user_name, user_row, *args, **kwargs: int(user_row["user_id"]))
def render_rank_badge(user_name: str, user_row: sqlite3.Row, avatar_path_value: str = "", theme_name: str = "gray") -> io.BytesIO:
    width, height = 640, 168
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_ui_theme_palette(theme_name)

    outer = (10, 10, width - 10, height - 10)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    inner = (24, 24, width - 24, height - 24)
    draw.rounded_rectangle(inner, 28, fill=palette["panel_fill"], outline=palette["panel_outline"], width=2)

    avatar_size = (96, 96)
    avatar = load_player_avatar_image(Path(str(avatar_path_value)) if str(avatar_path_value or "").strip() else None, avatar_size)
    avatar_pos = (40, (height - avatar_size[1]) // 2)
    paste_avatar_with_outline(img, avatar, avatar_pos, (*palette["avatar_outline"], 255), outline_width=3)

    level_value = int(user_row["level"] or 1)
    current_xp = max(0, int(user_row["level_xp"] or 0))
    max_xp = 0 if level_value >= LEVELING_MAX_LEVEL else xp_required_for_level(level_value)
    progress_ratio = 1.0 if level_value >= LEVELING_MAX_LEVEL else (current_xp / max_xp if max_xp > 0 else 0.0)
    progress_ratio = max(0.0, min(1.0, progress_ratio))

    content_top = 34
    title_x = 150
    title_max_width = 290
    display_name = ellipsize_text(draw, user_name, FONT_M_BOLD, title_max_width)
    draw.text((title_x, content_top + 8), display_name, font=FONT_M_BOLD, fill=palette["title"])

    info_text = f"Level {level_value}"
    if level_value >= LEVELING_MAX_LEVEL:
        info_text += " • MAX"
    else:
        info_text += f" • {current_xp}/{max_xp} XP"
    draw.text((title_x, content_top + 38), info_text, font=FONT_S_BOLD, fill=palette["card_text"])

    progress_track = (150, content_top + 68, 438, content_top + 84)
    draw.rounded_rectangle(progress_track, 8, fill=palette["card_fill"], outline=palette["panel_outline"], width=2)
    inset = 3
    fill_width = int(round((progress_track[2] - progress_track[0] - inset * 2) * progress_ratio))
    if fill_width > 0:
        fill_box = (progress_track[0] + inset, progress_track[1] + inset, progress_track[0] + inset + fill_width, progress_track[3] - inset)
        draw.rounded_rectangle(fill_box, 6, fill=palette["accent_support"])

    rank_panel = (462, 42, 596, 126)
    draw.rounded_rectangle(rank_panel, 18, fill=palette["card_fill"], outline=palette["accent_support"], width=2)
    draw.text(((rank_panel[0] + rank_panel[2]) // 2, rank_panel[1] + 19), "RANK", font=FONT_XS_BOLD, fill=palette["accent_support"], anchor="mm")
    draw.text(((rank_panel[0] + rank_panel[2]) // 2, rank_panel[1] + 48), f"LV {level_value}", font=FONT_M_BOLD, fill=palette["title"], anchor="mm")
    rank_subtext = "MAX" if level_value >= LEVELING_MAX_LEVEL else f"{int(round(progress_ratio * 100))}%"
    draw.text(((rank_panel[0] + rank_panel[2]) // 2, rank_panel[1] + 69), rank_subtext, font=FONT_XS_BOLD, fill=palette["card_text"], anchor="mm")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


def make_rank_badge_file(user_name: str, user_row: sqlite3.Row, avatar_path_value: str = "") -> discord.File:
    theme_name = db.get_ui_theme(int(user_row["user_id"]))
    return discord.File(render_rank_badge(user_name, user_row, avatar_path_value, theme_name), filename="rank_badge.png")
