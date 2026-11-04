"""Rendering the battle, campaign, Dueling Grounds and tournament hub pages."""

from __future__ import annotations

import io
import textwrap
from typing import Optional

import discord
from PIL import ImageDraw

from kuribot.config import (
    CAMPAIGN_AREA_BG_ALPHA, CAMPAIGN_AREA_TINT_ALPHA, CAMPAIGN_AREAS,
    CAMPAIGN_REQUIRED_WINS_PER_DUELIST,
)
from kuribot.database import db
from kuribot.render.fonts import (
    FONT_L_BOLD, FONT_M_BOLD, FONT_S, FONT_S_BOLD, FONT_XL, FONT_XL_BOLD, FONT_XS, FONT_XS_BOLD,
    get_bold_font,
)
from kuribot.render.themes import get_user_ui_theme_palette
from kuribot.render.images import (
    open_binder_canvas_background, paste_binder_panel, paste_panel_background_image,
    with_panel_user_context,
)
from kuribot.render.text import (
    _fit_text_block_fonts, draw_centered_text_block, draw_evenly_spaced_text_block,
    fit_wrapped_text, wrap_text,
)
from kuribot.campaign import (
    campaign_area_status_text, campaign_area_total_wins, get_campaign_area,
    get_campaign_duelist_keys, is_campaign_area_unlocked,
)
from kuribot.tournament import (
    _draw_tourney_team_image_row, _tourney_champion, _tourney_current_match, _tourney_get,
    _tourney_name, _tourney_round_label, _tourney_score, _tourney_setup, TOURNAMENT_WINNER_GOLD,
    TOURNAMENT_WINNER_PREMIUM_TICKETS, TOURNAMENT_WINNER_TICKETS,
)


@with_panel_user_context(lambda user_name, owner_id, *args, **kwargs: owner_id)
def render_binder_hub_page(user_name: str, owner_id: int) -> io.BytesIO:
    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_user_ui_theme_palette(owner_id)

    monster_count = len(db.user_inventory(owner_id))
    card_count = len(db.user_support_cards(owner_id))
    deck_count = len(db.list_support_decks(owner_id))
    extra_deck_count = len(db.list_extra_decks(owner_id)) if hasattr(db, "list_extra_decks") else 0
    team_count = len(db.list_teams(owner_id))

    draw.text((34, 28), f"{user_name}'s Binder", font=FONT_XL, fill=palette["title"])
    draw.text((34, 76), "Browse monsters, support cards, decks, Extra Decks, and teams from one hub.", font=FONT_S, fill=palette["subtitle"])

    outer = (24, 112, 936, 934)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    intro_box = (52, 144, 908, 294)
    draw.rounded_rectangle(intro_box, 24, fill=palette["panel_fill"], outline=palette["info_outline"], width=3)
    intro_lines = [
        "• Monsters: stores every owned unit and shows their element and type(s).",
        "• Cards: tracks your support cards and the stat bonuses or special effects they grant.",
        "• Decks: holds your saved support builds and marks the active one.",
        "• Extra Decks: stores saved Extra Deck Monster lineups for battle summons.",
        "• Teams: displays your monster lineups that you can use in battle.",
    ]
    title_bbox = draw.textbbox((0, 0), "Binder Overview", font=FONT_L_BOLD)
    title_height = title_bbox[3] - title_bbox[1]
    line_height = 22
    line_block_height = len(intro_lines) * line_height
    block_gap = 16
    total_intro_height = title_height + block_gap + line_block_height
    intro_top = intro_box[1] + ((intro_box[3] - intro_box[1]) - total_intro_height) // 2
    draw.text((76, intro_top), "Binder Overview", font=FONT_L_BOLD, fill=palette["title"])
    lines_y = intro_top + title_height + block_gap
    for i, line in enumerate(intro_lines):
        draw.text((76, lines_y + i * line_height), line, font=FONT_XS, fill=palette["info_text"])

    binder_section_accents = {
        "Monsters": (120, 170, 255),
        "Cards": (190, 145, 255),
        "Decks": (255, 215, 90),
        "Extra Decks": (190, 132, 255),
        "Teams": (110, 210, 140),
    }
    sections = [
        ("Monsters", monster_count, "Summoned monsters, traded monsters, and gifted monsters.", binder_section_accents["Monsters"]),
        ("Cards", card_count, "Support cards and their attribute-based boosts or effects.", binder_section_accents["Cards"]),
        ("Decks", deck_count, "Saved support decks with active deck tracking.", binder_section_accents["Decks"]),
        ("Extra Decks", extra_deck_count, "Extra Deck Monster lineups used by your teams.", binder_section_accents["Extra Decks"]),
        ("Teams", team_count, "Monster lineups used for battle and active team setup.", binder_section_accents["Teams"]),
    ]

    box_w = 398
    box_h = 142
    x_positions = [52, 510]
    y_positions = [318, 480, 642]

    for idx, (label, count, desc, accent) in enumerate(sections):
        col = idx % 2
        row = idx // 2
        left = x_positions[col]
        top = y_positions[row]
        box = (left, top, left + box_w, top + box_h)
        draw.rounded_rectangle(box, 24, fill=palette["card_fill"], outline=accent, width=3)

        badge_box = (left + 16, top + 14, left + 118, top + 44)
        draw.rounded_rectangle(badge_box, 12, fill=palette["card_fill"], outline=accent, width=2)
        draw.text(((badge_box[0] + badge_box[2]) / 2, (badge_box[1] + badge_box[3]) / 2 + 1), label.upper(), font=FONT_XS_BOLD, fill=palette["title"], anchor="mm")

        draw.text((left + 20, top + 54), label, font=FONT_M_BOLD, fill=palette["title"])
        count_label = "owned" if label in {"Monsters", "Cards"} else "saved"
        draw.text((left + 20, top + 84), f"{count} {count_label}", font=FONT_S_BOLD, fill=accent)

        desc_lines = wrap_text(draw, desc, FONT_XS, 352)
        for i, line in enumerate(desc_lines[:2]):
            draw.text((left + 20, top + 108 + i * 17), line, font=FONT_XS, fill=palette["info_text"])

    footer_box = (52, 814, 908, 900)
    draw.rounded_rectangle(footer_box, 20, fill=palette["panel_fill"], outline=palette["outer_outline"], width=2)
    footer_lines = [
        "Use the buttons below to open each section.",
        "Inside each browser you can filter, sort, page through entries, and inspect the selected item.",
    ]
    for i, line in enumerate(footer_lines):
        draw.text((74, 832 + i * 24), line, font=FONT_S, fill=palette["info_text"])

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


@with_panel_user_context(lambda user_name, owner_id, *args, **kwargs: owner_id)
def render_battle_hub_page(user_name: str, owner_id: int) -> io.BytesIO:
    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_user_ui_theme_palette(owner_id)

    draw.text((34, 28), f"{user_name}'s Adventure Hub", font=FONT_XL, fill=palette["title"])
    draw.text((34, 76), "Choose an adventure mode below. Campaign is live and Duel Tower is still under construction.", font=FONT_S, fill=palette["subtitle"])

    outer = (24, 112, 936, 934)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    intro_box = (52, 144, 908, 294)
    draw.rounded_rectangle(intro_box, 24, fill=palette["panel_fill"], outline=palette["info_outline"], width=3)
    intro_lines = [
        "• Campaign: clear stages in order, unlock the next stage, and earn first-clear rewards.",
        "• Duel Tower: under construction.",
    ]
    title_bbox = draw.textbbox((0, 0), "Battle Overview", font=FONT_L_BOLD)
    title_height = title_bbox[3] - title_bbox[1]
    line_height = 24
    line_block_height = len(intro_lines) * line_height
    block_gap = 16
    total_intro_height = title_height + block_gap + line_block_height
    intro_top = intro_box[1] + ((intro_box[3] - intro_box[1]) - total_intro_height) // 2
    draw.text((76, intro_top), "Battle Overview", font=FONT_L_BOLD, fill=palette["title"])
    lines_y = intro_top + title_height + block_gap
    for i, line in enumerate(intro_lines):
        draw.text((76, lines_y + i * line_height), line, font=FONT_S, fill=palette["info_text"])

    sections = [
        ("Campaign", campaign_area_status_text(owner_id, CAMPAIGN_AREAS[0]["key"]), "4 areas • 24 stages", "Story progression with area unlocks and first-clear rewards.", palette["accent_binder"]),
        ("Duel Tower", "Soon", "Under construction", "Free-duel adventure mode is still under construction.", (255, 215, 90)),
        ("Rewards", "Gold + Tickets", "Area clear bonus", "Each stage gives rewards, and clearing an entire area grants bonus rewards.", palette["panel_outline"]),
    ]

    box_w = 398
    box_h = 200
    x_positions = [52, 510]
    y_positions = [328, 560]

    for idx, (label, stat_a, stat_b, desc, accent) in enumerate(sections):
        col = idx % 2
        row = idx // 2
        left = x_positions[col]
        top = y_positions[row]
        box = (left, top, left + box_w, top + box_h)
        draw.rounded_rectangle(box, 24, fill=palette["card_fill"], outline=accent, width=3)

        badge_box = (left + 16, top + 14, left + 170, top + 44)
        draw.rounded_rectangle(badge_box, 12, fill=palette["card_fill"], outline=accent, width=2)
        draw.text(((badge_box[0] + badge_box[2]) / 2, (badge_box[1] + badge_box[3]) / 2 + 1), label.upper(), font=FONT_XS_BOLD, fill=palette["title"], anchor="mm")

        desc_lines = wrap_text(draw, desc, FONT_S, 344)
        info_lines = [label, stat_a, stat_b, *desc_lines[:2]]
        fonts = [FONT_L_BOLD, FONT_M_BOLD, FONT_S_BOLD, *([FONT_S] * min(2, len(desc_lines)))]
        fills = [palette["title"], accent, palette["accent_dust"], *([palette["info_text"]] * min(2, len(desc_lines)))]
        text_box_top = top + 52
        if label == "Endless Duel":
            text_box_top -= 22
        text_box = (left + 18, text_box_top, left + box_w - 18, top + box_h - 18)
        draw_centered_text_block(draw, text_box, info_lines, fonts, fills, line_gap=5)

    footer_box = (52, 822, 908, 884)
    draw.rounded_rectangle(footer_box, 20, fill=palette["panel_fill"], outline=palette["outer_outline"], width=2)
    draw_centered_text_block(draw, (74, 834, 886, 872), ["Use the buttons below to start an adventure mode."], [FONT_S], [palette["info_text"]], line_gap=6)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


@with_panel_user_context(lambda user_name, owner_id, *args, **kwargs: owner_id)
def render_campaign_hub_page(user_name: str, owner_id: int, hard_mode: bool = False) -> io.BytesIO:
    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_user_ui_theme_palette(owner_id)

    campaign_title = f"{user_name}'s Campaign" + (" • Hard Mode" if hard_mode else "")
    subtitle = "Pick an area, then choose a duelist from the dropdown to trigger their pre-battle speech."
    if hard_mode:
        subtitle = "Hard Mode is active. Duelists use aligned support decks, generated equip spells, and better rewards."
    draw.text((34, 28), campaign_title, font=FONT_XL, fill=palette["title"])
    draw.text((34, 76), subtitle, font=FONT_S, fill=palette["subtitle"])

    outer = (24, 112, 936, 934)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    intro_box = (52, 144, 908, 294)
    draw.rounded_rectangle(intro_box, 24, fill=palette["panel_fill"], outline=palette["info_outline"], width=3)
    intro_lines = [
        "• Each area has 6 duelists.",
        f"• Defeat every duelist {CAMPAIGN_REQUIRED_WINS_PER_DUELIST} times to unlock the next area.",
        "• Domino City duelists use no support decks.",
        "• Duelist Kingdom uses random weak support; Battle City and Duel Tower use attribute-matched support.",
    ]
    rules_fonts = [FONT_L_BOLD, *([FONT_XS] * len(intro_lines))]
    rules_fills = [palette["title"], *([palette["info_text"]] * len(intro_lines))]
    draw_centered_text_block(draw, (76, 156, 884, 282), ["Campaign Rules", *intro_lines], rules_fonts, rules_fills, line_gap=8, x_align="left")

    box_w = 398
    box_h = 200
    x_positions = [52, 510]
    y_positions = [328, 560]
    for idx, area in enumerate(CAMPAIGN_AREAS):
        col = idx % 2
        row = idx // 2
        left = x_positions[col]
        top = y_positions[row]
        accent = area["accent"]
        locked = not is_campaign_area_unlocked(owner_id, area["key"])
        status = campaign_area_status_text(owner_id, area["key"], hard_mode=hard_mode)
        box = (left, top, left + box_w, top + box_h)
        draw.rounded_rectangle(box, 24, fill=palette["card_fill"], outline=accent, width=3)
        paste_panel_background_image(img, box, area.get("background_path", ""), radius=24)
        badge_box = (left + 16, top + 14, left + 238, top + 44)
        draw.rounded_rectangle(badge_box, 12, fill=(*palette["card_fill"], 215), outline=accent, width=2)
        badge_label = area["label"] + (" - Hard Mode" if hard_mode else "")
        badge_font = FONT_XS_BOLD
        if draw.textlength(badge_label.upper(), font=badge_font) > (badge_box[2] - badge_box[0] - 20):
            badge_font = get_bold_font(12)
        badge_text = badge_label.upper()
        if draw.textlength(badge_text, font=badge_font) > (badge_box[2] - badge_box[0] - 20):
            badge_text = textwrap.shorten(badge_text, width=32, placeholder="...")
        draw.text(((badge_box[0] + badge_box[2]) / 2, (badge_box[1] + badge_box[3]) / 2 + 1), badge_text, font=badge_font, fill=palette["title"], anchor="mm")
        text_box = (left + 18, top + 52, left + box_w - 18, top + box_h - 18)
        area_display = area["label"] + (" - Hard Mode" if hard_mode else "")
        title_lines = fit_wrapped_text(draw, area_display, FONT_L_BOLD, 344, 2)
        header_lines = [*title_lines, status, "Locked" if locked else "Select a duelist below"]
        header_fonts = ([FONT_L_BOLD] * len(title_lines)) + [FONT_M_BOLD, FONT_S_BOLD]
        header_fills = ([palette["title"]] * len(title_lines)) + [accent, palette["empty_text"] if locked else palette["accent_dust"]]
        base_heights = []
        for line, font in zip(header_lines, header_fonts):
            bbox = draw.textbbox((0, 0), str(line), font=font)
            base_heights.append(bbox[3] - bbox[1])
        box_height = text_box[3] - text_box[1]
        base_total_height = sum(base_heights) + 5 * (len(header_lines) - 1)
        desc_line_height = draw.textbbox((0, 0), "Ag", font=FONT_S)[3] - draw.textbbox((0, 0), "Ag", font=FONT_S)[1]
        remaining_height = max(0, box_height - base_total_height)
        max_desc_lines = max(1, (remaining_height + 5) // (desc_line_height + 5)) if remaining_height > 0 else 1
        desc_lines = fit_wrapped_text(draw, area["description"], FONT_S, 344, max_desc_lines)
        info_lines = [*header_lines, *desc_lines]
        fonts = [*header_fonts, *([FONT_S] * len(desc_lines))]
        fills = [*header_fills, *([palette["info_text"]] * len(desc_lines))]
        draw_centered_text_block(draw, text_box, info_lines, fonts, fills, line_gap=5)

    footer_box = (52, 822, 908, 884)
    draw.rounded_rectangle(footer_box, 20, fill=palette["panel_fill"], outline=palette["outer_outline"], width=2)
    footer_text = "Press an area button to open its duelist dropdown." if not hard_mode else "Hard Mode is on. Press an area button to open its duelist dropdown."
    draw_centered_text_block(draw, (74, 834, 886, 872), [footer_text], [FONT_S], [palette["info_text"]], line_gap=6)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


@with_panel_user_context(lambda user_name, owner_id, *args, **kwargs: owner_id)
def render_campaign_area_page(user_name: str, owner_id: int, area_key: str, hard_mode: bool = False) -> io.BytesIO:
    area = get_campaign_area(area_key) or CAMPAIGN_AREAS[0]
    width, height = 960, 720
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_user_ui_theme_palette(owner_id)
    accent = area["accent"]
    total_wins = campaign_area_total_wins(owner_id, area["key"], hard_mode=hard_mode)
    total_needed = len(get_campaign_duelist_keys(area["key"])) * CAMPAIGN_REQUIRED_WINS_PER_DUELIST

    area_title = area['label'] + (" - Hard Mode" if hard_mode else "")
    draw.text((34, 28), f"{user_name} • {area_title}", font=FONT_XL, fill=palette["title"])
    draw.text((34, 76), "Use the duelist dropdown below to view their speech, then press Begin Battle.", font=FONT_S, fill=palette["subtitle"])

    outer = (24, 136, 936, 694)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    card = (78, 188, 882, 624)
    draw.rounded_rectangle(card, 28, fill=palette["card_fill"], outline=accent, width=3)
    paste_panel_background_image(img, card, area.get("background_path", ""), radius=28, image_alpha=CAMPAIGN_AREA_BG_ALPHA, tint_alpha=CAMPAIGN_AREA_TINT_ALPHA, tint_rgb=(18, 12, 30))

    badge = (106, 214, 372, 252)
    draw.rounded_rectangle(badge, 12, fill=(*palette["card_fill"], 215), outline=accent, width=2)
    badge_text = area_title.upper()
    badge_font = FONT_XS_BOLD
    if draw.textlength(badge_text, font=badge_font) > (badge[2] - badge[0] - 20):
        badge_font = get_bold_font(12)
    if draw.textlength(badge_text, font=badge_font) > (badge[2] - badge[0] - 20):
        badge_text = textwrap.shorten(badge_text, width=30, placeholder="...")
    draw.text(((badge[0] + badge[2]) / 2, (badge[1] + badge[3]) / 2 + 1), badge_text, font=badge_font, fill=palette["title"], anchor="mm")

    lines = [
        area_title,
        campaign_area_status_text(owner_id, area["key"], hard_mode=hard_mode),
        area["description"],
        f"Area progress: {total_wins}/{total_needed} total wins.",
        f"Every listed duelist must reach {CAMPAIGN_REQUIRED_WINS_PER_DUELIST}/{CAMPAIGN_REQUIRED_WINS_PER_DUELIST} wins to clear the area.",
        "Hard Mode rewards are boosted." if hard_mode else "",
    ]
    lines = [line for line in lines if line]
    desc_lines = fit_wrapped_text(draw, area["description"], FONT_S, 680, 3)
    lines = [area_title, campaign_area_status_text(owner_id, area["key"], hard_mode=hard_mode), *desc_lines, f"Area progress: {total_wins}/{total_needed} total wins.", f"Every listed duelist must reach {CAMPAIGN_REQUIRED_WINS_PER_DUELIST}/{CAMPAIGN_REQUIRED_WINS_PER_DUELIST} wins to clear the area."]
    if hard_mode:
        lines.append("Hard Mode rewards are boosted.")
    fonts = [FONT_L_BOLD, FONT_L_BOLD] + ([FONT_S] * (len(desc_lines) + 2))
    fills = [palette["title"], accent] + ([palette["info_text"]] * (len(desc_lines) + 1)) + [palette["accent_dust"]]
    if hard_mode:
        fonts.append(FONT_S)
        fills.append(palette["accent_dust"])
    draw_centered_text_block(draw, (118, 286, 842, 562), lines, fonts[:len(lines)], fills[:len(lines)], line_gap=12, x_align="center")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


def make_battle_hub_file(user_name: str, owner_id: int) -> discord.File:
    return discord.File(render_battle_hub_page(user_name, owner_id), filename="battle_hub.png")


def make_dueling_grounds_hub_file(user_name: str, owner_id: int) -> discord.File:
    return discord.File(render_dueling_grounds_hub_page(user_name, owner_id), filename="dueling_grounds_hub.png")


def make_campaign_hub_file(user_name: str, owner_id: int, hard_mode: bool = False) -> discord.File:
    return discord.File(render_campaign_hub_page(user_name, owner_id, hard_mode=hard_mode), filename="campaign_hub.png")


def make_campaign_area_file(user_name: str, owner_id: int, area_key: str, hard_mode: bool = False) -> discord.File:
    return discord.File(render_campaign_area_page(user_name, owner_id, area_key, hard_mode=hard_mode), filename="campaign_area.png")


@with_panel_user_context(lambda user_name, owner_id, *args, **kwargs: owner_id)
def render_dueling_grounds_hub_page(user_name: str, owner_id: int) -> io.BytesIO:
    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_user_ui_theme_palette(owner_id)
    draw.text((34, 28), f"{user_name}'s Dueling Grounds", font=FONT_XL, fill=palette['title'])
    outer = (24, 112, 936, 934)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette['outer_outline'], width=3)
    intro_box = (52, 140, 908, 268)
    draw.rounded_rectangle(intro_box, 24, fill=palette['panel_fill'], outline=palette['info_outline'], width=3)
    intro_lines = [
        '• Normal: fight one random duelist.',
        '• Ranked: uses your active team, starts at 1000 ELO, and can match duelists or users.',
        '• Arena: ladder ranked with offense and defense teams.',
        '• Lobby: make custom lobbies for yourself or multiple users; can pick a duelist to battle.',
    ]
    draw_centered_text_block(draw, (76, 154, 884, 252), ['Dueling Grounds Rules', *intro_lines], [FONT_L_BOLD, *([FONT_XS] * len(intro_lines))], [palette['title'], *([palette['info_text']] * len(intro_lines))], line_gap=7, x_align='left')
    sections = [
        ('Normal', '', 'Random opponent', 'Starts a duel immediately against one random duelist.', palette['accent_support']),
        ('Ranked', '', 'ELO + GXE', 'Fight duelists or users with active teams on the ladder.', palette['accent_support']),
        ('Arena', '', 'Defense + Ladder', 'Set an Arena defense team, browse 9 opponents, and climb ranks.', (255, 215, 90)),
        ('Tournament', '', '16-player bracket', 'Best-of-3 single elimination tournament with prize rewards.', (190, 150, 255)),
        ('Lobby', '', 'Custom rooms', 'Custom lobbies against duelists and/or users.', palette['accent_binder']),
    ]
    box_w = 398
    box_h = 140
    x_positions = [52, 510]
    y_positions = [294, 452, 610]
    for idx, (label, stat_a, stat_b, desc, accent) in enumerate(sections):
        col = idx % 2
        row = idx // 2
        left = x_positions[col]
        top = y_positions[row]
        box = (left, top, left + box_w, top + box_h)
        draw.rounded_rectangle(box, 24, fill=palette['card_fill'], outline=accent, width=3)
        badge_box = (left + 16, top + 12, left + 170, top + 40)
        draw.rounded_rectangle(badge_box, 12, fill=palette['card_fill'], outline=accent, width=2)
        draw.text(((badge_box[0] + badge_box[2]) / 2, (badge_box[1] + badge_box[3]) / 2 + 1), label.upper(), font=FONT_XS_BOLD, fill=palette['title'], anchor='mm')
        desc_lines = wrap_text(draw, desc, FONT_S, 336)
        info_items = [
            (label, FONT_L_BOLD, palette['title']),
            (stat_a, FONT_M_BOLD, accent),
            (stat_b, FONT_S_BOLD, palette['accent_dust']),
            *[(line, FONT_S, palette['info_text']) for line in desc_lines[:2]],
        ]
        info_items = [(line, font, fill) for line, font, fill in info_items if str(line or '').strip()]
        info_lines = [line for line, _font, _fill in info_items]
        fonts = [_font for _line, _font, _fill in info_items]
        fills = [_fill for _line, _font, _fill in info_items]
        text_box = (left + 22, top + 42, left + box_w - 22, top + box_h - 14)
        fitted_fonts, _ = _fit_text_block_fonts(draw, text_box, info_lines, fonts, line_gap=4, min_font_size=12)
        draw_evenly_spaced_text_block(draw, text_box, info_lines, fitted_fonts, fills, min_gap=4)
    footer_box = (52, 786, 908, 840)
    draw.rounded_rectangle(footer_box, 20, fill=palette['panel_fill'], outline=palette['outer_outline'], width=2)
    draw_centered_text_block(draw, (74, 796, 886, 828), ['Press a mode below to continue. Arena now has its own dedicated hub.'], [FONT_S], [palette['info_text']], line_gap=6)
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer


@with_panel_user_context(lambda user_name, owner_id, *args, **kwargs: owner_id)
def render_tournament_hub_page(user_name: str, owner_id: int, setup_override: Optional[dict] = None) -> io.BytesIO:
    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_user_ui_theme_palette(owner_id)
    # Use the shared custom panel renderer so Tournament respects the player's
    # equipped background image, UI theme, and binder-panel treatment just like
    # the other hubs.
    outer = (24, 24, 936, 936)
    paste_binder_panel(img, outer, radius=36, user_id=owner_id)
    draw.rounded_rectangle(outer, 36, outline=palette['outer_outline'], width=4)
    draw.text((480, 72), 'KURIBOT TOURNAMENT', font=FONT_XL_BOLD, fill=palette['title'], anchor='mm')
    draw.text((480, 118), f"{user_name}'s 16-player bracket", font=FONT_M_BOLD, fill=palette['subtitle'], anchor='mm')
    rules = [
        '• Choose 1 Main Team.',
        '• Add up to 2 Secondary Teams for swaps between battles.',
        '• 16 contestants. Best-of-3 matches. Single elimination.',
        '• Matches can be watched at normal speed or skipped one battle at a time.',
        '• You can complete 1 Tournament per day. Progress is saved after every battle.',
        f'• Champion prize: {TOURNAMENT_WINNER_GOLD:,} Gold, {TOURNAMENT_WINNER_TICKETS} Tickets, {TOURNAMENT_WINNER_PREMIUM_TICKETS} Random Packs.',
    ]
    functions_box = (70, 160, 890, 386)
    draw.rounded_rectangle(functions_box, 28, fill=palette['panel_fill'], outline=palette['info_outline'], width=3)
    text_x = 100
    title_text = 'Tournament Functions'
    title_gap = 10
    rule_gap = 6
    title_bbox = draw.textbbox((0, 0), title_text, font=FONT_L_BOLD)
    title_h = title_bbox[3] - title_bbox[1]
    rule_metrics = []
    for rule in rules:
        bbox = draw.textbbox((0, 0), rule, font=FONT_S)
        rule_metrics.append((rule, bbox, bbox[3] - bbox[1]))
    content_h = title_h + title_gap + sum(metric[2] for metric in rule_metrics) + (rule_gap * max(0, len(rule_metrics) - 1))
    text_y = functions_box[1] + ((functions_box[3] - functions_box[1] - content_h) // 2)
    draw.text((text_x, text_y - title_bbox[1]), title_text, font=FONT_L_BOLD, fill=palette['title'])
    text_y += title_h + title_gap
    for rule, rule_bbox, rule_h in rule_metrics:
        draw.text((text_x, text_y - rule_bbox[1]), rule, font=FONT_S, fill=palette['info_text'])
        text_y += rule_h + rule_gap
    setup = dict(setup_override) if setup_override is not None else _tourney_setup(owner_id)
    setup['main_team_id'] = int(setup.get('main_team_id') or 0)
    setup['secondary_team_ids'] = [int(x) for x in setup.get('secondary_team_ids', []) if int(x or 0)][:2]
    rows = {int(r['id']): r for r in db.list_teams(owner_id)}
    main_row = rows.get(setup['main_team_id'])
    secondary_rows = [rows.get(tid) for tid in setup.get('secondary_team_ids', []) if tid in rows][:2]
    team_panel_boxes = [
        (78, 420, 882, 508),
        (78, 522, 882, 610),
        (78, 624, 882, 712),
    ]
    _draw_tourney_team_image_row(img, draw, main_row, owner_id, 'Main Team', team_panel_boxes[0], palette)
    _draw_tourney_team_image_row(img, draw, secondary_rows[0] if len(secondary_rows) > 0 else None, owner_id, 'Secondary 1', team_panel_boxes[1], palette)
    _draw_tourney_team_image_row(img, draw, secondary_rows[1] if len(secondary_rows) > 1 else None, owner_id, 'Secondary 2', team_panel_boxes[2], palette)
    active = _tourney_get(int(owner_id))
    footer_y = 890
    if active:
        champ = _tourney_champion(active)
        cur = _tourney_current_match(active)
        next_text = f"Champion: {_tourney_name(active, champ)}" if champ else (f"Next: {_tourney_round_label(cur[0])} • {_tourney_score(active, cur[2])}" if cur else 'Next: waiting for bracket')
        draw.text((480, 868), next_text, font=FONT_XS_BOLD, fill=palette['accent_dust'], anchor='mm')
        footer_y = 908
    footer_text = 'Details and Sign-up are disabled. Teams unlocks Start once a main team is saved.'
    draw_centered_text_block(
        draw,
        (82, footer_y - 16, 878, 932),
        [footer_text],
        [FONT_XS_BOLD],
        [palette['info_text']],
        line_gap=0,
        x_align='center',
    )
    buffer = io.BytesIO(); img.save(buffer, format='PNG'); buffer.seek(0); return buffer
