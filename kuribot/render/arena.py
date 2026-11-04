"""Rendering arena pages."""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path
from typing import Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from kuribot.config import TEAM_SIZE
from kuribot.models import Fighter
from kuribot.database import db
from kuribot.render.fonts import (
    FONT_L_BOLD, FONT_M_BOLD, FONT_S, FONT_S_BOLD, FONT_XL, FONT_XS, FONT_XS_BOLD,
)
from kuribot.render.themes import get_user_ui_theme_palette
from kuribot.render.images import (
    open_binder_canvas_background, open_card_image, paste_binder_panel, with_panel_user_context,
)
from kuribot.render.text import draw_centered_text_block, ellipsize_text, fit_wrapped_text
from kuribot.monsters import decode_monster_types, format_monster_types, resolve_row_combat_stats
from kuribot.battle.fighters import make_fighter_from_id
from kuribot.portraits import (
    get_duelist_portrait_path, load_player_avatar_image, paste_avatar_with_outline,
)
from kuribot.arena import (
    arena_entry_defense_team_text, arena_entry_team, ARENA_LEADERBOARD_PAGE_SIZE,
    ARENA_MATCH_COUNT, arena_rank_label, arena_rank_title, ARENA_STARTING_ELO,
    get_user_arena_rank_position,
)


def arena_entry_avatar_image(entry: dict, size: Tuple[int, int]) -> Image.Image:
    avatar_path: Optional[Path] = None
    if entry.get('kind') == 'user':
        stored = db.get_avatar_path(int(entry.get('id', 0)))
        if stored:
            avatar_path = Path(str(stored))
    else:
        portrait = get_duelist_portrait_path(str(entry.get('id', '')), str(entry.get('name', '')))
        if portrait is not None:
            avatar_path = portrait
    return load_player_avatar_image(avatar_path, size)


@with_panel_user_context(lambda user_name, owner_id, *args, **kwargs: owner_id)
def render_arena_hub_page(user_name: str, owner_id: int) -> io.BytesIO:
    width, height = 960, 720
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_user_ui_theme_palette(owner_id)
    owner_rank_position = get_user_arena_rank_position(owner_id)
    owner_rank_title = arena_rank_title(owner_rank_position)
    owner_rank_label = arena_rank_label(owner_rank_position)

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

    draw.text((34, 28), f"{user_name}'s Arena", font=FONT_XL, fill=palette['title'])
    draw.text((34, 76), f"Arena Rank: {owner_rank_label}", font=FONT_S, fill=palette['subtitle'])
    outer = (24, 112, 936, 694)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette['outer_outline'], width=3)
    intro_box = (52, 142, 908, 300)
    draw.rounded_rectangle(intro_box, 24, fill=palette['panel_fill'], outline=palette['info_outline'], width=3)
    intro_lines = [
        '• Set Team creates your Arena defense team only for Arena.',
        '• Battle shows 9 nearby opponents and uses your active team on offense.',
        '• Duelists fill the Arena ladder and use max level aligned support decks plus equip spells.',
        '• Rank #1 is the top spot. Defeat opponents to climb up the ranks and become the best!',
    ]
    draw.text((76, 156), 'Arena Rules', font=FONT_L_BOLD, fill=palette['title'])
    draw_centered_text_block(draw, (76, 192, 884, 286), intro_lines, [FONT_XS] * len(intro_lines), [palette['info_text']] * len(intro_lines), line_gap=8, x_align='left')
    sections = [
        ('Set Team', 'Defense Team', 'Arena Only', 'Choose the 3 monsters other players and duelists will challenge.', palette['accent_binder']),
        ('Battle', f'Arena Rank: {owner_rank_title}', f'Rank #{owner_rank_position}', 'Browse the challenge roster, then attack with your active team.', palette['accent_support']),
        ('Leaderboard', 'Top 30', 'Defense Teams', 'See the highest Arena players and duelists on the ladder.', palette['accent_dust']),
    ]
    box_w = 260
    gap = 18
    start_x = 52
    y = 332
    for idx, (label, stat_a, stat_b, desc, accent) in enumerate(sections):
        left = start_x + idx * (box_w + gap)
        box = (left, y, left + box_w, y + 194)
        draw.rounded_rectangle(box, 24, fill=palette['card_fill'], outline=accent, width=3)
        draw.text((left + 18, y + 18), label, font=FONT_L_BOLD, fill=palette['title'])
        fitted_stat_a, stat_a_font = fit_text_to_width(stat_a, box_w - 36, (FONT_M_BOLD, FONT_S_BOLD, FONT_XS_BOLD))
        draw.text((left + 18, y + 58), fitted_stat_a, font=stat_a_font, fill=accent)
        draw.text((left + 18, y + 90), stat_b, font=FONT_S_BOLD, fill=palette['accent_dust'])
        desc_lines = fit_wrapped_text(draw, desc, FONT_S, box_w - 36, 4)
        draw_centered_text_block(draw, (left + 18, y + 116, left + box_w - 18, y + 172), desc_lines, [FONT_S] * len(desc_lines), [palette['info_text']] * len(desc_lines), line_gap=5, x_align='left')
    footer_box = (52, 556, 908, 616)
    draw.rounded_rectangle(footer_box, 20, fill=palette['panel_fill'], outline=palette['outer_outline'], width=2)
    draw_centered_text_block(draw, (74, 568, 886, 604), ['Use the buttons below to set a defense team, open the challenge roster, or view the leaderboard.'], [FONT_S], [palette['info_text']], line_gap=6)
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer


