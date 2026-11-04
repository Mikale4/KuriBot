"""Rendering battle frames, status badges and battle-log emoji."""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from kuribot.config import BATTLE_HEARTS, SPEED_GAUGE_MAX, SUPPORT_CARD_IMAGE_PATH, TEAM_SIZE
from kuribot.models import Fighter, StatusEffect, SupportCardDef, SupportDeckRuntime
from kuribot.render.fonts import (
    FONT_L, FONT_L_BOLD, FONT_M_BOLD, FONT_S, FONT_S_BOLD, FONT_XL, FONT_XS, FONT_XS_BOLD,
    get_bold_font, get_font,
)
from kuribot.render.themes import element_color, get_ui_theme_palette
from kuribot.render.images import (
    blurred_round_rect_layer, open_binder_canvas_background, open_card_image,
    open_card_image_contain, open_card_image_contain_tight, open_support_card_template,
)
from kuribot.render.text import draw_rich_text, ellipsize_text, wrap_rich_text_lines
from kuribot.monsters import format_monster_types
from kuribot.battle.runtime import (
    _fighter_statuses_store, active_equip_spell_name, ACTIVE_SUPPORT_CARDS_BY_FIGHTER,
    fighter_statuses, runtime_flag,
)
from kuribot.battle.stats import (
    effective_atk, effective_defense, effective_spd, support_card_stat_bonus,
)
from kuribot.battle.extra_deck import extra_deck_pool_for_team
from kuribot.portraits import load_player_avatar_image


def format_ratio_percent(value: float) -> str:
    return f"{int(round(float(value) * 100))}%"


def format_crit_damage_percent(value: float) -> str:
    return f"{int(round(float(value) * 100))}%"


def hp_percent_text(current: int, maximum: int) -> str:
    if maximum <= 0:
        return "0%"
    percent = int(round(max(0.0, min(1.0, current / maximum)) * 100))
    return f"{percent}%"


def hp_value_text(current: int, maximum: int) -> str:
    safe_current = max(0, int(current))
    safe_maximum = max(0, int(maximum))
    return f"HP {safe_current}/{safe_maximum}"


def battle_status_badge_labels(fighter: Fighter) -> List[str]:
    labels: List[str] = []

    def add_label(label: str):
        cleaned = " ".join(str(label or "").split()).strip()
        if cleaned and cleaned not in labels:
            labels.append(cleaned)

    # Show the highest-impact time-sensitive effects first so they do not get hidden
    # by the per-fighter badge cap in the battle status panel.
    bomb_count = len(list(runtime_flag(fighter, "bombs", [])))
    if bomb_count > 0:
        add_label(f"BOMB x{bomb_count}")

    statuses = fighter_statuses(fighter)
    if not statuses and getattr(fighter, "status", None) is not None:
        statuses = _fighter_statuses_store(fighter)

    for status in statuses:
        if status.effect is StatusEffect.POISON:
            add_label(f"POISON {status.poison_pct}%")
        else:
            add_label(status.effect.value)

    fallback_badges = runtime_flag(fighter, "status_badges", [])
    if isinstance(fallback_badges, (list, tuple, set)):
        for badge in fallback_badges:
            badge_text = " ".join(str(badge or "").split()).strip()
            if not badge_text:
                continue
            if badge_text.upper().startswith("BOMB"):
                continue
            add_label(badge_text)

    spell_name = active_equip_spell_name(fighter)

    if int(runtime_flag(fighter, "immunity_turns", 0)) > 0 or int(runtime_flag(fighter, "full_debuff_immunity_turns", 0)) > 0 or bool(runtime_flag(fighter, "immune_effects", set())):
        add_label("IMMUNITY")
    if int(runtime_flag(fighter, "protection_turns", 0)) > 0:
        add_label("PROTECTION")
    if int(runtime_flag(fighter, "heal_block_turns", 0)) > 0 or int(runtime_flag(fighter, "soul_taker_block_turns", 0)) > 0:
        add_label("HEAL BLOCK")
    if int(runtime_flag(fighter, "damage_taken_up_turns", 0)) > 0:
        add_label("MARK")
    if int(runtime_flag(fighter, "shield", 0)) > 0:
        add_label("SHIELD")
    if runtime_flag(fighter, "summon_data", None):
        add_label("TOKEN")
    if list(runtime_flag(fighter, "recovery_effects", [])):
        add_label("RECOVERY")
    if int(runtime_flag(fighter, "precog_turns", 0)) > 0:
        add_label("PRECOG")
    if spell_name == "Malevolent Nuzzler":
        add_label("LIFESTEAL")
    if int(runtime_flag(fighter, "reflect_turns", 0)) > 0:
        add_label("REFLECT")
    if int(runtime_flag(fighter, "counter_turns", 0)) > 0:
        add_label("COUNTER")
    return labels


def draw_hp_bar(draw: ImageDraw.ImageDraw, xy: Tuple[int, int, int, int], current: int, maximum: int):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, 9, fill=(28, 28, 40), outline=(220, 220, 235), width=2)
    ratio = 0.0 if maximum <= 0 else max(0.0, min(1.0, current / maximum))
    fill_w = int((x2 - x1 - 4) * ratio)
    if fill_w > 0:
        fill = (80, 220, 120) if ratio > 0.5 else (240, 200, 80) if ratio > 0.2 else (250, 95, 95)
        draw.rounded_rectangle((x1 + 2, y1 + 2, x1 + 2 + fill_w, y2 - 2), 7, fill=fill)

    percent_text = hp_percent_text(current, maximum)
    text_x = (x1 + x2) / 2
    text_y = (y1 + y2) / 2
    outline_fill = (0, 0, 0)
    outline_px = 1
    text_fill = (255, 255, 255)
    for dx in range(-outline_px, outline_px + 1):
        for dy in range(-outline_px, outline_px + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((text_x + dx, text_y + dy), percent_text, font=FONT_XS_BOLD, fill=outline_fill, anchor="mm")
    draw.text((text_x, text_y), percent_text, font=FONT_XS_BOLD, fill=text_fill, anchor="mm")


def render_battle_debug_speed_duel_field(
    player_name: str,
    enemy_name: str,
    allies: List[Fighter],
    enemies: List[Fighter],
    log_lines: List[str],
    active_fighter: Optional[Fighter] = None,
    target_fighter: Optional[Fighter] = None,
    tick_info: Optional[str] = None,
    round_info: Optional[str] = None,
    player_support_runtime: Optional[SupportDeckRuntime] = None,
    enemy_support_runtime: Optional[SupportDeckRuntime] = None,
    player_hearts: int = BATTLE_HEARTS,
    enemy_hearts: Optional[int] = BATTLE_HEARTS,
    max_hearts: int = BATTLE_HEARTS,
    theme_name: str = "gray",
    player_header_note: Optional[str] = None,
    enemy_header_note: Optional[str] = None,
    player_avatar_path: Optional[str] = None,
    enemy_avatar_path: Optional[str] = None,
) -> io.BytesIO:
    """Rush/Speed Duel inspired battlefield renderer used for all battles."""
    w, h = 1450, 940
    palette = get_ui_theme_palette(theme_name)
    img = open_binder_canvas_background((w, h))
    draw = ImageDraw.Draw(img)

    def clamp_color(value: int) -> int:
        return max(0, min(255, int(value)))

    def blend(c1: Tuple[int, int, int], c2: Tuple[int, int, int], ratio: float) -> Tuple[int, int, int]:
        ratio = max(0.0, min(1.0, float(ratio)))
        return tuple(clamp_color(c1[i] + (c2[i] - c1[i]) * ratio) for i in range(3))

    base = tuple(int(c) for c in palette.get("panel_fill", (18, 22, 38)))
    accent = tuple(int(c) for c in palette.get("accent_binder", (70, 180, 255)))
    outline = tuple(int(c) for c in palette.get("outer_outline", (120, 180, 255)))
    title = tuple(int(c) for c in palette.get("title", (245, 248, 255)))
    subtitle = tuple(int(c) for c in palette.get("subtitle", (170, 185, 220)))
    card_text = tuple(int(c) for c in palette.get("card_text", (235, 240, 255)))
    fire_theme = False
    if fire_theme:
        base = (38, 13, 8)
        accent = (255, 91, 20)
        outline = (255, 158, 54)
        title = (255, 244, 208)
        subtitle = (255, 187, 105)
        card_text = (255, 236, 206)
    lava_dark = (16, 4, 3)
    lava_mid = (108, 24, 8)
    lava_hot = (255, 104, 18)
    ember = (255, 196, 66)

    # Use the same canvas background as the bot's HUB renders instead of the old
    # diagonal arena rails. Keep the battle-specific glass panel and center divider
    # layered on top so the field layout/readability stays unchanged.
    bg = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bg)
    if fire_theme:
        fire_tint = Image.new("RGBA", (w, h), lava_dark + (96,))
        img = Image.alpha_composite(img, fire_tint)
        draw = ImageDraw.Draw(img)
        for x in range(-120, w + 180, 180):
            bd.line((x, h - 72, x + 82, h - 18), fill=lava_hot + (92,), width=6)
            bd.line((x + 38, h - 46, x + 128, h - 10), fill=ember + (58,), width=3)
        for i in range(44):
            ex = (i * 137 + 43) % w
            ey = (i * 83 + 31) % h
            radius = 1 + (i % 3)
            bd.ellipse((ex - radius, ey - radius, ex + radius, ey + radius), fill=ember + (70 + (i % 4) * 24,))
    # The debug battlefield has several edge-hugging pieces (player plate,
    # status panel, and both support back rows). Keep the huge dark panel
    # fully on-canvas while giving Player 2's support zones the same
    # breathing room Player 1's support zones have at the bottom.
    arena_panel = (40, 6, w - 40, h - 14)
    bd.rounded_rectangle(arena_panel, 44, fill=((30, 7, 4, 184) if fire_theme else (7, 10, 22, 170)), outline=outline + ((210,) if fire_theme else (150,)), width=3)
    bd.line((w // 2, arena_panel[1] + 46, w // 2, arena_panel[3] - 18), fill=accent + ((112,) if fire_theme else (70,)), width=2)
    if fire_theme:
        bd.arc((arena_panel[0] + 26, arena_panel[1] + 20, arena_panel[2] - 26, arena_panel[3] + 160), 190, 350, fill=lava_hot + (95,), width=5)
        bd.arc((arena_panel[0] + 95, arena_panel[1] + 48, arena_panel[2] - 95, arena_panel[3] + 92), 192, 348, fill=ember + (60,), width=2)
    img.alpha_composite(bg.filter(ImageFilter.GaussianBlur(0.35)))
    draw = ImageDraw.Draw(img)
    if fire_theme:
        banner = (520, 34, 930, 76)
        draw.rounded_rectangle(banner, 16, fill=(42, 10, 5, 232), outline=ember + (210,), width=2)
        draw.text(((banner[0] + banner[2]) // 2, (banner[1] + banner[3]) // 2), "🔥 VOLCANIC DEBUG FIELD 🔥", font=FONT_S_BOLD, fill=title, anchor="mm")

    def _rounded_rect_mask(size: Tuple[int, int], radius: int) -> Image.Image:
        mask = Image.new("L", size, 0)
        md = ImageDraw.Draw(mask)
        md.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
        return mask

    def _add_block_outline(pd: ImageDraw.ImageDraw, coords: Tuple[int, int, int, int], fill: Tuple[int, int, int, int], line: Tuple[int, int, int, int]) -> None:
        pd.rectangle(coords, fill=fill, outline=line)

    def _pixel_flame(pd: ImageDraw.ImageDraw, px: int, base_y: int, height: int, palette_seed: int = 0) -> None:
        palettes = [
            ((255, 106, 20, 210), (255, 164, 46, 235), (255, 226, 132, 245)),
            ((214, 48, 14, 210), (255, 112, 26, 232), (255, 196, 98, 245)),
        ]
        outer, mid, inner = palettes[palette_seed % len(palettes)]
        widths = [7, 5, 3]
        heights = [height, max(3, height - 2), max(2, height - 4)]
        offsets = [0, 1, 2]
        colors = [outer, mid, inner]
        for width, seg_h, offset, color in zip(widths, heights, offsets, colors):
            left = px - width // 2 + offset // 2
            top = base_y - seg_h
            pd.polygon([
                (left, base_y),
                (left + width, base_y),
                (left + width - 1, max(top + 1, base_y - max(2, seg_h // 2))),
                (left + width // 2, top),
                (left + 1, max(top + 2, base_y - max(2, seg_h // 2))),
            ], fill=color)

    def _build_fire_pixel_overlay(size: Tuple[int, int], motif: str, radius: int, intensity: float = 1.0) -> Image.Image:
        width, height = size
        if width <= 8 or height <= 8:
            return Image.new("RGBA", size, (0, 0, 0, 0))
        scale = 4 if min(width, height) >= 60 else 3
        pw = max(16, width // scale)
        ph = max(10, height // scale)
        pixel = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        pd = ImageDraw.Draw(pixel)
        rock_dark = (34, 11, 7, int(182 * intensity))
        rock_mid = (69, 24, 12, int(196 * intensity))
        rock_light = (105, 40, 18, int(188 * intensity))
        lava_main = (255, 98, 22, int(228 * intensity))
        lava_glow = (255, 148, 48, int(216 * intensity))
        lava_core = (255, 220, 116, int(238 * intensity))
        ash = (95, 54, 34, int(154 * intensity))

        def ridge_points(base_y: int, height_steps: List[int], start_x: int = 0, step: int = 3) -> List[Tuple[int, int]]:
            points: List[Tuple[int, int]] = [(0, ph), (start_x, ph)]
            x = start_x
            for h_step in height_steps:
                points.append((x, max(0, base_y - h_step)))
                x += step
            points.extend([(pw, base_y), (pw, ph)])
            return points

        if motif in {"panel", "status"}:
            floor_h = max(3, ph // 4)
            pd.rectangle((0, ph - floor_h, pw, ph), fill=rock_dark)
            mountain = [
                (0, ph - floor_h + 1),
                (max(2, pw // 7), ph - floor_h - 2),
                (max(4, pw // 5), ph - floor_h - 6),
                (max(7, pw // 3), ph - floor_h - 1),
                (max(10, pw // 2), ph - floor_h - 9),
                (max(13, (pw * 2) // 3), ph - floor_h - 2),
                (max(16, (pw * 5) // 6), ph - floor_h - 7),
                (pw, ph - floor_h + 1),
                (pw, ph),
                (0, ph),
            ]
            pd.polygon(mountain, fill=rock_mid)
            crater_cx = pw // 2 if motif == "status" else (pw * 2) // 3
            crater_top = max(2, ph - floor_h - 10)
            crater = [(crater_cx - 3, crater_top + 2), (crater_cx - 1, crater_top), (crater_cx + 2, crater_top), (crater_cx + 4, crater_top + 2), (crater_cx + 2, crater_top + 4), (crater_cx - 2, crater_top + 4)]
            pd.polygon(crater, fill=lava_main)
            pd.rectangle((crater_cx - 2, crater_top + 1, crater_cx + 1, crater_top + 3), fill=lava_core)
            for x in range(0, pw, 8):
                flow_y = ph - floor_h + 1 + ((x // 8) % 2)
                pd.rectangle((x, flow_y, min(pw - 1, x + 3), min(ph - 1, flow_y + 1)), fill=lava_glow)
            for idx, x in enumerate(range(4, pw, 10)):
                _pixel_flame(pd, x, ph - floor_h + 1, 4 + (idx % 4), idx)
            for spark in range(0, pw, 6):
                sy = 2 + ((spark * 5) % max(3, ph // 2))
                pd.point((spark, sy), fill=lava_core)
        elif motif in {"monster", "deck", "extra", "support", "token"}:
            floor_h = max(4, ph // 5)
            pd.rectangle((0, ph - floor_h, pw, ph), fill=rock_dark)
            ridge = [
                (0, ph - floor_h),
                (max(3, pw // 6), ph - floor_h - 3),
                (max(6, pw // 3), ph - floor_h - 8),
                (max(10, pw // 2), ph - floor_h - 4),
                (max(13, (pw * 2) // 3), ph - floor_h - 10),
                (max(16, (pw * 5) // 6), ph - floor_h - 5),
                (pw, ph - floor_h),
                (pw, ph),
                (0, ph),
            ]
            pd.polygon(ridge, fill=rock_mid)
            cone_center = pw // 2 if motif != "token" else (pw * 2) // 3
            cone_base_y = ph - floor_h - 1
            cone = [(cone_center - 7, cone_base_y), (cone_center - 2, cone_base_y - 8), (cone_center + 1, cone_base_y - 11), (cone_center + 6, cone_base_y), (cone_center + 4, cone_base_y + 2), (cone_center - 5, cone_base_y + 2)]
            pd.polygon(cone, fill=rock_light if motif == "support" else rock_mid)
            crater_box = (cone_center - 2, cone_base_y - 10, cone_center + 2, cone_base_y - 8)
            pd.rectangle(crater_box, fill=lava_main)
            pd.rectangle((crater_box[0] + 1, crater_box[1], crater_box[2] - 1, crater_box[3]), fill=lava_core)
            if motif in {"monster", "support", "token"}:
                for idx, xpos in enumerate(range(4, pw - 3, 8)):
                    _pixel_flame(pd, xpos, ph - floor_h + 1, 4 + (idx % 3), idx)
            if motif == "deck":
                river = [(2, ph - floor_h + 1), (6, ph - floor_h - 1), (10, ph - floor_h + 2), (14, ph - floor_h - 2), (18, ph - floor_h + 1), (pw - 2, ph - floor_h - 3)]
                for idx in range(len(river) - 1):
                    x_a, y_a = river[idx]
                    x_b, y_b = river[idx + 1]
                    pd.line((x_a, y_a, x_b, y_b), fill=lava_glow, width=2)
                    pd.line((x_a, y_a + 1, x_b, y_b + 1), fill=lava_main, width=1)
            if motif == "extra":
                for x in range(2, pw - 1, 6):
                    top = 3 + ((x * 3) % max(4, ph // 2))
                    pd.rectangle((x, top, x + 1, top + 1), fill=ash)
                    pd.point((x + 1, top - 1), fill=lava_core)
        elif motif == "equip":
            flow_y = ph // 2
            pd.rectangle((0, flow_y - 2, pw, flow_y + 2), fill=rock_dark)
            for idx, x in enumerate(range(0, pw, 5)):
                pd.rectangle((x, flow_y - 1 - (idx % 2), min(pw - 1, x + 3), flow_y + 1 + (idx % 2)), fill=lava_main)
                pd.rectangle((x + 1, flow_y, min(pw - 1, x + 2), flow_y + 1), fill=lava_core)
            for idx, x in enumerate(range(4, pw, 10)):
                _pixel_flame(pd, x, flow_y - 1, 3 + (idx % 2), idx)
        else:
            pd.rectangle((0, ph - 3, pw, ph), fill=rock_dark)
            for idx, x in enumerate(range(2, pw, 7)):
                _pixel_flame(pd, x, ph - 3, 4 + (idx % 2), idx)

        upscaled = pixel.resize((pw * scale, ph * scale), Image.Resampling.NEAREST)
        overlay = Image.new("RGBA", size, (0, 0, 0, 0))
        ox = max(0, (width - upscaled.width) // 2)
        oy = max(0, (height - upscaled.height) // 2)
        overlay.alpha_composite(upscaled, (ox, oy))
        mask = _rounded_rect_mask(size, max(4, radius))
        alpha = ImageChops.multiply(overlay.getchannel("A"), mask)
        overlay.putalpha(alpha)
        return overlay

    def _paste_fire_pixel_overlay(box: Tuple[int, int, int, int], motif: str, radius: int, inset: int = 4, intensity: float = 1.0) -> None:
        if not fire_theme:
            return
        x0, y0, x1, y1 = box
        inner = (x0 + inset, y0 + inset, x1 - inset, y1 - inset)
        if inner[2] - inner[0] < 12 or inner[3] - inner[1] < 12:
            return
        overlay = _build_fire_pixel_overlay((inner[2] - inner[0], inner[3] - inner[1]), motif, max(4, radius - inset), intensity=intensity)
        img.alpha_composite(overlay, (inner[0], inner[1]))

    def text_fit(text: str, font_options: Sequence[ImageFont.ImageFont], max_width: int) -> Tuple[str, ImageFont.ImageFont]:
        normalized = " ".join(str(text or "").split()) or "-"
        for font in font_options:
            if draw.textlength(normalized, font=font) <= max_width:
                return normalized, font
        font = font_options[-1]
        ellipsis = "..."
        while normalized and draw.textlength(normalized + ellipsis, font=font) > max_width:
            normalized = normalized[:-1]
        return ((normalized.rstrip() + ellipsis) if normalized else ellipsis), font

    def draw_avatar(center: Tuple[int, int], path_value: Optional[str], side: str) -> None:
        size = (84, 84)
        avatar = load_player_avatar_image(Path(str(path_value)) if str(path_value or "").strip() else None, size)
        x = int(center[0] - size[0] / 2)
        y = int(center[1] - size[1] / 2)
        img.alpha_composite(
            blurred_round_rect_layer((size[0] + 18, size[1] + 18), (4, 4, size[0] + 14, size[1] + 14), 24, tuple(accent) + (45,), 2),
            (x - 9, y - 9),
        )
        img.alpha_composite(avatar.convert("RGBA"), (x, y))
        tag = "P1" if side == "player" else "P2"
        draw.rounded_rectangle((x + 55, y + 58, x + 89, y + 83), 8, fill=(8, 12, 24, 230), outline=accent, width=2)
        draw.text((x + 72, y + 70), tag, font=FONT_XS_BOLD, fill=title, anchor="mm")

    def draw_heart_icon(cx: int, cy: int, size: int, fill: Tuple[int, int, int], outline_color: Tuple[int, int, int]) -> None:
        # Draw a true heart silhouette instead of circular life pips.
        # Render at a higher resolution first so the small icons stay smooth.
        scale = 4
        pad = 4 * scale
        canvas_size = (size + 8) * scale
        heart = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
        hd = ImageDraw.Draw(heart)
        points: List[Tuple[float, float]] = []
        for step in range(64):
            t = (math.tau * step) / 64
            px = 16 * (math.sin(t) ** 3)
            py = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
            points.append((px, py))
        min_x = min(px for px, _ in points)
        max_x = max(px for px, _ in points)
        min_y = min(py for _, py in points)
        max_y = max(py for _, py in points)
        span = max(max_x - min_x, max_y - min_y)
        usable = size * scale
        normalized = [
            (
                pad + ((px - min_x) / span) * usable,
                pad + ((py - min_y) / span) * usable,
            )
            for px, py in points
        ]
        hd.polygon(normalized, fill=fill + (255,))
        hd.line(normalized + [normalized[0]], fill=outline_color + (255,), width=max(1, 2 * scale), joint="curve")
        heart = heart.resize((canvas_size // scale, canvas_size // scale), Image.Resampling.LANCZOS)
        img.alpha_composite(heart, (int(cx - heart.width / 2), int(cy - heart.height / 2)))

    def draw_heart_row(x: int, y: int, current: int, maximum: int, anchor: str) -> None:
        maximum = max(1, min(8, int(maximum or BATTLE_HEARTS)))
        current = max(0, min(maximum, int(current or 0)))
        size = 17
        gap = 9
        total = (maximum * size) + ((maximum - 1) * gap)
        start = x - total if anchor == "right" else x
        for idx in range(maximum):
            cx = int(start + idx * (size + gap) + size / 2)
            fill = (255, 80, 128) if idx < current else (55, 62, 82)
            heart_outline = (255, 230, 238) if idx < current else (120, 130, 155)
            draw_heart_icon(cx, y, size, fill, heart_outline)

    def deck_count(runtime: Optional[SupportDeckRuntime]) -> int:
        if runtime is None:
            return 0
        return max(0, len(runtime.shuffled_cards) - runtime.draw_index)

    def extra_deck_count(team: Sequence[Fighter]) -> int:
        # Extra Decks are attached to the active team and copied into each
        # fighter's runtime as a shared pool.  The renderer only needs to know
        # whether that pool exists and still has cards in it so the EXTRA zone
        # can render as occupied.
        return len(extra_deck_pool_for_team(team))

    zone_w, zone_h = 156, 202
    card_w, card_h = 118, 166
    monster_gap = 26
    start_x = int((w - (3 * zone_w + 2 * monster_gap)) / 2)
    # Keep the two monster rows visually separated in the debug renderer,
    # with enough back-row clearance that shadows/glows do not read as overlap.
    enemy_monster_y = 248
    player_monster_y = 482
    equip_w, equip_h = 124, 42
    # Support card zones are card-shaped and nearly TOKEN-sized, but still
    # small enough to sit cleanly in the monster column back row.
    support_w, support_h = 96, 142
    back_row_gap = 19
    # Player 1 reads from the center outward as MONSTER -> EQUIP -> SUPPORT.
    # Player 2 mirrors that layout vertically. The equip zone is centered in
    # the space between the monster and support rows with equal vertical gaps.
    equip_y_player = player_monster_y + zone_h + back_row_gap
    support_y_player = equip_y_player + equip_h + back_row_gap
    equip_y_enemy = enemy_monster_y - back_row_gap - equip_h
    support_y_enemy = equip_y_enemy - back_row_gap - support_h
    player_plate_h = 134
    player_plate_y = int((support_y_player + support_h / 2) - (player_plate_h / 2))
    enemy_plate_y = int((support_y_enemy + support_h / 2) - (player_plate_h / 2))

    def draw_player_plate(side: str, name: str, note: Optional[str], avatar_path: Optional[str], hearts: int, runtime: Optional[SupportDeckRuntime]) -> None:
        is_player = side == "player"
        # Vertically center the player/enemy panels on the support-card zone row
        # without placing the panels on top of those zones.
        y0 = player_plate_y if is_player else enemy_plate_y
        y1 = y0 + player_plate_h
        x0, x1 = (92, 468) if is_player else (982, 1358)
        panel_fill = (42, 10, 5, 232) if fire_theme else (9, 13, 28, 218)
        panel_line = ember if fire_theme else outline
        draw.rounded_rectangle((x0, y0, x1, y1), 28, fill=panel_fill, outline=panel_line + (220,), width=3)
        if fire_theme:
            _paste_fire_pixel_overlay((x0, y0, x1, y1), "panel", 28, inset=5, intensity=0.95)
            draw.line((x0 + 22, y1 - 12, x1 - 22, y1 - 12), fill=lava_hot + (140,), width=3)
        avatar_center = (x0 + 62, (y0 + y1) // 2)
        draw_avatar(avatar_center, avatar_path, side)
        tx = x0 + 124
        # Stack the player name, compact deck counter, and hearts with even breathing room.
        deck_box_w = 76
        deck_box_h = 18
        deck_text = f"MAIN {deck_count(runtime):02d}"
        deck_font = get_bold_font(11)
        deck_text, deck_font = text_fit(deck_text, [deck_font, FONT_XS_BOLD, FONT_XS], deck_box_w - 8)
        name_text, name_font = text_fit(name, [FONT_L_BOLD, FONT_M_BOLD, FONT_S_BOLD], x1 - tx - 20)
        name_center_y = y0 + 34
        hearts_y = y1 - 31
        deck_center_y = int((name_center_y + hearts_y) / 2)
        deck_box_y0 = deck_center_y - deck_box_h // 2
        deck_box_y1 = deck_center_y + deck_box_h // 2
        draw.text((tx, name_center_y), name_text, font=name_font, fill=title, anchor="lm")
        draw.rounded_rectangle((tx, deck_box_y0, tx + deck_box_w, deck_box_y1), 7, fill=accent + (48,), outline=accent + (160,), width=1)
        draw.text((tx + deck_box_w // 2, deck_center_y), deck_text, font=deck_font, fill=title, anchor="mm")
        if note:
            note_x = tx + deck_box_w + 8
            note_text, note_font = text_fit(note, [FONT_XS_BOLD, FONT_XS], x1 - note_x - 12)
            draw.text((note_x, deck_center_y), note_text, font=note_font, fill=subtitle, anchor="lm")
        draw_heart_row(tx, hearts_y, hearts, max_hearts, "left")

    draw_player_plate("player", player_name, player_header_note, player_avatar_path, player_hearts, player_support_runtime)
    draw_player_plate("enemy", enemy_name, enemy_header_note, enemy_avatar_path, int(enemy_hearts if enemy_hearts is not None else BATTLE_HEARTS), enemy_support_runtime)

    if tick_info or round_info:
        status = "   /   ".join(part for part in [round_info, tick_info] if part)
        status_text, status_font = text_fit(status, [FONT_M_BOLD, FONT_S_BOLD, FONT_XS_BOLD], 360)
        counter_box = (80, 47, 440, 92)
        draw.rounded_rectangle(counter_box, 18, fill=((44, 10, 5, 236) if fire_theme else (8, 12, 24, 230)), outline=(ember if fire_theme else accent) + (210 if fire_theme else 180,), width=2)
        if fire_theme:
            _paste_fire_pixel_overlay(counter_box, "panel", 18, inset=4, intensity=0.85)
        draw.text(((counter_box[0] + counter_box[2]) / 2, (counter_box[1] + counter_box[3]) / 2), status_text.upper(), font=status_font, fill=title, anchor="mm")

    zone_radius = 18

    def zone_color(kind: str, occupied: bool = False) -> Tuple[int, int, int, int]:
        colors = (
            {
                "monster": blend(lava_mid, accent, 0.26),
                "equip": (74, 20, 8),
                "support": (68, 24, 10),
                "extra": (76, 18, 9),
                "deck": (64, 27, 8),
                "token": (78, 34, 12),
                "gy": (58, 20, 12),
            }
            if fire_theme
            else {
                "monster": blend(base, accent, 0.22),
                "equip": (42, 36, 60),
                "support": (36, 42, 68),
                "extra": (52, 38, 70),
                "deck": (30, 48, 70),
                "token": (52, 58, 70),
                "gy": (52, 52, 58),
            }
        )
        rgb = colors.get(kind, base)
        return rgb + ((210 if occupied else 110),)

    def draw_zone_box(
        box: Tuple[int, int, int, int],
        label: str,
        kind: str,
        occupied: bool = False,
        active: bool = False,
        targeted: bool = False,
        *,
        inner_outline: bool = True,
        outer_outline: bool = True,
        empty_label_center: bool = False,
    ) -> None:
        x0, y0, x1, y1 = box
        z_outline = accent if active else ((255, 132, 132) if targeted else outline)
        width_line = 4 if (active or targeted) else 2
        img.alpha_composite(
            blurred_round_rect_layer((x1 - x0 + 20, y1 - y0 + 20), (8, 8, x1 - x0 + 12, y1 - y0 + 12), zone_radius, (0, 0, 0, 135), 5),
            (x0 - 10, y0 - 8),
        )
        if outer_outline:
            draw.rounded_rectangle(box, zone_radius, fill=zone_color(kind, occupied), outline=z_outline + (235,), width=width_line)
        else:
            draw.rounded_rectangle(box, zone_radius, fill=zone_color(kind, occupied))
        if fire_theme:
            motif = {"monster": "monster", "equip": "equip", "support": "support", "extra": "extra", "deck": "deck", "token": "token", "gy": "support"}.get(kind, "panel")
            _paste_fire_pixel_overlay(box, motif, zone_radius, inset=6, intensity=0.92 if occupied else 0.78)
        if inner_outline:
            inset = 10
            inner_line = ember + (60,) if fire_theme else (255, 255, 255, 52)
            draw.rounded_rectangle((x0 + inset, y0 + inset, x1 - inset, y1 - inset), max(8, zone_radius - 4), outline=inner_line, width=1)
        if fire_theme:
            crack_y = y0 + int((y1 - y0) * 0.72)
            draw.line((x0 + 13, crack_y, x0 + 36, crack_y - 7, x0 + 55, crack_y + 2), fill=lava_hot + (92,), width=2)
            draw.line((x1 - 18, y0 + 24, x1 - 42, y0 + 38, x1 - 68, y0 + 34), fill=ember + (58,), width=1)
        if not occupied:
            label_text, label_font = text_fit(label, [FONT_XS_BOLD, get_font(11), get_font(10)], x1 - x0 - 20)
            label_y = ((y0 + y1) // 2) if empty_label_center else (y1 - 13)
            draw.text(((x0 + x1) // 2, label_y), label_text, font=label_font, fill=((255, 202, 128, 205) if fire_theme else (210, 220, 245, 185)), anchor="mm")

    def draw_back_card(box: Tuple[int, int, int, int], label: str, count_text: str, kind: str, *, show_support_back: bool = False) -> None:
        empty_deck_like = kind in {"deck", "extra"} and not show_support_back
        visual_kind = "deck" if empty_deck_like else kind
        occupied = show_support_back or not empty_deck_like
        draw_zone_box(box, label, visual_kind, occupied=occupied, inner_outline=False)
        x0, y0, x1, y1 = box
        # Occupied deck zones should behave like occupied support zones: the card
        # image fills the card-shaped zone instead of shrinking into a tiny inner
        # panel. The card image mask/outline should wrap the visible image, not the full zone.
        art_box = (x0 + 6, y0 + 6, x1 - 6, y1 - 6) if show_support_back else (x0 + 28, y0 + 22, x1 - 28, y1 - 34)
        if show_support_back:
            thumb = open_card_image_contain_tight(str(SUPPORT_CARD_IMAGE_PATH), (art_box[2] - art_box[0], art_box[3] - art_box[1]), radius=12)
            thumb_x = art_box[0] + int(((art_box[2] - art_box[0]) - thumb.width) / 2)
            thumb_y = art_box[1] + int(((art_box[3] - art_box[1]) - thumb.height) / 2)
            img.paste(thumb, (thumb_x, thumb_y), thumb)
            thumb_box = (thumb_x, thumb_y, thumb_x + thumb.width, thumb_y + thumb.height)
            draw.rounded_rectangle(thumb_box, 12, outline=accent + (220,), width=2)
            count_badge_w = min(x1 - x0 - 22, max(44, int(draw.textlength(count_text, font=FONT_XS_BOLD)) + 22))
            count_badge = (
                int((x0 + x1 - count_badge_w) / 2),
                y1 - 32,
                int((x0 + x1 + count_badge_w) / 2),
                y1 - 10,
            )
            draw.rounded_rectangle(count_badge, 8, fill=(8, 12, 24, 232), outline=accent + (225,), width=1)
            draw.text(((count_badge[0] + count_badge[2]) / 2, (count_badge[1] + count_badge[3]) / 2), count_text, font=FONT_XS_BOLD, fill=title, anchor="mm")
            return
        elif empty_deck_like:
            draw.text(((x0 + x1) // 2, (y0 + y1) // 2), "EMPTY", font=FONT_XS_BOLD, fill=(170, 180, 210, 115), anchor="mm")
            return
        else:
            draw.rounded_rectangle(art_box, 12, fill=((42, 11, 5, 248) if fire_theme else (15, 18, 34, 245)), outline=(ember if fire_theme else accent) + (220,), width=2)
            for k in range(5):
                draw.line((art_box[0] + 12, art_box[1] + 18 + k * 23, art_box[2] - 12, art_box[1] + 6 + k * 23), fill=(lava_hot if fire_theme else accent) + (92 if fire_theme else 70,), width=3)
        draw.text(((x0 + x1) // 2, (y0 + y1) // 2), count_text, font=FONT_M_BOLD, fill=title, anchor="mm")

    def support_at(runtime: Optional[SupportDeckRuntime], index: int) -> Optional[SupportCardDef]:
        if runtime is None or index < 0 or index >= len(runtime.active_cards):
            return None
        return runtime.active_cards[index]

    def draw_debug_name_badge(
        center_x: int,
        center_y: int,
        text: str,
        max_width: int,
        *,
        fill: Tuple[int, int, int, int] = (8, 12, 24, 232),
        outline_color: Tuple[int, int, int] = outline,
        text_color: Tuple[int, int, int] = title,
    ) -> None:
        badge_text, badge_font = text_fit(text, [FONT_XS_BOLD, get_bold_font(10), get_font(9)], max_width - 16)
        badge_w = min(max_width, max(42, int(math.ceil(draw.textlength(badge_text, font=badge_font))) + 18))
        badge_h = 22
        badge_box = (
            int(center_x - badge_w / 2),
            int(center_y - badge_h / 2),
            int(center_x + badge_w / 2),
            int(center_y + badge_h / 2),
        )
        draw.rounded_rectangle(badge_box, 8, fill=fill, outline=outline_color + (225,), width=1)
        draw.text(((badge_box[0] + badge_box[2]) / 2, (badge_box[1] + badge_box[3]) / 2), badge_text, font=badge_font, fill=text_color, anchor="mm")

    def draw_equip_spell_zone(box: Tuple[int, int, int, int], fighter: Optional[Fighter], index: int) -> None:
        equip_name = active_equip_spell_name(fighter) if fighter is not None else ""
        zone_label = f"EQUIP {index + 1}" if equip_name else "EQUIP"
        draw_zone_box(box, zone_label, "equip", occupied=bool(equip_name), inner_outline=False, empty_label_center=not bool(equip_name))
        x0, y0, x1, y1 = box
        if not equip_name:
            return
        # Equip spells in the debug renderer are labeled with compact badges only;
        # do not draw an extra interior panel inside the equip zone.
        draw_debug_name_badge(
            (x0 + x1) // 2,
            (y0 + y1) // 2,
            equip_name,
            x1 - x0 - 18,
            fill=((66, 17, 7, 238) if fire_theme else (28, 22, 44, 232)),
            outline_color=(ember if fire_theme else (180, 130, 255)),
            text_color=(title if fire_theme else (232, 222, 255)),
        )

    def draw_support_card_zone(box: Tuple[int, int, int, int], card: Optional[SupportCardDef], index: int) -> None:
        zone_label = f"SUPPORT {index + 1}" if card is not None else "SUPPORT"
        draw_zone_box(box, zone_label, "support", occupied=card is not None)
        x0, y0, x1, y1 = box
        if card is None:
            draw.text(((x0 + x1) // 2, (y0 + y1) // 2), "EMPTY", font=get_bold_font(8), fill=(170, 180, 210, 115), anchor="mm")
            return
        # Support zones use a portrait/card-shaped frame, plus a name badge for
        # the active support card so the debug view is readable at a glance.
        inner = (x0 + 6, y0 + 6, x1 - 6, y1 - 6)
        thumb = open_card_image_contain_tight(str(SUPPORT_CARD_IMAGE_PATH), (inner[2] - inner[0], inner[3] - inner[1]), radius=10)
        thumb_x = inner[0] + int(((inner[2] - inner[0]) - thumb.width) / 2)
        thumb_y = inner[1] + int(((inner[3] - inner[1]) - thumb.height) / 2)
        img.paste(thumb, (thumb_x, thumb_y), thumb)
        outline_color = element_color(card.attribute)
        thumb_box = (thumb_x, thumb_y, thumb_x + thumb.width, thumb_y + thumb.height)
        draw.rounded_rectangle(thumb_box, 10, outline=outline_color, width=2)
        draw_debug_name_badge(
            (x0 + x1) // 2,
            y1 - 18,
            card.name,
            x1 - x0 - 10,
            fill=(8, 12, 24, 238),
            outline_color=outline_color,
            text_color=title,
        )

    def _debug_initial_stat(fighter: Fighter, key: str, current_value: int) -> int:
        runtime_key = f"debug_initial_{key.lower()}"
        if runtime_key not in fighter.runtime:
            fighter.runtime[runtime_key] = max(1, int(current_value or 1))
        try:
            return max(1, int(fighter.runtime.get(runtime_key) or current_value or 1))
        except Exception:
            return max(1, int(current_value or 1))

    def fighter_display_stats(fighter: Fighter) -> List[Tuple[str, int, int, Tuple[int, int, int]]]:
        active_cards = ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(fighter), [])
        current_values = {
            "ATK": effective_atk(fighter, active_cards),
            "DEF": effective_defense(fighter, active_cards),
            "SPD": effective_spd(fighter, active_cards),
        }
        initial_values = {
            "ATK": _debug_initial_stat(fighter, "ATK", int(fighter.atk)),
            "DEF": _debug_initial_stat(fighter, "DEF", int(fighter.defense)),
            "SPD": _debug_initial_stat(fighter, "SPD", int(fighter.spd)),
        }
        boosted = (100, 245, 142)
        lowered = (255, 104, 112)
        normal = card_text
        stats: List[Tuple[str, int, int, Tuple[int, int, int]]] = []
        for label in ("ATK", "DEF", "SPD"):
            current = max(1, int(current_values[label]))
            initial = max(1, int(initial_values[label]))
            fill = boosted if current > initial else lowered if current < initial else normal
            stats.append((label, current, initial, fill))
        return stats

    def _draw_debug_hp_strip(card_layer: Image.Image, xy: Tuple[int, int, int, int], current: int, maximum: int) -> None:
        cd = ImageDraw.Draw(card_layer)
        x0, y0, x1, y1 = xy
        ratio = 0.0 if maximum <= 0 else max(0.0, min(1.0, float(current) / float(maximum)))
        cd.rounded_rectangle(xy, 8, fill=((42, 9, 4, 224) if fire_theme else (5, 8, 18, 215)), outline=((255, 188, 86, 90) if fire_theme else (255, 255, 255, 72)), width=1)
        fill_w = int((x1 - x0 - 4) * ratio)
        if fill_w > 0:
            hp_fill = ((255, 170, 42) if ratio > 0.5 else (255, 92, 20) if ratio > 0.2 else (138, 20, 12)) if fire_theme else ((86, 230, 132) if ratio > 0.5 else (244, 202, 82) if ratio > 0.2 else (255, 90, 102))
            cd.rounded_rectangle((x0 + 2, y0 + 2, x0 + 2 + fill_w, y1 - 2), 6, fill=hp_fill + (232,))
        cd.text(((x0 + x1) // 2, (y0 + y1) // 2), hp_percent_text(current, maximum), font=get_bold_font(9), fill=(255, 255, 255, 245), anchor="mm")

    def _draw_debug_speed_strip(card_layer: Image.Image, xy: Tuple[int, int, int, int], fighter: Fighter) -> None:
        cd = ImageDraw.Draw(card_layer)
        x0, y0, x1, y1 = xy
        maximum = float(SPEED_GAUGE_MAX or 100.0)
        ratio = max(0.0, min(1.0, float(getattr(fighter, "speed_gauge", 0.0) or 0.0) / maximum))
        cd.rounded_rectangle(xy, 5, fill=((42, 9, 4, 214) if fire_theme else (5, 8, 18, 205)), outline=((255, 200, 90, 94) if fire_theme else (145, 220, 255, 82)), width=1)
        fill_w = int((x1 - x0 - 4) * ratio)
        if fill_w > 0:
            cd.rounded_rectangle((x0 + 2, y0 + 2, x0 + 2 + fill_w, y1 - 2), 4, fill=((255, 121, 26, 232) if fire_theme else (92, 212, 255, 232)))

    def _draw_debug_stat_hud(card_layer: Image.Image, fighter: Fighter, art_outline: Tuple[int, int, int]) -> None:
        cd = ImageDraw.Draw(card_layer)
        hud_h = 49
        y0 = card_h - hud_h
        # Build the HUD into the card art instead of floating a separate badge over the zone.
        cd.rounded_rectangle((3, y0 - 2, card_w - 3, card_h - 3), 12, fill=((42, 9, 4, 228) if fire_theme else (3, 7, 17, 224)), outline=(ember if fire_theme else art_outline) + (205,), width=1)
        cd.line((10, y0 + 3, card_w - 10, y0 + 3), fill=(255, 255, 255, 55), width=1)
        stat_font = get_bold_font(10)
        label_font = get_bold_font(8)
        stats = fighter_display_stats(fighter)
        col_w = (card_w - 12) / 3.0
        for idx, (label, current, _initial, fill) in enumerate(stats):
            cx = int(6 + (idx * col_w) + col_w / 2)
            if idx > 0:
                divider_x = int(6 + idx * col_w)
                cd.line((divider_x, y0 + 10, divider_x, card_h - 8), fill=(255, 255, 255, 32), width=1)
            cd.text((cx, y0 + 14), label, font=label_font, fill=(178, 190, 218, 235), anchor="mm")
            value_text = str(current)
            value_text, value_font = text_fit(value_text, [stat_font, get_bold_font(9), get_bold_font(8)], int(col_w) - 4)
            # A tiny text shadow keeps red/green numbers readable against busy card art.
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                cd.text((cx + dx, y0 + 31 + dy), value_text, font=value_font, fill=(0, 0, 0, 205), anchor="mm")
            cd.text((cx, y0 + 31), value_text, font=value_font, fill=fill + (255,), anchor="mm")

    def draw_monster_zone(fighter: Optional[Fighter], box: Tuple[int, int, int, int]) -> None:
        active = fighter is not None and active_fighter is fighter
        targeted = fighter is not None and target_fighter is fighter
        draw_zone_box(box, "MONSTER", "monster", occupied=fighter is not None, active=active, targeted=targeted, inner_outline=False)
        x0, y0, x1, y1 = box
        if fighter is None:
            draw.text(((x0 + x1) // 2, (y0 + y1) // 2), "EMPTY", font=FONT_XS_BOLD, fill=(170, 180, 210, 115), anchor="mm")
            return
        card_x = x0 + (x1 - x0 - card_w) // 2
        card_y = y0 + (y1 - y0 - card_h) // 2
        card = open_card_image(fighter.image_path, (card_w, card_h), radius=14, grayscale=not fighter.alive)
        art_outline = element_color(fighter.attribute)
        card_layer = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        card_layer.alpha_composite(card.convert("RGBA"), (0, 0))
        # Soft top/bottom fades make the HP strip and stat HUD feel embedded in the card frame.
        overlay = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle((0, 0, card_w, 30), 13, fill=(0, 0, 0, 74))
        od.rounded_rectangle((0, card_h - 58, card_w, card_h), 13, fill=(0, 0, 0, 132))
        card_layer.alpha_composite(overlay)
        _draw_debug_hp_strip(card_layer, (8, 7, card_w - 8, 19), fighter.hp, fighter.max_hp)
        _draw_debug_speed_strip(card_layer, (16, 22, card_w - 16, 29), fighter)
        _draw_debug_stat_hud(card_layer, fighter, art_outline)
        img.paste(card_layer, (card_x, card_y), card_layer)
        draw.rounded_rectangle((card_x, card_y, card_x + card_w, card_y + card_h), 13, outline=art_outline, width=3)
        draw.rounded_rectangle((card_x + 4, card_y + 4, card_x + card_w - 4, card_y + card_h - 4), 11, outline=(255, 255, 255, 42), width=1)
        if not fighter.alive:
            draw.rounded_rectangle((card_x + 18, card_y + 62, card_x + card_w - 18, card_y + 104), 10, fill=(0, 0, 0, 210), outline=(188, 188, 194), width=2)
            draw.text((card_x + card_w // 2, card_y + 83), "KO", font=FONT_M_BOLD, fill=(215, 218, 226), anchor="mm")
        if active and fighter.alive:
            draw.rounded_rectangle((x0 + 24, y0 - 13, x1 - 24, y0 + 14), 9, fill=(255, 226, 110, 235), outline=(255, 255, 235), width=1)
            draw.text(((x0 + x1) // 2, y0), "TURN", font=FONT_XS_BOLD, fill=(24, 20, 6), anchor="mm")
        if targeted and fighter.alive:
            draw.rounded_rectangle((x0 + 18, y1 - 10, x1 - 18, y1 + 16), 9, fill=(130, 28, 42, 235), outline=(255, 150, 160), width=1)
            draw.text(((x0 + x1) // 2, y1 + 3), "TARGET", font=FONT_XS_BOLD, fill=(255, 238, 240), anchor="mm")

    monster_boxes_enemy = []
    monster_boxes_player = []
    equip_boxes_enemy = []
    equip_boxes_player = []
    support_boxes_enemy = []
    support_boxes_player = []
    for i in range(3):
        x0 = start_x + i * (zone_w + monster_gap)
        monster_boxes_enemy.append((x0, enemy_monster_y, x0 + zone_w, enemy_monster_y + zone_h))
        monster_boxes_player.append((x0, player_monster_y, x0 + zone_w, player_monster_y + zone_h))
        equip_x = x0 + (zone_w - equip_w) // 2
        support_x = x0 + (zone_w - support_w) // 2
        equip_boxes_enemy.append((equip_x, equip_y_enemy, equip_x + equip_w, equip_y_enemy + equip_h))
        support_boxes_enemy.append((support_x, support_y_enemy, support_x + support_w, support_y_enemy + support_h))
        equip_boxes_player.append((equip_x, equip_y_player, equip_x + equip_w, equip_y_player + equip_h))
        support_boxes_player.append((support_x, support_y_player, support_x + support_w, support_y_player + support_h))

    for i, box in enumerate(monster_boxes_enemy):
        draw_monster_zone(enemies[i] if i < len(enemies) else None, box)
    for i, box in enumerate(monster_boxes_player):
        draw_monster_zone(allies[i] if i < len(allies) else None, box)
    for i, box in enumerate(equip_boxes_enemy):
        draw_equip_spell_zone(box, enemies[i] if i < len(enemies) else None, i)
    for i, box in enumerate(equip_boxes_player):
        draw_equip_spell_zone(box, allies[i] if i < len(allies) else None, i)
    for i, box in enumerate(support_boxes_enemy):
        draw_support_card_zone(box, support_at(enemy_support_runtime, i), i)
    for i, box in enumerate(support_boxes_player):
        draw_support_card_zone(box, support_at(player_support_runtime, i), i)

    def team_tokens(team: Sequence[Fighter]) -> List[Tuple[Fighter, dict]]:
        tokens: List[Tuple[Fighter, dict]] = []
        for fighter in team:
            summon = runtime_flag(fighter, "summon_data", None)
            if isinstance(summon, dict) and int(summon.get("hp", 0) or 0) > 0:
                tokens.append((fighter, summon))
        return tokens

    def draw_token_zone(box: Tuple[int, int, int, int], team: Sequence[Fighter]) -> None:
        tokens = team_tokens(team)
        count = len(tokens)
        draw_zone_box(box, "TOKEN", "token", occupied=count > 0)
        x0, y0, x1, y1 = box
        if count <= 0:
            draw.text(((x0 + x1) // 2, (y0 + y1) // 2), "EMPTY", font=FONT_XS_BOLD, fill=(170, 180, 210, 115), anchor="mm")
            return
        source_fighter, summon = tokens[0]
        token_image_path = str(summon.get("image_path") or source_fighter.image_path or "")
        token_outline = element_color(str(summon.get("attribute") or source_fighter.attribute))
        art_box = (x0 + 9, y0 + 9, x1 - 9, y1 - 9)
        token_art = open_card_image(token_image_path, (art_box[2] - art_box[0], art_box[3] - art_box[1]), radius=14, grayscale=not source_fighter.alive)
        art_x = art_box[0] + int(((art_box[2] - art_box[0]) - token_art.width) / 2)
        art_y = art_box[1] + int(((art_box[3] - art_box[1]) - token_art.height) / 2)
        img.paste(token_art, (art_x, art_y), token_art)
        draw.rounded_rectangle(art_box, 14, outline=token_outline, width=3)
        if count > 1:
            badge_w, badge_h = 58, 24
            badge_box = (int((x0 + x1 - badge_w) / 2), y1 - badge_h - 14, int((x0 + x1 + badge_w) / 2), y1 - 14)
            draw.rounded_rectangle(badge_box, 8, fill=(8, 12, 24, 226), outline=token_outline + (220,), width=1)
            draw.text(((badge_box[0] + badge_box[2]) / 2, (badge_box[1] + badge_box[3]) / 2), f"x{count}", font=FONT_XS_BOLD, fill=title, anchor="mm")


    side_w, side_h = 116, 172
    player_side_y = player_monster_y + 15
    enemy_side_y = enemy_monster_y + 15
    monster_row_right = start_x + 3 * zone_w + 2 * monster_gap
    extra_col_x = start_x - 280
    main_col_x = start_x - 146
    side_zone_gap = start_x - (main_col_x + side_w)
    token_col_x = monster_row_right + side_zone_gap
    player_deck_count = deck_count(player_support_runtime)
    enemy_deck_count = deck_count(enemy_support_runtime)
    player_extra_deck_count = extra_deck_count(allies)
    enemy_extra_deck_count = extra_deck_count(enemies)
    draw_back_card((extra_col_x, player_side_y, extra_col_x + side_w, player_side_y + side_h), "EXTRA", f"{player_extra_deck_count:02d}", "extra", show_support_back=player_extra_deck_count > 0)
    draw_back_card((main_col_x, player_side_y, main_col_x + side_w, player_side_y + side_h), "MAIN", f"{player_deck_count:02d}", "deck", show_support_back=player_deck_count > 0)
    draw_token_zone((token_col_x, player_side_y, token_col_x + side_w, player_side_y + side_h), allies)
    draw_back_card((extra_col_x, enemy_side_y, extra_col_x + side_w, enemy_side_y + side_h), "EXTRA", f"{enemy_extra_deck_count:02d}", "extra", show_support_back=enemy_extra_deck_count > 0)
    draw_back_card((main_col_x, enemy_side_y, main_col_x + side_w, enemy_side_y + side_h), "MAIN", f"{enemy_deck_count:02d}", "deck", show_support_back=enemy_deck_count > 0)
    draw_token_zone((token_col_x, enemy_side_y, token_col_x + side_w, enemy_side_y + side_h), enemies)

    def debug_badge_colors(label: str) -> Tuple[Tuple[int, int, int], Tuple[int, int, int], Tuple[int, int, int]]:
        upper = str(label or "").upper()
        if upper == "OK":
            return (58, 62, 72), (142, 148, 164), (238, 240, 245)
        if any(term in upper for term in ("IMMUNITY", "RECOVERY", "PROTECTION", "SHIELD", "TOKEN", "PRECOG", "LIFESTEAL", "REFLECT", "COUNTER")):
            return (36, 78, 54), (105, 214, 147), (230, 255, 240)
        if any(term in upper for term in ("BURN", "POISON", "STUN", "FREEZE", "PETRIFY", "TAUNT", "FEAR", "SLEEP", "CHARM", "SILENCE", "BOMB", "HEAL BLOCK", "MARK", "KO")):
            return (86, 38, 44), (232, 120, 120), (255, 236, 236)
        return blend(base, accent, 0.12), outline, title

    def draw_debug_status_panel(panel_box: Tuple[int, int, int, int]) -> None:
        left, top, right, bottom = panel_box
        draw.rounded_rectangle(panel_box, 22, fill=((42, 10, 5, 235) if fire_theme else (9, 13, 28, 226)), outline=(ember if fire_theme else outline) + (220 if fire_theme else 210,), width=3)
        if fire_theme:
            _paste_fire_pixel_overlay(panel_box, "status", 22, inset=5, intensity=0.92)
            draw.line((left + 20, bottom - 16, right - 20, bottom - 16), fill=lava_hot + (145,), width=3)
        draw.text(((left + right) / 2, top + 20), "Monster Status", font=FONT_S_BOLD, fill=title, anchor="mm")
        mid = (left + right) // 2
        draw.line((mid, top + 44, mid, bottom - 12), fill=outline + (150,), width=2)

        def draw_column(label: str, team: Sequence[Fighter], col_left: int, col_right: int) -> None:
            draw.text(((col_left + col_right) / 2, top + 46), label, font=FONT_XS_BOLD, fill=subtitle, anchor="mm")
            rows_top = top + 60
            rows_bottom = bottom - 12
            row_gap = 7
            row_h = max(42, int((rows_bottom - rows_top - row_gap * 2) / 3))
            for idx in range(3):
                row_top = rows_top + idx * (row_h + row_gap)
                row_bottom = min(rows_bottom, row_top + row_h)
                fighter = team[idx] if idx < len(team) else None
                row_box = (col_left, row_top, col_right, row_bottom)
                row_outline = element_color(fighter.attribute) if fighter is not None and fighter.alive else outline
                draw.rounded_rectangle(row_box, 13, fill=(blend(lava_mid, accent, 0.16) if fire_theme else blend(base, accent, 0.08)) + (218 if fire_theme else 210,), outline=(ember if fire_theme else row_outline) + (205 if fire_theme else 190,), width=2)
                if fire_theme:
                    _paste_fire_pixel_overlay(row_box, "status", 13, inset=4, intensity=0.72)
                if fighter is None:
                    draw.text(((col_left + col_right) / 2, (row_top + row_bottom) / 2), "EMPTY", font=get_bold_font(9), fill=(170, 180, 210, 115), anchor="mm")
                    continue
                name_text, name_font = text_fit(fighter.name, [get_bold_font(10), get_bold_font(9), get_font(8)], col_right - col_left - 12)
                draw.text(((col_left + col_right) / 2, row_top + 10), name_text, font=name_font, fill=title, anchor="mm")
                badges = battle_status_badge_labels(fighter) or (["OK"] if fighter.alive else ["KO"])
                badge_gap = 4
                badge_y = row_top + 22
                badge_h = 15
                fitted: List[Tuple[str, ImageFont.ImageFont, int, Tuple[int, int, int], Tuple[int, int, int], Tuple[int, int, int]]] = []
                max_badges = min(3, len(badges))
                for badge in badges[:max_badges]:
                    badge_text, badge_font = text_fit(badge, [get_bold_font(8), get_font(8)], 66)
                    fill_c, line_c, text_c = debug_badge_colors(badge_text)
                    bw = max(30, min(70, int(draw.textlength(badge_text, font=badge_font) + 12)))
                    fitted.append((badge_text, badge_font, bw, fill_c, line_c, text_c))
                total_w = sum(item[2] for item in fitted) + badge_gap * max(0, len(fitted) - 1)
                bx = int((col_left + col_right - total_w) / 2)
                for badge_text, badge_font, bw, fill_c, line_c, text_c in fitted:
                    badge_box = (bx, badge_y, bx + bw, badge_y + badge_h)
                    draw.rounded_rectangle(badge_box, 7, fill=fill_c, outline=line_c, width=1)
                    draw.text(((badge_box[0] + badge_box[2]) / 2, (badge_box[1] + badge_box[3]) / 2), badge_text, font=badge_font, fill=text_c, anchor="mm")
                    bx += bw + badge_gap

        draw_column("Player", allies, left + 14, mid - 8)
        draw_column("Enemy", enemies, mid + 8, right - 14)

    draw_debug_status_panel((982, 692, 1368, 918))

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


def render_battle(player_name: str, enemy_name: str, allies: List[Fighter], enemies: List[Fighter], log_lines: List[str], active_fighter: Optional[Fighter] = None, target_fighter: Optional[Fighter] = None, tick_info: Optional[str] = None, round_info: Optional[str] = None, player_support_runtime: Optional[SupportDeckRuntime] = None, enemy_support_runtime: Optional[SupportDeckRuntime] = None, player_hearts: int = BATTLE_HEARTS, enemy_hearts: Optional[int] = BATTLE_HEARTS, max_hearts: int = BATTLE_HEARTS, theme_name: str = "gray", player_header_note: Optional[str] = None, enemy_header_note: Optional[str] = None, player_avatar_path: Optional[str] = None, enemy_avatar_path: Optional[str] = None) -> io.BytesIO:
    # The Speed Duel field renderer is now the standard battle renderer.
    return render_battle_debug_speed_duel_field(
        player_name,
        enemy_name,
        allies,
        enemies,
        log_lines,
        active_fighter=active_fighter,
        target_fighter=target_fighter,
        tick_info=tick_info,
        round_info=round_info,
        player_support_runtime=player_support_runtime,
        enemy_support_runtime=enemy_support_runtime,
        player_hearts=player_hearts,
        enemy_hearts=enemy_hearts,
        max_hearts=max_hearts,
        theme_name=theme_name,
        player_header_note=player_header_note,
        enemy_header_note=enemy_header_note,
        player_avatar_path=player_avatar_path,
        enemy_avatar_path=enemy_avatar_path,
    )

    def fit_text_to_width(text_value: str, max_width: int, font_candidates: Sequence[ImageFont.ImageFont], *, allow_ellipsis: bool = True) -> Tuple[str, ImageFont.ImageFont]:
        normalized = " ".join(str(text_value or "").split()) or "-"
        fallback_font = font_candidates[-1]
        for candidate_font in font_candidates:
            if draw.textlength(normalized, font=candidate_font) <= max_width:
                return normalized, candidate_font
        chosen_font = fallback_font
        if not allow_ellipsis:
            return normalized, chosen_font
        ellipsis = "..."
        if draw.textlength(ellipsis, font=chosen_font) > max_width:
            return "", chosen_font
        clipped = normalized
        while clipped and draw.textlength(clipped + ellipsis, font=chosen_font) > max_width:
            clipped = clipped[:-1]
        clipped = clipped.rstrip(" ,.-")
        return ((clipped + ellipsis) if clipped else ellipsis), chosen_font

    w, h = 1450, 840
    palette = get_ui_theme_palette(theme_name)
    bg_fill = tuple(max(0, int(channel * 0.45)) for channel in palette["panel_fill"])
    bubble_fill = tuple(max(0, int(channel * 0.6)) for channel in palette["panel_fill"])
    log_fill = tuple(max(0, int(channel * 0.4)) for channel in palette["panel_fill"])
    img = Image.new("RGBA", (w, h), bg_fill + (255,))
    draw = ImageDraw.Draw(img)

    y = 172
    card_size = (135, 135)
    support_thumb_size = (54, 78)
    fighter_stats_bottom = y + 268
    support_title_gap = 24
    support_row_y = fighter_stats_bottom + support_title_gap + 18
    bubble_top = 128
    bubble_bottom = support_row_y + support_thumb_size[1] + 20
    draw.rounded_rectangle((25, bubble_top, w - 25, bubble_bottom), 26, outline=palette["outer_outline"], width=3, fill=bubble_fill)

    slot_gap = 35
    formation_width = TEAM_SIZE * card_size[0] + (TEAM_SIZE - 1) * slot_gap
    left_start = 70
    right_start = w - left_start - formation_width
    ally_x = [left_start + i * (card_size[0] + slot_gap) for i in range(TEAM_SIZE)]
    enemy_x = [right_start + i * (card_size[0] + slot_gap) for i in range(TEAM_SIZE)]
    left_center_x = left_start + (formation_width // 2)
    right_center_x = right_start + (formation_width // 2)

    def deck_cards_left(runtime: Optional[SupportDeckRuntime]) -> int:
        if runtime is None:
            return 0
        return max(0, len(runtime.shuffled_cards) - runtime.draw_index)

    def deck_label(runtime: Optional[SupportDeckRuntime]) -> str:
        if runtime is None:
            return "No Support Deck"
        return f"{runtime.deck_name}: {deck_cards_left(runtime)} cards left"

    header_width = formation_width + 30
    center_panel_width = 320
    center_panel_left = int((w - center_panel_width) / 2)
    center_panel_right = center_panel_left + center_panel_width
    deck_font_options = [FONT_S_BOLD, FONT_XS_BOLD, FONT_XS]
    player_deck_text, player_deck_font = fit_text_to_width(deck_label(player_support_runtime), header_width, deck_font_options)
    enemy_deck_text, enemy_deck_font = fit_text_to_width(deck_label(enemy_support_runtime), header_width, deck_font_options)
    draw.text((left_center_x, 22), player_deck_text, font=player_deck_font, fill=palette["info_text"], anchor="ma")
    draw.text((right_center_x, 22), enemy_deck_text, font=enemy_deck_font, fill=palette["info_text"], anchor="ma")
    duelist_name_center_y = 60 if (player_header_note or enemy_header_note) else 68
    duelist_font_options = [FONT_XL, FONT_L_BOLD, FONT_M_BOLD, FONT_S_BOLD]
    duelist_avatar_size = (88, 88)
    duelist_avatar_gap = 28
    duelist_avatar_center_y = bubble_top - (duelist_avatar_size[1] // 2) - 2

    def draw_duelist_header(center_x: int, name: str, avatar_path_value: Optional[str], header_note: Optional[str], avatar_center_x: int):
        header_note_font_options = [FONT_S_BOLD, FONT_XS_BOLD, FONT_XS]
        max_name_width = header_width - 24
        name_text, name_font = fit_text_to_width(name, max_name_width, duelist_font_options)
        draw.text((center_x, duelist_name_center_y), name_text, font=name_font, fill=palette["title"], anchor="mm")
        if header_note:
            note_text, note_font = fit_text_to_width(header_note, header_width, header_note_font_options)
            draw.text((center_x, duelist_name_center_y + 28), note_text, font=note_font, fill=palette["subtitle"], anchor="mm")
        avatar_x = int(avatar_center_x - duelist_avatar_size[0] / 2)
        avatar_y = int(duelist_avatar_center_y - duelist_avatar_size[1] / 2)
        avatar = load_player_avatar_image(Path(str(avatar_path_value)) if str(avatar_path_value or "").strip() else None, duelist_avatar_size)
        img.alpha_composite(avatar.convert("RGBA"), (avatar_x, avatar_y))

    avatar_pair_center_x = w // 2
    left_avatar_center_x = avatar_pair_center_x - (duelist_avatar_gap // 2) - duelist_avatar_size[0] // 2
    right_avatar_center_x = avatar_pair_center_x + (duelist_avatar_gap // 2) + duelist_avatar_size[0] // 2
    draw_duelist_header(left_center_x, player_name, player_avatar_path, player_header_note, left_avatar_center_x)
    draw_duelist_header(right_center_x, enemy_name, enemy_avatar_path, enemy_header_note, right_avatar_center_x)

    def draw_heart_icon(center_x: int, center_y: int, size: int, filled: bool = True):
        heart_fill = (255, 102, 152) if filled else (82, 92, 118)
        heart_shade = (236, 64, 118) if filled else (62, 72, 94)
        heart_outline = (255, 226, 236) if filled else (144, 154, 178)
        shadow_fill = (60, 12, 28) if filled else (28, 32, 44)
        lobe_radius = max(5, int(size * 0.31))
        lobe_offset_x = int(size * 0.23)
        heart_height = int(size * 0.92)

        shadow_center_y = center_y + max(1, int(size * 0.08))
        shadow_size = max(8, int(size * 0.92))
        shadow_lobe_radius = max(4, int(shadow_size * 0.29))
        shadow_lobe_offset_x = int(shadow_size * 0.21)
        shadow_lobe_offset_y = int(shadow_size * 0.18)
        shadow_bottom_y = shadow_center_y + int(shadow_size * 0.37)
        shadow = [
            (center_x - shadow_lobe_offset_x - shadow_lobe_radius, shadow_center_y - shadow_lobe_offset_y),
            (center_x + shadow_lobe_offset_x + shadow_lobe_radius, shadow_center_y - shadow_lobe_offset_y),
            (center_x, shadow_bottom_y),
        ]
        draw.polygon(shadow, fill=shadow_fill)

        heart_layer = Image.new("RGBA", (size + 10, size + 12), (0, 0, 0, 0))
        hd = ImageDraw.Draw(heart_layer)
        local_cx = heart_layer.width // 2
        local_top = 2
        local_bottom = local_top + heart_height
        left_lobe = (
            local_cx - lobe_offset_x - lobe_radius,
            local_top,
            local_cx - lobe_offset_x + lobe_radius,
            local_top + lobe_radius * 2,
        )
        right_lobe = (
            local_cx + lobe_offset_x - lobe_radius,
            local_top,
            local_cx + lobe_offset_x + lobe_radius,
            local_top + lobe_radius * 2,
        )
        body = [
            (local_cx - int(size * 0.47), local_top + int(size * 0.33)),
            (local_cx - int(size * 0.36), local_bottom - int(size * 0.16)),
            (local_cx, local_bottom),
            (local_cx + int(size * 0.36), local_bottom - int(size * 0.16)),
            (local_cx + int(size * 0.47), local_top + int(size * 0.33)),
        ]

        hd.ellipse(left_lobe, fill=heart_fill, outline=heart_outline, width=2)
        hd.ellipse(right_lobe, fill=heart_fill, outline=heart_outline, width=2)
        hd.polygon(body, fill=heart_fill, outline=heart_outline)
        if filled:
            squish = [
                (local_cx - int(size * 0.19), local_top + int(size * 0.48)),
                (local_cx - int(size * 0.05), local_top + int(size * 0.76)),
                (local_cx, local_bottom - int(size * 0.10)),
                (local_cx + int(size * 0.05), local_top + int(size * 0.76)),
                (local_cx + int(size * 0.19), local_top + int(size * 0.48)),
                (local_cx, local_top + int(size * 0.58)),
            ]
            hd.polygon(squish, fill=heart_shade)
            hd.ellipse(
                (
                    local_cx - int(size * 0.28),
                    local_top + int(size * 0.10),
                    local_cx - int(size * 0.02),
                    local_top + int(size * 0.38),
                ),
                fill=(255, 242, 247, 225),
            )
            hd.ellipse(
                (
                    local_cx + int(size * 0.07),
                    local_top + int(size * 0.13),
                    local_cx + int(size * 0.19),
                    local_top + int(size * 0.24),
                ),
                fill=(255, 248, 252, 180),
            )
        else:
            hd.ellipse(
                (
                    local_cx - int(size * 0.22),
                    local_top + int(size * 0.12),
                    local_cx - int(size * 0.06),
                    local_top + int(size * 0.28),
                ),
                fill=(170, 178, 198, 96),
            )

        heart_layer = heart_layer.rotate(-8 if filled else -5, resample=Image.Resampling.BICUBIC, expand=True)
        paste_x = int(center_x - heart_layer.width / 2)
        paste_y = int(center_y - heart_layer.height / 2)
        img.paste(heart_layer, (paste_x, paste_y), heart_layer)

    def draw_hearts(center_x: int, top_y: int, hearts_remaining: int, total_hearts: int):
        heart_size = 20
        heart_gap = 8
        total_width = total_hearts * heart_size + max(0, total_hearts - 1) * heart_gap
        start_x = int(center_x - total_width / 2 + heart_size / 2)
        for idx in range(total_hearts):
            heart_center_x = start_x + idx * (heart_size + heart_gap)
            draw_heart_icon(heart_center_x, top_y + heart_size // 2, heart_size, filled=idx < max(0, hearts_remaining))

    heart_top_y = 103
    draw_hearts(left_center_x, heart_top_y, player_hearts, max_hearts)
    if enemy_hearts is not None:
        draw_hearts(right_center_x, heart_top_y, enemy_hearts, max_hearts)

    support_highlight = (255, 230, 110)

    def effective_support_cards_for_team(team: Sequence[Fighter]) -> List[SupportCardDef]:
        # Use the battle-runtime support cards that were assigned per fighter.
        # This matters for cards like Necrovalley, which can seal the opposing
        # team's support cards even though that team's raw runtime still has
        # drawn active cards.
        cards: List[SupportCardDef] = []
        seen: set[str] = set()
        for fighter in team:
            for card in ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(fighter), []):
                if card.support_card_id in seen:
                    continue
                seen.add(card.support_card_id)
                cards.append(card)
        return cards

    def draw_support_strip(center_x: int, runtime: Optional[SupportDeckRuntime], title_fill: Tuple[int, int, int], team: Sequence[Fighter], active_cards: Optional[Sequence[SupportCardDef]] = None):
        cards = list(active_cards) if active_cards is not None else (list(runtime.active_cards) if runtime else [])
        summons = [fighter for fighter in team if fighter.alive and runtime_flag(fighter, "summon_data", None)]
        card_gap = 10
        summon_gap = 10
        section_gap = 14
        summon_size = support_thumb_size
        content_top = support_row_y

        card_strip_width = len(cards) * support_thumb_size[0] + max(0, len(cards) - 1) * card_gap
        summon_strip_width = len(summons) * summon_size[0] + max(0, len(summons) - 1) * summon_gap
        total_strip_width = card_strip_width
        if cards and summons:
            total_strip_width += section_gap + summon_strip_width
        elif summons:
            total_strip_width = summon_strip_width

        if cards or summons:
            draw.text((center_x, support_row_y - 28), "Active Support", font=FONT_XS_BOLD, fill=title_fill, anchor="ma")
            current_x = int(center_x - (total_strip_width / 2))
            for support_card in cards:
                thumb_x = current_x
                thumb_box = (thumb_x - 2, content_top - 2, thumb_x + support_thumb_size[0] + 2, content_top + support_thumb_size[1] + 2)
                outline = element_color(support_card.attribute)
                draw.rounded_rectangle(thumb_box, 10, fill=palette["card_fill"], outline=outline, width=1)
                thumb = open_card_image_contain(str(SUPPORT_CARD_IMAGE_PATH), support_thumb_size, radius=10)
                img.paste(thumb, (thumb_x, content_top), thumb)
                draw.rounded_rectangle((thumb_x, content_top, thumb_x + support_thumb_size[0], content_top + support_thumb_size[1]), 10, outline=outline, width=1)
                label_y = content_top + support_thumb_size[1] - 20
                draw.rounded_rectangle((thumb_x + 3, label_y, thumb_x + support_thumb_size[0] - 3, content_top + support_thumb_size[1] - 3), 7, fill=(18, 18, 22), outline=outline, width=1)
                draw.text((thumb_x + support_thumb_size[0] / 2, label_y + 8), support_card.stat_type, font=FONT_XS_BOLD, fill=title_fill, anchor="mm")
                current_x += support_thumb_size[0] + card_gap
            if cards:
                current_x -= card_gap
            if cards and summons:
                current_x += section_gap
            for fighter in summons:
                sx = current_x
                outline = element_color(fighter.attribute)
                summon_frame = open_support_card_template(summon_size, radius=10)
                img.paste(summon_frame, (sx, content_top), summon_frame)
                art_margin_x = 4
                art_top = content_top + 4
                art_bottom = content_top + summon_size[1] - 22
                art_h = max(12, art_bottom - art_top)
                summon_thumb = open_card_image_contain(fighter.image_path, (summon_size[0] - art_margin_x * 2, art_h), radius=8, grayscale=not fighter.alive)
                art_x = sx + int((summon_size[0] - summon_thumb.width) / 2)
                art_y = art_top + int((art_h - summon_thumb.height) / 2)
                img.paste(summon_thumb, (art_x, art_y), summon_thumb)
                draw.rounded_rectangle((sx, content_top, sx + summon_size[0], content_top + summon_size[1]), 10, outline=outline, width=1)
                label_y = content_top + summon_size[1] - 20
                draw.rounded_rectangle((sx + 3, label_y, sx + summon_size[0] - 3, content_top + summon_size[1] - 3), 7, fill=(18, 18, 22), outline=outline, width=1)
                draw.text((sx + summon_size[0] / 2, label_y + 8), "TOKEN", font=FONT_XS_BOLD, fill=support_highlight, anchor="mm")
                current_x += summon_size[0] + summon_gap
        else:
            draw.text((center_x, support_row_y - 2), "No active support", font=FONT_XS, fill=title_fill, anchor="ma")


    def badge_colors(label: str) -> Tuple[Tuple[int, int, int], Tuple[int, int, int], Tuple[int, int, int]]:
        upper = str(label or "").upper()
        if any(term in upper for term in ("IMMUNITY", "RECOVERY", "PROTECTION", "SHIELD", "TOKEN", "PRECOG", "LIFESTEAL", "REFLECT", "COUNTER")):
            return (44, 86, 58), (105, 214, 147), (230, 255, 240)
        if any(term in upper for term in ("BURN", "POISON", "STUN", "FREEZE", "PETRIFY", "TAUNT", "FEAR", "SLEEP", "CHARM", "SILENCE", "BOMB", "HEAL BLOCK", "MARK")):
            return (88, 44, 44), (232, 120, 120), (255, 236, 236)
        return tuple(palette["card_fill"]), tuple(palette["panel_outline"]), tuple(palette["title"])

    def is_positive_badge(label: str) -> bool:
        upper = str(label or "").upper()
        return any(term in upper for term in (
            "IMMUNITY", "RECOVERY", "PROTECTION", "SHIELD", "TOKEN", "PRECOG", "LIFESTEAL", "REFLECT", "COUNTER", "OK"
        ))

    def is_negative_badge(label: str) -> bool:
        upper = str(label or "").upper()
        return any(term in upper for term in (
            "BURN", "POISON", "STUN", "FREEZE", "PETRIFY", "TAUNT", "FEAR", "SLEEP", "CHARM", "SILENCE", "BOMB", "HEAL BLOCK", "MARK", "KO"
        ))

    def split_status_badges(labels: Sequence[str]) -> Tuple[List[str], List[str], List[str]]:
        positives: List[str] = []
        negatives: List[str] = []
        neutral: List[str] = []
        for label in labels:
            cleaned = " ".join(str(label or "").split()).strip()
            if not cleaned:
                continue
            if is_positive_badge(cleaned):
                positives.append(cleaned)
            elif is_negative_badge(cleaned):
                negatives.append(cleaned)
            else:
                neutral.append(cleaned)
        return positives, negatives, neutral

    def draw_status_side_panel(panel_box: Tuple[int, int, int, int], ally_team: Sequence[Fighter], enemy_team: Sequence[Fighter]):
        left, top, right, bottom = panel_box
        draw.rounded_rectangle(panel_box, 22, fill=palette["card_fill"], outline=palette["panel_outline"], width=3)
        draw.text(((left + right) / 2, top + 20), "Monster Status", font=FONT_M_BOLD, fill=palette["title"], anchor="ma")

        mid_x = (left + right) // 2
        divider_top = top + 52
        divider_bottom = bottom - 18
        draw.line((mid_x, divider_top, mid_x, divider_bottom), fill=palette["panel_outline"], width=2)

        column_padding = 18
        column_gap = 14
        section_top = top + 62

        def draw_badge_stack(badges: Sequence[str], box: Tuple[int, int, int, int], alignment: str):
            if not badges:
                return
            left_x, top_y, right_x, bottom_y = box
            stack_width = max(24, right_x - left_x)
            stack_height = max(24, bottom_y - top_y)
            badge_count = len(badges)
            vertical_gap = max(2, min(6, stack_height // max(6, badge_count * 3)))
            badge_height = max(12, int((stack_height - vertical_gap * max(0, badge_count - 1)) / max(1, badge_count)))
            badge_height = min(18, badge_height)
            radius = max(4, min(10, badge_height // 2))
            inner_pad = max(6, min(10, badge_height // 2))
            font_size = max(9, min(14, badge_height - 4))
            font_candidates = [get_bold_font(font_size), get_bold_font(max(9, font_size - 1)), get_bold_font(9)]
            total_height = badge_count * badge_height + max(0, badge_count - 1) * vertical_gap
            start_y = top_y + max(0, int((stack_height - total_height) / 2))
            for idx, badge_label in enumerate(badges):
                badge_top = start_y + idx * (badge_height + vertical_gap)
                badge_bottom = badge_top + badge_height
                fitted_badge, badge_font = fit_text_to_width(badge_label, stack_width - inner_pad * 2, font_candidates)
                text_width = draw.textlength(fitted_badge, font=badge_font)
                badge_width = min(stack_width, max(24, int(text_width + inner_pad * 2)))
                if alignment == "left":
                    badge_x1 = left_x
                elif alignment == "right":
                    badge_x1 = right_x - badge_width
                else:
                    badge_x1 = int((left_x + right_x - badge_width) / 2)
                badge_x2 = badge_x1 + badge_width
                fill_color, outline_color, text_color = badge_colors(badge_label)
                draw.rounded_rectangle((badge_x1, badge_top, badge_x2, badge_bottom), radius, fill=fill_color, outline=outline_color, width=2)
                draw.text(((badge_x1 + badge_x2) / 2, (badge_top + badge_bottom) / 2), fitted_badge, font=badge_font, fill=text_color, anchor="mm")

        def draw_team_column(title: str, fighters: Sequence[Fighter], col_left: int, col_right: int):
            draw.text(((col_left + col_right) / 2, section_top), title, font=FONT_S_BOLD, fill=palette["subtitle"], anchor="ma")
            available_width = col_right - col_left
            row_gap = 14
            fighter_count = max(1, len(fighters))
            content_top = section_top + 18
            content_bottom = bottom - 16
            row_height = max(96, int((content_bottom - content_top - row_gap * (fighter_count - 1)) / fighter_count))
            name_fonts = [FONT_S_BOLD, FONT_XS_BOLD, FONT_XS]
            neutral_fonts = [FONT_XS_BOLD, get_bold_font(12), get_bold_font(10)]
            for idx, fighter in enumerate(fighters):
                row_top = content_top + idx * (row_height + row_gap)
                row_bottom = min(content_bottom, row_top + row_height)
                row_box = (col_left, row_top, col_right, row_bottom)
                outline = element_color(fighter.attribute) if fighter.alive else palette["panel_outline"]
                fill = tuple(max(0, int(channel * (0.82 if fighter.alive else 0.62))) for channel in palette["panel_fill"])
                draw.rounded_rectangle(row_box, 16, fill=fill, outline=outline, width=2)

                name_max_width = available_width - 18
                name_text, name_font = fit_text_to_width(fighter.name, name_max_width, name_fonts)
                draw.text(((col_left + col_right) / 2, row_top + 14), name_text, font=name_font, fill=palette["title"], anchor="ma")

                badges = battle_status_badge_labels(fighter)
                if not badges:
                    badges = ["OK" if fighter.alive else "KO"]
                positive_badges, negative_badges, neutral_badges = split_status_badges(badges)

                status_area_top = row_top + 30
                status_area_bottom = row_bottom - 8
                inner_left = col_left + 8
                inner_right = col_right - 8
                inner_mid = (inner_left + inner_right) // 2
                lane_gap = 8

                if positive_badges and negative_badges:
                    positive_box = (inner_left, status_area_top, inner_mid - lane_gap // 2, status_area_bottom)
                    negative_box = (inner_mid + lane_gap // 2, status_area_top, inner_right, status_area_bottom)
                    draw_badge_stack(positive_badges, positive_box, "left")
                    draw_badge_stack(negative_badges, negative_box, "right")
                elif positive_badges:
                    draw_badge_stack(positive_badges, (inner_left, status_area_top, inner_right, status_area_bottom), "center")
                elif negative_badges:
                    draw_badge_stack(negative_badges, (inner_left, status_area_top, inner_right, status_area_bottom), "center")

                if neutral_badges:
                    neutral_text = " / ".join(neutral_badges)
                    neutral_text, neutral_font = fit_text_to_width(neutral_text, available_width - 16, neutral_fonts)
                    draw.text(((col_left + col_right) / 2, row_bottom - 7), neutral_text, font=neutral_font, fill=palette["info_text"], anchor="ms")

        left_col_left = left + column_padding
        left_col_right = mid_x - column_gap // 2
        right_col_left = mid_x + column_gap // 2
        right_col_right = right - column_padding
        draw_team_column("Player", ally_team, left_col_left, left_col_right)
        draw_team_column("Enemy", enemy_team, right_col_left, right_col_right)

    def draw_fighter_card(fighter: Fighter, x: int, active_cards: Sequence[SupportCardDef]):
        card_center_x = x + (card_size[0] // 2)
        portrait_top = y
        # Keep the full header region available so equip spell badges always have
        # room to render, then clamp the stacked content slightly lower so names
        # do not collide with the panel edge.
        header_top = bubble_top - 2
        header_bottom = portrait_top - 4
        header_vertical_padding = 4
        badge_side_padding = 6
        badge_height = 18
        min_badge_height = 14
        name_to_badge_gap = 4
        name_font = FONT_XS_BOLD
        name_max_width = card_size[0] + 12
        name_text = ellipsize_text(draw, fighter.name, name_font, name_max_width)
        probe_name_bbox = draw.textbbox((card_center_x, 0), name_text, font=name_font, anchor="ma")
        name_height = (probe_name_bbox[3] - probe_name_bbox[1]) if probe_name_bbox else 14
        equip_name = active_equip_spell_name(fighter)

        header_inner_top = header_top + header_vertical_padding
        header_inner_bottom = header_bottom - header_vertical_padding
        header_inner_height = max(0, header_inner_bottom - header_inner_top)

        actual_badge_height = 0
        content_height = name_height
        if equip_name:
            max_badge_height = max(0, header_inner_height - name_height - name_to_badge_gap)
            if max_badge_height >= min_badge_height:
                actual_badge_height = min(badge_height, max_badge_height)
                content_height += name_to_badge_gap + actual_badge_height

        if header_inner_height > 0:
            content_top = header_inner_top + max(0, int((header_inner_height - content_height) / 2))
            min_content_top = header_inner_top + 4
            max_content_top = max(header_inner_top, header_inner_bottom - content_height)
            content_top = max(min_content_top, min(content_top, max_content_top))
        else:
            content_top = header_top

        fighter_name_center_y = int(content_top + (name_height / 2))
        draw.text((card_center_x, fighter_name_center_y), name_text, font=name_font, fill=palette["title"], anchor="mm")

        if equip_name and actual_badge_height >= min_badge_height:
            equip_top = int(content_top + name_height + name_to_badge_gap)
            equip_bottom = equip_top + actual_badge_height
            equip_box = (x + badge_side_padding, equip_top, x + card_size[0] - badge_side_padding, equip_bottom)
            equip_font_candidates = [FONT_XS_BOLD, FONT_XS, get_font(12), get_font(11), get_font(10)]
            equip_text, equip_font = fit_text_to_width(equip_name, (equip_box[2] - equip_box[0]) - 12, equip_font_candidates)
            draw.rounded_rectangle(equip_box, 9, fill=palette["card_fill"], outline=(110, 230, 140), width=2)
            draw.text(((equip_box[0] + equip_box[2]) / 2, (equip_box[1] + equip_box[3]) / 2), equip_text, font=equip_font, fill=(110, 230, 140), anchor="mm")

        card = open_card_image(fighter.image_path, card_size, radius=18, grayscale=not fighter.alive)
        img.paste(card, (x, y), card)
        is_active = active_fighter is fighter
        is_targeted = target_fighter is fighter
        outline_color = element_color(fighter.attribute)
        outline_width = 3
        if is_targeted:
            outline_color = (188, 188, 194)
            outline_width = 5
        elif is_active:
            outline_color = (255, 236, 145)
            outline_width = 4
        draw.rounded_rectangle((x, y, x + 135, y + 135), 14, outline=outline_color, width=outline_width)
        draw_hp_bar(draw, (x, y + 180, x + 135, y + 202), fighter.hp, fighter.max_hp)
        draw.text((x + 67, y + 215), hp_value_text(fighter.hp, fighter.max_hp), font=FONT_XS, fill=palette["card_text"], anchor="ma")
        draw.text((x + 67, y + 236), format_monster_types(fighter.monster_types), font=FONT_XS, fill=palette["info_text"], anchor="ma")
        spd_bonus = support_card_stat_bonus(fighter, active_cards, "SPD")
        spd_text = f"SPD {fighter.spd}"
        draw.text((x + 67, y + 252), spd_text, font=FONT_XS, fill=palette["subtitle"], anchor="ma")
        if spd_bonus:
            spd_bonus_x = x + 67 + (draw.textlength(spd_text, font=FONT_XS) / 2)
            draw.text((spd_bonus_x, y + 252), f"+{spd_bonus}", font=FONT_XS, fill=support_highlight)
        atk_bonus = support_card_stat_bonus(fighter, active_cards, "ATK")
        def_bonus = support_card_stat_bonus(fighter, active_cards, "DEF")
        bonus_color = support_highlight
        base_stat_color = palette["subtitle"]

        def draw_stat_with_bonus(left_x: float, baseline_y: float, label: str, base_value: int, bonus_value: int):
            base_text = f"{label} {base_value}"
            draw.text((left_x, baseline_y), base_text, font=FONT_XS, fill=base_stat_color)
            if bonus_value:
                bonus_x = left_x + draw.textlength(base_text, font=FONT_XS)
                draw.text((bonus_x, baseline_y), f"+{bonus_value}", font=FONT_XS, fill=bonus_color)
            total_width = draw.textlength(base_text, font=FONT_XS)
            if bonus_value:
                total_width += draw.textlength(f"+{bonus_value}", font=FONT_XS)
            return total_width

        left_x = x + 10
        stat_y = y + 268
        atk_width = draw_stat_with_bonus(left_x, stat_y, "ATK", fighter.atk, atk_bonus)
        draw_stat_with_bonus(left_x + atk_width + draw.textlength("  ", font=FONT_XS), stat_y, "DEF", fighter.defense, def_bonus)
        if is_active and fighter.alive:
            turn_box = (x + 18, y + 8, x + 117, y + 36)
            draw.rounded_rectangle(turn_box, 8, fill=palette["card_fill"], outline=palette["accent_binder"], width=2)
            draw.text(((turn_box[0] + turn_box[2]) / 2, (turn_box[1] + turn_box[3]) / 2 + 1), "TURN", font=FONT_XS_BOLD, fill=palette["title"], anchor="mm")
        if is_targeted and fighter.alive:
            draw.rounded_rectangle((x + 26, y + 142, x + 109, y + 166), 8, fill=(50, 20, 20), outline=(255, 135, 135), width=2)
            draw.text((x + 67, y + 154), "TARGET", font=FONT_XS_BOLD, fill=(255, 210, 210), anchor="mm")
        # Status badges now render in the side panel instead of over the monster art.
        if not fighter.alive:
            draw.rounded_rectangle((x + 20, y + 52, x + 115, y + 88), 10, fill=(0, 0, 0, 200), outline=(186, 186, 192), width=2)
            draw.text((x + 67, y + 70), "KO", font=FONT_M_BOLD, fill=(188, 188, 194), anchor="mm")

    ally_effective_support_cards = effective_support_cards_for_team(allies)
    enemy_effective_support_cards = effective_support_cards_for_team(enemies)

    for idx, fighter in enumerate(allies):
        draw_fighter_card(fighter, ally_x[idx], ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(fighter), []))

    for idx, fighter in enumerate(enemies):
        draw_fighter_card(fighter, enemy_x[idx], ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(fighter), []))

    draw_support_strip(left_center_x, player_support_runtime, support_highlight, allies, ally_effective_support_cards)
    draw_support_strip(right_center_x, enemy_support_runtime, support_highlight, enemies, enemy_effective_support_cards)

    draw_status_side_panel((center_panel_left, bubble_top, center_panel_right, bubble_bottom), allies, enemies)

    log_top = bubble_bottom + 25
    log_bottom = h - 30
    draw.rounded_rectangle((25, log_top, w - 25, log_bottom), 24, outline=palette["panel_outline"], width=3, fill=log_fill)
    draw.text((50, log_top + 14), "Battle Log", font=FONT_L, fill=palette["title"])
    status_bits = []
    if tick_info:
        status_bits.append(tick_info)
    if round_info:
        status_bits.append(round_info)
    log_left = 50
    log_right = 1148
    if status_bits:
        header_status_text = "  •  ".join(status_bits)
        draw.text((w - 50, log_top + 19), header_status_text, font=FONT_S, fill=palette["info_text"], anchor="ra")
    log_line_start_y = log_top + 55
    line_height = 20
    max_log_lines = max(1, int((log_bottom - log_line_start_y - 10) // line_height))
    rendered_log_lines: List[Tuple[str, str]] = []
    bullet_indent = int(draw.textlength("• ", font=FONT_S))
    available_width = log_right - log_left
    continuation_width = max(20, available_width - bullet_indent)
    for raw_line in log_lines[::-1]:
        wrapped = wrap_rich_text_lines(draw, raw_line, FONT_S, FONT_S_BOLD, available_width)
        entry_lines = [(wrapped[0], "• ")]
        entry_lines.extend((segment, " " * 2) for segment in wrapped[1:])
        if len(rendered_log_lines) + len(entry_lines) > max_log_lines:
            remaining = max_log_lines - len(rendered_log_lines)
            if remaining > 0:
                rendered_log_lines.extend(entry_lines[-remaining:])
            break
        rendered_log_lines.extend(reversed(entry_lines))
    rendered_log_lines = list(reversed(rendered_log_lines))[-max_log_lines:]
    for i, (line, prefix) in enumerate(rendered_log_lines):
        prefix_text = prefix if prefix == "• " else " " * 2
        max_width = available_width if prefix_text == "• " else continuation_width
        display_line = ellipsize_text(draw, line, FONT_S, max_width)
        draw_rich_text(draw, (log_left, log_line_start_y + i * line_height), display_line, FONT_S, FONT_S_BOLD, palette["card_text"], bullet_prefix=prefix_text, support_fill=support_highlight, positive_fill=(145, 220, 255), negative_fill=(255, 110, 110), equip_fill=(110, 230, 140))

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer
