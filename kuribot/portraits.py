"""Player avatar and duelist portrait files."""

from __future__ import annotations

import io
import math
import re
from pathlib import Path
from typing import List, Optional, Tuple

import discord
from PIL import Image, ImageDraw, ImageOps

from kuribot.config import (
    AVATARS_DIR, DUELISTS_DIR, PLAYER_AVATAR_EXTENSIONS, PLAYER_AVATAR_PAGE_SIZE,
    PLAYER_AVATAR_RENDER_SIZE,
)
from kuribot.database import db
from kuribot.render.fonts import FONT_L_BOLD, FONT_M, FONT_M_BOLD, FONT_S, FONT_S_BOLD, FONT_XL_BOLD
from kuribot.render.themes import get_ui_theme_palette
from kuribot.render.images import (
    fit_cached_rgba_image, open_binder_canvas_background, open_card_image, paste_binder_panel,
)
from kuribot.render.text import ellipsize_text, fit_wrapped_text
from kuribot.duelists import (
    duelist_filename_lookup_variants, resolve_dueling_grounds_source_key, slugify_duelist_key,
)


def get_player_avatar_files() -> List[Path]:
    if not AVATARS_DIR.exists() or not AVATARS_DIR.is_dir():
        return []
    return sorted(
        [child for child in AVATARS_DIR.iterdir() if child.is_file() and child.suffix.lower() in PLAYER_AVATAR_EXTENSIONS],
        key=lambda p: p.stem.lower(),
    )


def get_duelist_files() -> List[Path]:
    if not DUELISTS_DIR.exists() or not DUELISTS_DIR.is_dir():
        return []
    return sorted(
        [child for child in DUELISTS_DIR.iterdir() if child.is_file() and child.suffix.lower() in PLAYER_AVATAR_EXTENSIONS],
        key=lambda p: p.stem.lower(),
    )


def get_player_avatar_choices() -> List[Tuple[str, Path]]:
    return [(path.stem, path) for path in get_player_avatar_files()]


def get_player_avatar_path_by_name(name: str) -> Optional[Path]:
    wanted = str(name or "").strip().lower()
    for display_name, avatar_path in get_player_avatar_choices():
        if display_name.lower() == wanted:
            return avatar_path
    return None


def normalize_portrait_lookup_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def get_campaign_duelist_portrait_path(area_key: str, duelist_key: str, duelist_name: str) -> Optional[Path]:
    return get_duelist_portrait_path(duelist_key, duelist_name)


def get_duelist_portrait_path(duelist_key: str, duelist_name: str) -> Optional[Path]:
    candidates = [
        str(duelist_name or "").strip(),
        str(duelist_key or "").strip(),
        str(duelist_key or "").replace("_", " ").strip(),
    ]
    normalized_candidates = set()
    for candidate in candidates:
        for variant in duelist_filename_lookup_variants(candidate) or [candidate]:
            normalized = normalize_portrait_lookup_name(variant)
            if normalized:
                normalized_candidates.add(normalized)
    if not normalized_candidates:
        return None

    wanted_source_key = resolve_dueling_grounds_source_key(duelist_key, duelist_name) or slugify_duelist_key(duelist_key)
    for duelist_path in get_duelist_files():
        normalized_stem = normalize_portrait_lookup_name(duelist_path.stem)
        if normalized_stem in normalized_candidates:
            return duelist_path
        resolved_stem_key = resolve_dueling_grounds_source_key(duelist_path.stem)
        if resolved_stem_key and slugify_duelist_key(resolved_stem_key) == slugify_duelist_key(wanted_source_key):
            return duelist_path
    return None


def make_campaign_duelist_portrait_file(area_key: str, duelist_key: str, duelist_name: str, *, filename: str = "campaign_duelist.png") -> Optional[discord.File]:
    portrait_path = get_campaign_duelist_portrait_path(area_key, duelist_key, duelist_name)
    if portrait_path is None or not portrait_path.exists() or not portrait_path.is_file():
        return None
    try:
        with Image.open(portrait_path) as image:
            preview = ImageOps.contain(image.convert("RGBA"), (256, 256), method=Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            preview.save(buffer, format="PNG", compress_level=3)
            buffer.seek(0)
            return discord.File(buffer, filename=filename)
    except Exception:
        try:
            return discord.File(str(portrait_path), filename=filename)
        except Exception:
            return None


def get_player_avatar_page(page: int) -> Tuple[List[Tuple[str, Path]], int, int]:
    choices = get_player_avatar_choices()
    total_pages = max(1, math.ceil(max(1, len(choices)) / PLAYER_AVATAR_PAGE_SIZE))
    clamped_page = max(1, min(page, total_pages))
    start = (clamped_page - 1) * PLAYER_AVATAR_PAGE_SIZE
    end = start + PLAYER_AVATAR_PAGE_SIZE
    return choices[start:end], clamped_page, total_pages


def load_player_avatar_image(avatar_path: Optional[Path], size: Tuple[int, int]) -> Image.Image:
    if avatar_path and avatar_path.exists():
        try:
            # Cached resize: battle frames used to re-open and re-resize both avatars every turn.
            contained = fit_cached_rgba_image(avatar_path, size, "contain")
            canvas = Image.new("RGBA", size, (0, 0, 0, 0))
            paste_x = (size[0] - contained.width) // 2
            paste_y = (size[1] - contained.height) // 2
            canvas.alpha_composite(contained, (paste_x, paste_y))
            return canvas
        except Exception:
            pass
    fallback = Image.new("RGBA", size, (90, 90, 100, 255))
    draw = ImageDraw.Draw(fallback)
    draw.text((size[0] // 2, size[1] // 2), "?", font=FONT_L_BOLD, fill=(255, 255, 255, 255), anchor="mm")
    return fallback


def paste_avatar_with_outline(
    base: Image.Image,
    avatar: Image.Image,
    position: Tuple[int, int],
    outline_color: Tuple[int, int, int, int],
    outline_width: int = 3,
):
    """Paste a player avatar without adding an extra outline.

    The legacy name/signature is kept so existing renderers continue to call it,
    but player avatars should render cleanly with only their image alpha.
    """
    x, y = position
    avatar_rgba = avatar.convert("RGBA")
    alpha = avatar_rgba.getchannel("A")
    if alpha.getbbox() is None:
        return
    base.alpha_composite(avatar_rgba, (x, y))


def render_avatar_selection_page(user_name: str, page: int, selected_name: str = "", theme_name: str = "gray") -> io.BytesIO:
    page_items, page, total_pages = get_player_avatar_page(page)
    width, height = PLAYER_AVATAR_RENDER_SIZE
    palette = get_ui_theme_palette(theme_name)
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    outer = (24, 24, width - 24, height - 24)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=(*palette["outer_outline"], 255), width=3)

    bot_box = (50, 50, 186, 186)
    draw.rounded_rectangle(bot_box, 28, fill=(*palette["panel_fill"], 235), outline=(*palette["avatar_outline"], 255), width=3)
    kuribot_path = Path("C:/Games/KuriBot/resources/kuribot.jpg")
    if kuribot_path.exists():
        kuribot_size = (bot_box[2] - bot_box[0] - 14, bot_box[3] - bot_box[1] - 14)
        kuribot_inner = open_card_image(str(kuribot_path), kuribot_size, radius=22)
        kuribot_x = bot_box[0] + 7
        kuribot_y = bot_box[1] + 7
        img.alpha_composite(kuribot_inner, (kuribot_x, kuribot_y))
        draw.rounded_rectangle((kuribot_x - 2, kuribot_y - 2, kuribot_x + kuribot_size[0] + 2, kuribot_y + kuribot_size[1] + 2), 24, outline=(214, 230, 255, 255), width=3)
    else:
        draw.text(((bot_box[0] + bot_box[2]) // 2, bot_box[1] + 46), "Kuri", font=FONT_L_BOLD, fill=(255, 255, 255, 255), anchor="mm")
        draw.text(((bot_box[0] + bot_box[2]) // 2, bot_box[1] + 88), "Bot", font=FONT_L_BOLD, fill=(255, 255, 255, 255), anchor="mm")
    draw.text((212, 58), "Choose your avatar below!", font=FONT_XL_BOLD, fill=(245, 248, 255, 255))
    subtitle = f"{user_name}, pick the portrait that will represent you in KuriBot."
    draw.text((212, 104), subtitle, font=FONT_M, fill=(*palette["subtitle"], 255))
    draw.text((212, 136), f"Page {page}/{total_pages} • 16 avatars per page", font=FONT_S, fill=(*palette["subtitle"], 255))
    if selected_name:
        selected_label = ellipsize_text(draw, f"Selected: {selected_name}", FONT_S_BOLD, width - 236)
        draw.text((212, 166), selected_label, font=FONT_S_BOLD, fill=(*palette["avatar_outline"], 255))

    columns = 4
    rows = 4
    cell_w = 222
    cell_h = 150
    thumb_size = (108, 108)
    gap_x = 4
    gap_y = 10
    grid_top = 220
    total_grid_w = columns * cell_w + (columns - 1) * gap_x
    grid_left = (width - total_grid_w) // 2
    grid_bottom = grid_top + rows * cell_h + (rows - 1) * gap_y

    avatar_margin_x = 14
    avatar_margin_y = 20
    text_left_padding = 132
    text_right_padding = 12
    text_top_padding = 24
    text_line_height = 22

    for idx in range(columns * rows):
        row = idx // columns
        col = idx % columns
        x1 = grid_left + col * (cell_w + gap_x)
        y1 = grid_top + row * (cell_h + gap_y)
        x2 = x1 + cell_w
        y2 = y1 + cell_h
        draw.rounded_rectangle((x1, y1, x2, y2), 22, fill=(*palette["card_fill"], 220), outline=(*palette["panel_outline"], 255), width=2)
        if idx < len(page_items):
            avatar_name, avatar_path = page_items[idx]
            avatar_x = x1 + avatar_margin_x
            avatar_y = y1 + avatar_margin_y
            avatar = load_player_avatar_image(avatar_path, thumb_size)
            paste_avatar_with_outline(img, avatar, (avatar_x, avatar_y), (*palette["panel_outline"], 255), outline_width=3)
            is_selected = selected_name and avatar_name.lower() == selected_name.lower()
            if is_selected:
                paste_avatar_with_outline(img, avatar, (avatar_x, avatar_y), (*palette["avatar_outline"], 255), outline_width=6)
            text_x = x1 + text_left_padding
            text_max_width = max(24, x2 - text_x - text_right_padding)
            text_max_lines = max(1, (cell_h - text_top_padding - 18) // text_line_height)
            name_lines = fit_wrapped_text(draw, avatar_name, FONT_S_BOLD, text_max_width, text_max_lines)
            text_y = y1 + text_top_padding
            max_text_y = y2 - 18
            for line in name_lines:
                if text_y + text_line_height > max_text_y + 2:
                    break
                safe_line = ellipsize_text(draw, line, FONT_S_BOLD, text_max_width)
                draw.text((text_x, text_y), safe_line, font=FONT_S_BOLD, fill=(*palette["card_text"], 255))
                text_y += text_line_height
        else:
            draw.text(((x1 + x2) // 2, (y1 + y2) // 2), "Empty", font=FONT_M_BOLD, fill=(120, 132, 158, 255), anchor="mm")

    footer = "Use the dropdown below to choose, then confirm your avatar."
    footer_center_y = grid_bottom + ((outer[3] - grid_bottom) / 2)
    draw.text((width // 2, int(round(footer_center_y))), footer, font=FONT_S_BOLD, fill=(220, 228, 248, 255), anchor="mm")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


async def get_user_profile_avatar_bytes(user: discord.abc.User, size: int = 256) -> bytes:
    stored_avatar_path = db.get_avatar_path(int(user.id))
    if stored_avatar_path:
        try:
            avatar_path = Path(stored_avatar_path)
            if avatar_path.exists():
                img = Image.open(avatar_path).convert("RGBA")
                contained = ImageOps.contain(img, (size, size), method=Image.Resampling.LANCZOS)
                canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                paste_x = (size - contained.width) // 2
                paste_y = (size - contained.height) // 2
                canvas.alpha_composite(contained, (paste_x, paste_y))
                buffer = io.BytesIO()
                canvas.save(buffer, format="PNG", compress_level=3)
                return buffer.getvalue()
        except Exception:
            pass
    return b""