def render_unknown_monster_box(size: Tuple[int, int], radius: int = 14) -> Image.Image:
    box = Image.new('RGBA', size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(box)
    draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=(0, 0, 0, 255), outline=(255, 255, 255, 255), width=2)
    font = FONT_L_BOLD if min(size) >= 56 else FONT_M_BOLD
    draw.text((size[0] / 2, size[1] / 2 + 1), '?', font=font, fill=(255, 255, 255, 255), anchor='mm')
    return box


def render_arena_monster_strip(img: Image.Image, fighters: Sequence[Fighter], origin: Tuple[int, int], monster_size: Tuple[int, int] = (56, 56), gap: int = 10, *, visible_count: int = TEAM_SIZE):
    x, y = origin
    fighters_list = list(fighters)[:TEAM_SIZE]
    for idx in range(TEAM_SIZE):
        if idx < len(fighters_list) and idx < max(0, int(visible_count)):
            portrait = open_card_image(str(fighters_list[idx].image_path), monster_size, radius=14)
        else:
            portrait = render_unknown_monster_box(monster_size, radius=14)
        img.alpha_composite(portrait, (x, y))
        x += monster_size[0] + gap


@with_panel_user_context(lambda user_name, owner_id, *args, **kwargs: owner_id)
def render_arena_opponents_page(user_name: str, owner_id: int, opponents: Sequence[dict], page_label: str = '1') -> io.BytesIO:
    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_user_ui_theme_palette(owner_id)
    draw.text((34, 28), f"{user_name}'s Challenge Roster", font=FONT_XL, fill=palette['title'])
    draw.text((34, 76), f"Choose one opponent from the dropdown below • Page {page_label}", font=FONT_S, fill=palette['subtitle'])
    outer = (24, 112, 936, 934)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette['outer_outline'], width=3)
    card_w = 268
    card_h = 238
    gap_x = 28
    gap_y = 24
    start_x = 58
    start_y = 146
    avatar_size = (72, 72)
    monster_size = (64, 64)
    for idx, entry in enumerate(list(opponents)[:ARENA_MATCH_COUNT], start=1):
        col = (idx - 1) % 3
        row = (idx - 1) // 3
        left = start_x + col * (card_w + gap_x)
        top = start_y + row * (card_h + gap_y)
        box = (left, top, left + card_w, top + card_h)
        accent = palette['accent_support'] if entry.get('kind') == 'user' else palette['accent_dust']
        draw.rounded_rectangle(box, 24, fill=palette['card_fill'], outline=accent, width=3)

        rank_position = int(entry.get('rank_position', idx))
        rank_title = str(entry.get('rank_title') or arena_rank_title(rank_position))
        title_box = (left + 12, top + 16, left + 92, top + 40)
        draw_centered_text_block(draw, title_box, [ellipsize_text(draw, rank_title, FONT_XS_BOLD, title_box[2] - title_box[0])], [FONT_XS_BOLD], [accent], line_gap=0, x_align='center')

        avatar = arena_entry_avatar_image(entry, avatar_size)
        paste_avatar_with_outline(img, avatar, (left + 16, top + 52), (*palette['avatar_outline'], 255), outline_width=3)

        name_text = ellipsize_text(draw, str(entry['name']), FONT_S_BOLD, card_w - 114)
        draw.text((left + 100, top + 52), name_text, font=FONT_S_BOLD, fill=palette['title'])
        draw.text((left + 100, top + 80), f"Rank #{rank_position}", font=FONT_XS_BOLD, fill=accent)
        detail_text = 'Duelist' if entry.get('kind') == 'duelist' else str(entry.get('record') or f"{int(entry.get('elo', ARENA_STARTING_ELO))} rating")
        draw.text((left + 100, top + 102), ellipsize_text(draw, detail_text, FONT_XS, card_w - 114), font=FONT_XS, fill=palette['info_text'])

        team = arena_entry_team(entry)
        monster_y = top + 154
        slot_gap = 12
        total_monster_width = TEAM_SIZE * monster_size[0] + (TEAM_SIZE - 1) * slot_gap
        monster_x = left + (card_w - total_monster_width) // 2
        visible_count = 0 if entry.get('kind') == 'duelist' else max(0, TEAM_SIZE - 1)
        render_arena_monster_strip(img, team[:TEAM_SIZE], (monster_x, monster_y), monster_size=monster_size, gap=slot_gap, visible_count=visible_count)
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer


@with_panel_user_context(lambda owner_id, *args, **kwargs: owner_id)
def render_arena_leaderboard_page(owner_id: int, entries: Sequence[dict], page: int, max_page: int) -> io.BytesIO:
    width, height = 960, 900
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_user_ui_theme_palette(owner_id)
    draw.text((34, 28), 'Arena Leaderboard', font=FONT_XL, fill=palette['title'])
    draw.text((34, 76), f'Top {len(entries)} entries • Page {page + 1}/{max_page + 1}', font=FONT_S, fill=palette['subtitle'])
    outer = (24, 112, 936, 874)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette['outer_outline'], width=3)
    top = 144
    start_rank = page * ARENA_LEADERBOARD_PAGE_SIZE + 1
    page_entries = entries[page * ARENA_LEADERBOARD_PAGE_SIZE:(page + 1) * ARENA_LEADERBOARD_PAGE_SIZE]
    avatar_size = (52, 52)
    for offset, entry in enumerate(page_entries, start=0):
        rank = start_rank + offset
        accent = palette['accent_support'] if entry.get('kind') == 'user' else palette['accent_dust']
        row_bottom = top + 80
        draw.line((52, row_bottom, 908, row_bottom), fill=accent, width=2)
        avatar = arena_entry_avatar_image(entry, avatar_size)
        paste_avatar_with_outline(img, avatar, (64, top + 8), (*palette['avatar_outline'], 255), outline_width=3)
        draw.text((132, top + 8), ellipsize_text(draw, str(entry['name']), FONT_S_BOLD, 270), font=FONT_S_BOLD, fill=palette['title'])
        sub = str(entry.get('rank_label', arena_rank_label(entry.get('rank_position', rank))))
        if entry.get('kind') == 'duelist':
            sub += ' • Duelist'
        elif entry.get('kind') == 'user' and entry.get('record'):
            sub += f" • {entry['record']}"
        draw.text((132, top + 34), ellipsize_text(draw, sub, FONT_XS, 330), font=FONT_XS, fill=palette['info_text'])
        defense_text = arena_entry_defense_team_text(entry)
        draw.text((132, top + 54), ellipsize_text(draw, defense_text, FONT_XS, 420), font=FONT_XS, fill=accent)
        visible_count = 0 if entry.get('kind') == 'duelist' else max(0, TEAM_SIZE - 1)
        render_arena_monster_strip(img, arena_entry_team(entry), (572, top + 12), visible_count=visible_count)
        top += 84
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer


@with_panel_user_context(lambda owner_id, *args, **kwargs: owner_id)
def render_arena_team_builder_page(owner_id: int, user_name: str, picks: Sequence[str], rows: Sequence[sqlite3.Row], page: int, total_pages: int, filter_key: str, sort_key: str) -> io.BytesIO:
    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_user_ui_theme_palette(owner_id)
    draw.text((34, 28), f"{user_name}'s Arena Defense Team", font=FONT_XL, fill=palette['title'])
    draw.text((34, 76), f"Filter: {str(filter_key).upper()} • Sort: {str(sort_key).upper()} • Page {page}/{total_pages}", font=FONT_S, fill=palette['subtitle'])
    outer = (24, 112, 936, 934)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette['outer_outline'], width=3)

    pick_box = (52, 144, 908, 286)
    draw.rounded_rectangle(pick_box, 24, fill=palette['panel_fill'], outline=palette['info_outline'], width=3)
    draw.text((72, 162), f"Current Defense Picks ({len(picks)}/{TEAM_SIZE})", font=FONT_M_BOLD, fill=palette['title'])
    slot_w = 240
    for idx in range(TEAM_SIZE):
        left = 72 + idx * (slot_w + 24)
        slot = (left, 196, left + slot_w, 262)
        draw.rounded_rectangle(slot, 16, fill=palette['card_fill'], outline=palette['accent_binder'], width=2)
        if idx < len(picks):
            fighter = make_fighter_from_id(str(picks[idx]), user_id=owner_id)
            if fighter is not None:
                portrait = open_card_image(fighter.image_path, (52, 52), radius=12)
                img.alpha_composite(portrait, (left + 8, 203))
                draw.text((left + 70, 216), ellipsize_text(draw, fighter.name, FONT_S_BOLD, slot_w - 84), font=FONT_S_BOLD, fill=palette['title'])
                draw.text((left + 70, 240), f"{fighter.attribute} • {format_monster_types(fighter.monster_types)}", font=FONT_XS, fill=palette['info_text'])
            else:
                draw.text(((slot[0] + slot[2]) / 2, (slot[1] + slot[3]) / 2), 'Empty', font=FONT_S_BOLD, fill=palette['info_text'], anchor='mm')
        else:
            draw.text(((slot[0] + slot[2]) / 2, (slot[1] + slot[3]) / 2), 'Empty', font=FONT_S_BOLD, fill=palette['info_text'], anchor='mm')

    grid_top = 314
    card_w = 412
    card_h = 112
    x_positions = [52, 496]
    for idx, row in enumerate(list(rows)[:8]):
        col = idx % 2
        grid_row = idx // 2
        left = x_positions[col]
        top = grid_top + grid_row * (card_h + 18)
        box = (left, top, left + card_w, top + card_h)
        accent = palette['accent_support'] if str(row['instance_id']) not in picks else palette['accent_dust']
        draw.rounded_rectangle(box, 20, fill=palette['card_fill'], outline=accent, width=2)
        portrait = open_card_image(str(row['image_path']), (88, 88), radius=16)
        img.alpha_composite(portrait, (left + 12, top + 12))
        draw.text((left + 114, top + 16), ellipsize_text(draw, str(row['monster_name']), FONT_S_BOLD, 274), font=FONT_S_BOLD, fill=palette['title'])
        stats = resolve_row_combat_stats(row)
        draw.text((left + 114, top + 42), f"{row['attribute']} • {format_monster_types(decode_monster_types(row['monster_types']))}", font=FONT_XS, fill=palette['info_text'])
        draw.text((left + 114, top + 66), f"HP {stats[0]}  ATK {stats[1]}  DEF {stats[2]}  SPD {stats[3]}", font=FONT_XS_BOLD, fill=accent)

    footer = (52, 882, 908, 916)
    draw.rounded_rectangle(footer, 16, fill=palette['panel_fill'], outline=palette['outer_outline'], width=2)
    draw.text((72, 899), 'Select a monster from the dropdown. The image preview above updates as you filter and page.', font=FONT_XS, fill=palette['info_text'], anchor='lm')
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer
