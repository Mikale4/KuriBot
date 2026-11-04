"""UI colour themes and element colours."""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

from kuribot.database import db


def element_color(attribute: str) -> Tuple[int, int, int]:
    return {
        "FIRE": (255, 80, 80),
        "WATER": (80, 150, 255),
        "EARTH": (155, 125, 90),
        "WIND": (40, 225, 140),
        "LIGHT": (255, 235, 120),
        "DARK": (180, 120, 255),
        "DIVINE": (255, 210, 110),
    }.get(attribute.upper(), (180, 180, 180))


UI_THEME_PAGE_SIZE = 24


def _clamp_rgb(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _mix_rgb(a: Tuple[int, int, int], b: Tuple[int, int, int], amount: float) -> Tuple[int, int, int]:
    weight = max(0.0, min(1.0, float(amount)))
    return tuple(_clamp_rgb((x * (1.0 - weight)) + (y * weight)) for x, y in zip(a, b))


def _lighten_rgb(color: Tuple[int, int, int], amount: float) -> Tuple[int, int, int]:
    return _mix_rgb(color, (255, 255, 255), amount)


def build_ui_theme_palette(
    *,
    bg: Tuple[int, int, int],
    panel: Tuple[int, int, int],
    primary: Tuple[int, int, int],
    secondary: Tuple[int, int, int],
    tertiary: Tuple[int, int, int],
    text: Tuple[int, int, int] = (246, 246, 250),
) -> Dict[str, Tuple[int, int, int]]:
    title = _lighten_rgb(text, 0.04)
    subtitle = _mix_rgb(text, primary, 0.42)
    support_accent = _mix_rgb(secondary, (255, 160, 255), 0.32)
    team_accent = _mix_rgb(tertiary, (90, 240, 150), 0.26)
    dust_accent = _mix_rgb(primary, secondary, 0.45)
    return {
        'title': title,
        'subtitle': subtitle,
        'outer_outline': _lighten_rgb(primary, 0.12),
        'panel_fill': panel,
        'panel_outline': _lighten_rgb(primary, 0.06),
        'avatar_outline': _lighten_rgb(primary, 0.45),
        'card_fill': _lighten_rgb(bg, 0.05),
        'card_text': _lighten_rgb(text, 0.02),
        'signature_outline': _mix_rgb(primary, secondary, 0.30),
        'signature_hint': _mix_rgb(text, secondary, 0.34),
        'signature_text': _lighten_rgb(text, 0.01),
        'empty_text': _mix_rgb(text, bg, 0.46),
        'info_outline': _mix_rgb(primary, tertiary, 0.28),
        'info_text': _mix_rgb(text, primary, 0.18),
        'accent_binder': _lighten_rgb(primary, 0.02),
        'accent_support': _lighten_rgb(support_accent, 0.04),
        'accent_cards': _lighten_rgb(support_accent, 0.04),
        'accent_team': _lighten_rgb(team_accent, 0.04),
        'accent_dust': _lighten_rgb(dust_accent, 0.10),
    }


UI_THEME_SPECS: List[Tuple[str, str, Dict[str, Tuple[int, int, int]]]] = [
    ('gray', 'Gray', build_ui_theme_palette(bg=(22, 22, 26), panel=(30, 30, 34), primary=(146, 146, 154), secondary=(170, 125, 225), tertiary=(108, 190, 132), text=(244, 244, 246))),
    ('blue', 'Blue', build_ui_theme_palette(bg=(12, 20, 40), panel=(18, 26, 48), primary=(120, 170, 255), secondary=(188, 145, 255), tertiary=(108, 196, 140), text=(244, 247, 255))),
    ('emerald', 'Emerald', build_ui_theme_palette(bg=(16, 34, 28), panel=(22, 40, 34), primary=(98, 178, 142), secondary=(166, 136, 224), tertiary=(96, 196, 132), text=(242, 250, 246))),
    ('crimson', 'Crimson', build_ui_theme_palette(bg=(36, 18, 24), panel=(44, 24, 30), primary=(204, 106, 124), secondary=(202, 136, 236), tertiary=(106, 188, 126), text=(255, 245, 247))),
    ('violet', 'Violet', build_ui_theme_palette(bg=(30, 24, 42), panel=(36, 28, 48), primary=(156, 128, 220), secondary=(206, 142, 255), tertiary=(116, 202, 146), text=(248, 244, 255))),
    ('amber', 'Amber', build_ui_theme_palette(bg=(38, 28, 16), panel=(46, 36, 22), primary=(206, 166, 100), secondary=(194, 132, 226), tertiary=(116, 188, 132), text=(255, 248, 236))),
    ('rainbow_burst', 'Rainbow Burst', build_ui_theme_palette(bg=(24, 10, 36), panel=(34, 16, 48), primary=(120, 190, 255), secondary=(255, 88, 196), tertiary=(122, 255, 138), text=(252, 246, 255))),
    ('purple_magenta', 'Purple Magenta', build_ui_theme_palette(bg=(26, 10, 36), panel=(36, 16, 46), primary=(176, 92, 255), secondary=(255, 76, 196), tertiary=(126, 216, 170), text=(250, 240, 255))),
    ('neon_nights', 'Neon Nights', build_ui_theme_palette(bg=(6, 12, 18), panel=(12, 20, 28), primary=(0, 255, 214), secondary=(255, 65, 150), tertiary=(142, 255, 96), text=(236, 255, 252))),
    ('cotton_candy', 'Cotton Candy', build_ui_theme_palette(bg=(34, 20, 34), panel=(46, 28, 44), primary=(255, 146, 214), secondary=(174, 140, 255), tertiary=(118, 236, 212), text=(255, 242, 250))),
    ('sunset_pop', 'Sunset Pop', build_ui_theme_palette(bg=(40, 18, 20), panel=(52, 24, 26), primary=(255, 122, 88), secondary=(255, 72, 164), tertiary=(255, 208, 92), text=(255, 242, 236))),
    ('aurora_wave', 'Aurora Wave', build_ui_theme_palette(bg=(12, 26, 30), panel=(18, 34, 38), primary=(88, 255, 198), secondary=(126, 168, 255), tertiary=(186, 122, 255), text=(238, 255, 250))),
    ('lava_luxe', 'Lava Luxe', build_ui_theme_palette(bg=(30, 12, 10), panel=(40, 16, 14), primary=(255, 98, 58), secondary=(255, 170, 66), tertiary=(255, 64, 114), text=(255, 242, 236))),
    ('cyber_lime', 'Cyber Lime', build_ui_theme_palette(bg=(12, 20, 10), panel=(18, 28, 14), primary=(182, 255, 52), secondary=(70, 255, 180), tertiary=(255, 228, 76), text=(248, 255, 236))),
    ('electric_orchid', 'Electric Orchid', build_ui_theme_palette(bg=(22, 12, 32), panel=(30, 16, 42), primary=(222, 100, 255), secondary=(118, 204, 255), tertiary=(255, 118, 186), text=(248, 240, 255))),
    ('ocean_laser', 'Ocean Laser', build_ui_theme_palette(bg=(8, 24, 34), panel=(12, 32, 42), primary=(76, 228, 255), secondary=(120, 116, 255), tertiary=(90, 255, 184), text=(236, 250, 255))),
    ('toxic_splash', 'Toxic Splash', build_ui_theme_palette(bg=(18, 22, 8), panel=(24, 28, 12), primary=(222, 255, 64), secondary=(116, 255, 74), tertiary=(255, 122, 70), text=(248, 255, 232))),
    ('starlight_pink', 'Starlight Pink', build_ui_theme_palette(bg=(28, 14, 28), panel=(36, 20, 36), primary=(255, 120, 188), secondary=(255, 214, 122), tertiary=(170, 152, 255), text=(255, 242, 248))),
    ('plasma_gold', 'Plasma Gold', build_ui_theme_palette(bg=(24, 18, 8), panel=(32, 24, 12), primary=(255, 214, 76), secondary=(255, 132, 38), tertiary=(255, 92, 174), text=(255, 248, 232))),
    ('glacier_candy', 'Glacier Candy', build_ui_theme_palette(bg=(14, 22, 34), panel=(20, 30, 44), primary=(142, 220, 255), secondary=(255, 126, 212), tertiary=(186, 255, 210), text=(240, 248, 255))),
    ('dragonfruit', 'Dragonfruit', build_ui_theme_palette(bg=(30, 10, 22), panel=(38, 14, 28), primary=(255, 78, 150), secondary=(174, 88, 255), tertiary=(108, 255, 190), text=(255, 240, 248))),
    ('hyper_mint', 'Hyper Mint', build_ui_theme_palette(bg=(8, 26, 20), panel=(12, 34, 26), primary=(84, 255, 204), secondary=(130, 255, 122), tertiary=(255, 208, 112), text=(238, 255, 248))),
    ('deep_space', 'Deep Space', build_ui_theme_palette(bg=(8, 8, 20), panel=(12, 14, 28), primary=(108, 136, 255), secondary=(212, 96, 255), tertiary=(106, 255, 212), text=(238, 240, 255))),
    ('phoenix_flare', 'Phoenix Flare', build_ui_theme_palette(bg=(34, 12, 6), panel=(42, 18, 10), primary=(255, 102, 54), secondary=(255, 198, 72), tertiary=(255, 70, 136), text=(255, 244, 232))),
    ('galaxy_gum', 'Galaxy Gum', build_ui_theme_palette(bg=(18, 10, 30), panel=(24, 14, 40), primary=(144, 118, 255), secondary=(255, 108, 210), tertiary=(118, 232, 255), text=(246, 240, 255))),
    ('citrus_pop', 'Citrus Pop', build_ui_theme_palette(bg=(22, 18, 8), panel=(30, 24, 12), primary=(255, 228, 68), secondary=(130, 255, 80), tertiary=(255, 142, 62), text=(255, 252, 232))),
    ('midnight_rave', 'Midnight Rave', build_ui_theme_palette(bg=(10, 10, 18), panel=(16, 16, 26), primary=(255, 82, 182), secondary=(112, 140, 255), tertiary=(96, 255, 168), text=(246, 242, 255))),
    ('arctic_pulse', 'Arctic Pulse', build_ui_theme_palette(bg=(12, 24, 28), panel=(18, 32, 36), primary=(132, 248, 255), secondary=(150, 178, 255), tertiary=(124, 255, 212), text=(238, 252, 255))),
    ('rose_storm', 'Rose Storm', build_ui_theme_palette(bg=(24, 12, 20), panel=(32, 16, 28), primary=(255, 110, 160), secondary=(255, 168, 90), tertiary=(188, 104, 255), text=(255, 242, 246))),
    ('mint_jelly', 'Mint Jelly', build_ui_theme_palette(bg=(16, 26, 22), panel=(20, 34, 28), primary=(116, 255, 198), secondary=(106, 222, 255), tertiary=(220, 255, 120), text=(240, 255, 248))),
    ('ultra_heat', 'Ultra Heat', build_ui_theme_palette(bg=(26, 8, 8), panel=(34, 12, 12), primary=(255, 82, 82), secondary=(255, 178, 56), tertiary=(255, 72, 168), text=(255, 240, 236))),
    ('prism_shock', 'Prism Shock', build_ui_theme_palette(bg=(14, 14, 28), panel=(20, 18, 36), primary=(116, 176, 255), secondary=(255, 88, 226), tertiary=(255, 224, 96), text=(244, 242, 255))),
    ('bubblegum_wave', 'Bubblegum Wave', build_ui_theme_palette(bg=(30, 18, 28), panel=(38, 24, 36), primary=(255, 146, 196), secondary=(142, 188, 255), tertiary=(120, 250, 212), text=(255, 244, 248))),
    ('solar_flare', 'Solar Flare', build_ui_theme_palette(bg=(28, 18, 6), panel=(36, 24, 10), primary=(255, 208, 58), secondary=(255, 112, 58), tertiary=(255, 80, 152), text=(255, 248, 234))),
    ('sky_pop', 'Sky Pop', build_ui_theme_palette(bg=(14, 22, 32), panel=(18, 30, 42), primary=(112, 198, 255), secondary=(255, 130, 182), tertiary=(148, 255, 164), text=(240, 248, 255))),
    ('acid_trip', 'Acid Trip', build_ui_theme_palette(bg=(18, 18, 8), panel=(24, 24, 12), primary=(214, 255, 68), secondary=(255, 76, 190), tertiary=(82, 255, 210), text=(248, 255, 232))),
    ('royal_jam', 'Royal Jam', build_ui_theme_palette(bg=(20, 10, 28), panel=(28, 14, 36), primary=(172, 100, 255), secondary=(255, 92, 180), tertiary=(255, 206, 88), text=(248, 242, 255))),
    ('flamingo_flash', 'Flamingo Flash', build_ui_theme_palette(bg=(32, 14, 18), panel=(40, 18, 24), primary=(255, 110, 146), secondary=(255, 188, 96), tertiary=(120, 240, 212), text=(255, 242, 242))),
    ('jade_arcade', 'Jade Arcade', build_ui_theme_palette(bg=(10, 22, 18), panel=(14, 30, 24), primary=(86, 226, 162), secondary=(116, 176, 255), tertiary=(224, 255, 92), text=(240, 255, 248))),
    ('nova_punch', 'Nova Punch', build_ui_theme_palette(bg=(18, 12, 26), panel=(24, 16, 34), primary=(255, 104, 120), secondary=(122, 166, 255), tertiary=(255, 220, 110), text=(250, 242, 248))),
    ('mermaid_glow', 'Mermaid Glow', build_ui_theme_palette(bg=(10, 24, 30), panel=(14, 32, 38), primary=(78, 232, 218), secondary=(142, 164, 255), tertiary=(255, 132, 208), text=(236, 252, 255))),
    ('retro_wave', 'Retro Wave', build_ui_theme_palette(bg=(18, 10, 24), panel=(24, 14, 32), primary=(255, 88, 170), secondary=(114, 196, 255), tertiary=(192, 118, 255), text=(248, 240, 255))),
]

UI_THEME_PALETTES: Dict[str, Dict[str, Tuple[int, int, int]]] = {key: palette for key, _label, palette in UI_THEME_SPECS}
UI_THEME_LABELS = {key: label for key, label, _palette in UI_THEME_SPECS}
UI_THEME_KEYS = [key for key, _label, _palette in UI_THEME_SPECS]


def ui_theme_page_count() -> int:
    return max(1, math.ceil(len(UI_THEME_KEYS) / UI_THEME_PAGE_SIZE))


def ui_theme_page_keys(page_index: int) -> List[str]:
    max_page = ui_theme_page_count() - 1
    safe_page = max(0, min(max_page, int(page_index)))
    start = safe_page * UI_THEME_PAGE_SIZE
    return UI_THEME_KEYS[start:start + UI_THEME_PAGE_SIZE]


def get_ui_theme_palette(theme_name: str) -> Dict[str, Tuple[int, int, int]]:
    return UI_THEME_PALETTES.get(str(theme_name or "gray").strip().lower(), UI_THEME_PALETTES["gray"])


def get_user_ui_theme_palette(user_id: int) -> Dict[str, Tuple[int, int, int]]:
    return get_ui_theme_palette(db.get_ui_theme(user_id))
