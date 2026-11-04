"""Rendering shop pages."""

from __future__ import annotations

import io
import sqlite3
import textwrap
from typing import List, Sequence

import discord
from PIL import ImageDraw

from kuribot.config import (
    CARD_SHOP_MIN_COST, CARD_SHOP_SIZE, EQUIP_SHOP_MAX_COST, EQUIP_SHOP_MIN_COST, EQUIP_SHOP_SIZE,
    MONSTER_SHOP_SIZE, PACK_PURCHASE_COST, SUPPORT_CARD_IMAGE_PATH,
)
from kuribot.timeutil import reset_schedule_text
from kuribot.packs import pack_display_name
from kuribot.database import db
from kuribot.render.fonts import (
    FONT_L_BOLD, FONT_M_BOLD, FONT_S, FONT_S_BOLD, FONT_XL, FONT_XL_BOLD, FONT_XS, FONT_XS_BOLD,
)
from kuribot.render.themes import element_color, get_ui_theme_palette
from kuribot.render.images import (
    _ACTIVE_PANEL_USER_ID, _PanelUserContext, open_binder_canvas_background,
    open_card_image_contain_tight, paste_binder_panel, with_panel_user_context,
)
from kuribot.render.text import (
    _fit_text_block_fonts, draw_evenly_spaced_text_block, ellipsize_text, fit_wrapped_text,
    wrap_text,
)
from kuribot.cards.support_cards import support_card_compact_label
from kuribot.shops import (
    card_shop_offer_price, card_shop_offer_to_card, equip_shop_offer_price,
    equip_shop_offer_to_spell, monster_shop_offer_price,
)


@with_panel_user_context(lambda user_name, gold, offers, *args, **kwargs: int(offers[0]["user_id"]) if offers else _ACTIVE_PANEL_USER_ID.get())
def render_monster_shop_page(user_name: str, gold: int, offers: Sequence[sqlite3.Row], theme_name: str = "gray") -> io.BytesIO:
    width, height = 980, 900
    palette = get_ui_theme_palette(theme_name)
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    refresh_text = reset_schedule_text()
    draw.text((34, 28), f"{user_name}'s Pack Shop", font=FONT_XL, fill=palette["title"])
    draw.text((34, 76), f"6 daily themed packs • Each pack contains 3 monsters + 3 support cards • Price: {PACK_PURCHASE_COST:,} gold • Gold: {gold:,}", font=FONT_S, fill=palette["subtitle"])
    draw.text((34, 108), refresh_text, font=FONT_XS_BOLD, fill=palette["accent_dust"])
    outer = (24, 136, 956, 874)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, 34, outline=palette["outer_outline"], width=3)

    box_w, box_h = 430, 218
    x_positions = [48, 502]
    y_positions = [164, 396, 628]

    for idx, row in enumerate(offers[:MONSTER_SHOP_SIZE]):
        col = idx % 2
        row_idx = idx // 2
        base_x = x_positions[col]
        base_y = y_positions[row_idx]
        box = (base_x, base_y, base_x + box_w, base_y + box_h)
        theme_kind = str(row["theme_kind"] or "random")
        theme_value = str(row["theme_value"] or "")
        pack_name = str(row["pack_name"] or pack_display_name(str(row["pack_key"])))
        accent = element_color(theme_value if theme_kind == "attribute" else "LIGHT")
        if theme_kind == "boss":
            accent = (190, 132, 255)
        elif theme_kind == "type":
            accent = palette["accent_cards"]
        purchased = bool(row["purchased"])
        price = monster_shop_offer_price(row)
        draw.rounded_rectangle(box, 20, fill=palette["panel_fill"], outline=accent, width=3)

        icon_box = (base_x + 26, base_y + 52, base_x + 118, base_y + 178)
        card_img = open_card_image_contain_tight(str(SUPPORT_CARD_IMAGE_PATH), (92, 126), radius=8)
        card_x = icon_box[0] + ((icon_box[2] - icon_box[0]) - card_img.width) // 2
        card_y = icon_box[1] + ((icon_box[3] - icon_box[1]) - card_img.height) // 2
        img.paste(card_img, (card_x, card_y), card_img)
        draw.rounded_rectangle((card_x, card_y, card_x + card_img.width, card_y + card_img.height), 8, outline=accent, width=3)

        badge = (base_x + 14, base_y + 14, base_x + 150, base_y + 50)
        draw.rounded_rectangle(badge, 14, fill=palette["card_fill"], outline=accent, width=2)
        draw.text(((badge[0] + badge[2]) / 2, (badge[1] + badge[3]) / 2 + 1), "PACK", font=FONT_XS_BOLD, fill=palette["title"], anchor="mm")

        if purchased:
            sold_badge = (base_x + box_w - 132, base_y + 14, base_x + box_w - 14, base_y + 54)
            draw.rounded_rectangle(sold_badge, 12, fill=(20, 20, 20), outline=(240, 210, 120), width=2)
            draw.text(((sold_badge[0] + sold_badge[2]) / 2, (sold_badge[1] + sold_badge[3]) / 2), "SOLD", font=FONT_S_BOLD, fill=palette["accent_dust"], anchor="mm")

        text_box = (base_x + 148, base_y + 62, base_x + box_w - 18, base_y + box_h - 18)
        if theme_kind == "attribute":
            theme_line = f"Theme: {theme_value} monsters"
        elif theme_kind == "type":
            theme_line = f"Theme: {theme_value} monsters"
        elif theme_kind == "boss":
            theme_line = "Theme: 3 Extra Deck Monsters"
        else:
            theme_line = "Theme: Randomized"
        title = ellipsize_text(draw, pack_name, FONT_M_BOLD, text_box[2] - text_box[0])
        info_lines = [
            title,
            theme_line,
            "Contains: 3 Monsters",
            "Contains: 3 Support Cards",
            "Purchased" if purchased else f"Price: {price:,} Gold",
        ]
        fonts = [FONT_M_BOLD, FONT_XS_BOLD, FONT_XS, FONT_XS, FONT_S_BOLD]
        fills = [palette["title"], accent, palette["info_text"], palette["info_text"], palette["empty_text"] if purchased else palette["accent_dust"]]
        fitted_fonts, _ = _fit_text_block_fonts(draw, text_box, info_lines, fonts, line_gap=6, min_font_size=12)
        draw_evenly_spaced_text_block(draw, text_box, info_lines, fitted_fonts, fills, min_gap=5)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


@with_panel_user_context(lambda user_name, gold, offers, *args, **kwargs: int(offers[0]["user_id"]) if offers else _ACTIVE_PANEL_USER_ID.get())
def render_card_shop_page(user_name: str, gold: int, offers: Sequence[sqlite3.Row], theme_name: str = "gray") -> io.BytesIO:
    width, height = 980, 900
    palette = get_ui_theme_palette(theme_name)
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    refresh_text = reset_schedule_text()
    draw.text((34, 28), f"{user_name}'s Card Shop", font=FONT_XL, fill=palette["title"])
    draw.text((34, 76), f"6 daily offers • Support cards are maxed • Price: {CARD_SHOP_MIN_COST} Card Dust • Card Dust: {gold}", font=FONT_S, fill=palette["subtitle"])
    draw.text((34, 108), refresh_text, font=FONT_XS_BOLD, fill=palette["accent_dust"])
    outer = (24, 136, 956, 874)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, 34, outline=palette["outer_outline"], width=3)

    box_w, box_h = 430, 218
    x_positions = [48, 502]
    y_positions = [164, 396, 628]

    for idx, row in enumerate(offers[:CARD_SHOP_SIZE]):
        card = card_shop_offer_to_card(row)
        if card is None:
            continue
        col = idx % 2
        row_idx = idx // 2
        base_x = x_positions[col]
        base_y = y_positions[row_idx]
        box = (base_x, base_y, base_x + box_w, base_y + box_h)
        accent = element_color(card.attribute)
        purchased = bool(row["purchased"])
        price = card_shop_offer_price(row)
        draw.rounded_rectangle(box, 20, fill=palette["panel_fill"], outline=accent, width=3)

        badge = (base_x + 14, base_y + 14, base_x + 136, base_y + 54)
        draw.rounded_rectangle(badge, 14, fill=palette["card_fill"], outline=accent, width=2)
        draw.text(((badge[0] + badge[2]) / 2, (badge[1] + badge[3]) / 2 + 1), "SUPPORT CARD", font=FONT_XS_BOLD, fill=palette["title"], anchor="mm")

        if purchased:
            sold_badge = (base_x + box_w - 132, base_y + 14, base_x + box_w - 14, base_y + 54)
            draw.rounded_rectangle(sold_badge, 12, fill=(20, 20, 20), outline=(240, 210, 120), width=2)
            draw.text(((sold_badge[0] + sold_badge[2]) / 2, (sold_badge[1] + sold_badge[3]) / 2), "SOLD", font=FONT_S_BOLD, fill=palette["accent_dust"], anchor="mm")

        text_box = (base_x + 22, base_y + 68, base_x + box_w - 18, base_y + box_h - 18)
        title = ellipsize_text(draw, card.name, FONT_M_BOLD, text_box[2] - text_box[0])
        desc_lines = textwrap.wrap(card.description, width=42)[:3]
        info_lines = [title, support_card_compact_label(card), *desc_lines, "Purchased" if purchased else f"Price: {price} Card Dust"]
        fonts = [FONT_M_BOLD, FONT_XS_BOLD, *([FONT_XS] * len(desc_lines)), FONT_S_BOLD]
        fills = [palette["title"], accent, *([palette["info_text"]] * len(desc_lines)), palette["empty_text"] if purchased else palette["accent_dust"]]
        fitted_fonts, _ = _fit_text_block_fonts(draw, text_box, info_lines, fonts, line_gap=6, min_font_size=12)
        draw_evenly_spaced_text_block(draw, text_box, info_lines, fitted_fonts, fills, min_gap=5)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


@with_panel_user_context(lambda user_name, gold, offers, *args, **kwargs: int(offers[0]["user_id"]) if offers else _ACTIVE_PANEL_USER_ID.get())
def render_equip_shop_page(user_name: str, gold: int, offers: Sequence[sqlite3.Row], theme_name: str = "gray") -> io.BytesIO:
    width, height = 980, 900
    palette = get_ui_theme_palette(theme_name)
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    refresh_text = reset_schedule_text()
    draw.text((34, 28), f"{user_name}'s Equip Shop", font=FONT_XL, fill=palette["title"])
    draw.text((34, 76), f"6 daily offers • Prices range from {EQUIP_SHOP_MIN_COST}-{EQUIP_SHOP_MAX_COST} gold • Gold: {gold}", font=FONT_S, fill=palette["subtitle"])
    draw.text((34, 108), refresh_text, font=FONT_XS_BOLD, fill=palette["accent_dust"])
    outer = (24, 136, 956, 874)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, 34, outline=palette["outer_outline"], width=3)

    box_w, box_h = 430, 218
    x_positions = [48, 502]
    y_positions = [164, 396, 628]

    for idx, row in enumerate(offers[:EQUIP_SHOP_SIZE]):
        spell = equip_shop_offer_to_spell(row)
        if spell is None:
            continue
        col = idx % 2
        row_idx = idx // 2
        base_x = x_positions[col]
        base_y = y_positions[row_idx]
        box = (base_x, base_y, base_x + box_w, base_y + box_h)
        accent = palette["accent_support"]
        purchased = bool(row["purchased"])
        price = equip_shop_offer_price(row)
        draw.rounded_rectangle(box, 20, fill=palette["panel_fill"], outline=accent, width=3)

        badge = (base_x + 14, base_y + 14, base_x + 136, base_y + 54)
        draw.rounded_rectangle(badge, 14, fill=palette["card_fill"], outline=accent, width=2)
        draw.text(((badge[0] + badge[2]) / 2, (badge[1] + badge[3]) / 2 + 1), "EQUIP SPELL", font=FONT_XS_BOLD, fill=palette["title"], anchor="mm")

        if purchased:
            sold_badge = (base_x + box_w - 132, base_y + 14, base_x + box_w - 14, base_y + 54)
            draw.rounded_rectangle(sold_badge, 12, fill=(20, 20, 20), outline=(240, 210, 120), width=2)
            draw.text(((sold_badge[0] + sold_badge[2]) / 2, (sold_badge[1] + sold_badge[3]) / 2), "SOLD", font=FONT_S_BOLD, fill=palette["accent_dust"], anchor="mm")

        text_box = (base_x + 22, base_y + 68, base_x + box_w - 18, base_y + box_h - 18)
        title = ellipsize_text(draw, spell.name, FONT_M_BOLD, text_box[2] - text_box[0])
        desc_lines = textwrap.wrap(spell.description, width=42)[:4]
        info_lines = [title, *desc_lines, "Purchased" if purchased else f"Price: {price} Gold"]
        fonts = [FONT_M_BOLD, *([FONT_XS] * len(desc_lines)), FONT_S_BOLD]
        fills = [palette["title"], *([palette["info_text"]] * len(desc_lines)), palette["empty_text"] if purchased else palette["accent_dust"]]
        fitted_fonts, _ = _fit_text_block_fonts(draw, text_box, info_lines, fonts, line_gap=6, min_font_size=12)
        draw_evenly_spaced_text_block(draw, text_box, info_lines, fitted_fonts, fills, min_gap=5)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


@with_panel_user_context(lambda *args, **kwargs: _ACTIVE_PANEL_USER_ID.get())
def render_shop_hub_page(user_name: str, gold: int, card_dust: int, theme_name: str = "gray") -> io.BytesIO:
    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_ui_theme_palette(theme_name)

    draw.text((34, 28), f"{user_name}'s Shop", font=FONT_XL, fill=palette["title"])
    draw.text((34, 76), "Choose a shop below to open its separate page.", font=FONT_S, fill=palette["subtitle"])
    draw.text((34, 104), reset_schedule_text(), font=FONT_XS_BOLD, fill=palette["accent_dust"])

    outer = (24, 136, 936, 934)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    intro_box = (52, 164, 908, 314)
    draw.rounded_rectangle(intro_box, 24, fill=palette["panel_fill"], outline=palette["info_outline"], width=3)
    intro_title = "Shop Overview"
    intro_lines = [
        "• Monster Shop: buy one of 6 daily monsters using gold.",
        "• Card Shop: buy one of 6 daily maxed support cards using Card Dust.",
        "• Equip Shop: buy one of 6 daily equip spells using gold.",
        "• Customization: unlock Showcase and UI Theme for your profile.",
        "• Duel Partners: under construction.",
    ]
    intro_font = FONT_XS
    wrapped_intro_lines: List[str] = []
    intro_text_width = intro_box[2] - intro_box[0] - 48
    for line in intro_lines:
        wrapped_intro_lines.extend(fit_wrapped_text(draw, line, intro_font, intro_text_width, 2) or [line])
    title_bbox = draw.textbbox((0, 0), intro_title, font=FONT_L_BOLD)
    title_height = title_bbox[3] - title_bbox[1]
    line_heights = []
    for line in wrapped_intro_lines:
        bbox = draw.textbbox((0, 0), line, font=intro_font)
        line_heights.append(bbox[3] - bbox[1])
    intro_gap = 12
    intro_line_gap = 4
    intro_block_height = title_height + intro_gap + sum(line_heights) + intro_line_gap * max(0, len(line_heights) - 1)
    intro_top = intro_box[1] + max(12, ((intro_box[3] - intro_box[1]) - intro_block_height) // 2)
    draw.text((76, intro_top), intro_title, font=FONT_L_BOLD, fill=palette["title"])
    y = intro_top + title_height + intro_gap
    for line, line_height in zip(wrapped_intro_lines, line_heights):
        draw.text((76, y), line, font=intro_font, fill=palette["info_text"])
        y += line_height + intro_line_gap

    section_layout = [
        ("Monster Shop", 52, 348, 398, 114, "Open the monster shop to browse today's monsters.", palette["accent_binder"]),
        ("Card Shop", 510, 348, 398, 114, "Open the card shop to browse today's support cards.", palette["accent_support"]),
        ("Equip Shop", 52, 484, 398, 114, "Open the equip shop to browse today's equip spells.", palette["accent_support"]),
        ("Customization", 510, 484, 398, 114, "Unlock Showcase and UI Theme for your profile here.", palette["accent_support"]),
        ("Wallet", 52, 620, 398, 146, "Your currencies are shown here for quick reference.", palette["panel_outline"]),
        ("Duel Partners", 510, 620, 398, 146, "This section is under construction until the partner shop is added.", (255, 215, 90)),
    ]

    for label, left, top, box_w, box_h, desc, accent in section_layout:
        box = (left, top, left + box_w, top + box_h)
        draw.rounded_rectangle(box, 24, fill=palette["card_fill"], outline=accent, width=3)

        badge_box = (left + 16, top + 14, left + 150, top + 44)
        draw.rounded_rectangle(badge_box, 12, fill=palette["card_fill"], outline=accent, width=2)
        draw.text(((badge_box[0] + badge_box[2]) / 2, (badge_box[1] + badge_box[3]) / 2 + 1), label.upper(), font=FONT_XS_BOLD, fill=palette["title"], anchor="mm")

        body_box = (left + 20, top + 56, left + box_w - 20, top + box_h - 14)
        if label == "Wallet":
            info_lines = [f"{gold} gold", f"{card_dust} Card Dust", *wrap_text(draw, desc, FONT_S, body_box[2] - body_box[0])[:1]]
            fonts = [FONT_M_BOLD, FONT_S, FONT_S][:len(info_lines)]
            fills = [accent, palette["info_text"], palette["info_text"]][:len(info_lines)]
            fitted_fonts, _ = _fit_text_block_fonts(draw, body_box, info_lines, fonts, line_gap=6, min_font_size=12)
            draw_evenly_spaced_text_block(draw, body_box, info_lines, fitted_fonts, fills, min_gap=5)
            continue

        desc_lines = wrap_text(draw, desc, FONT_S, body_box[2] - body_box[0])[:2] or [desc]
        y = body_box[1] + 2
        for line in desc_lines:
            draw.text((body_box[0], y), line, font=FONT_S, fill=palette["info_text"])
            bbox = draw.textbbox((0, 0), line, font=FONT_S)
            y += (bbox[3] - bbox[1]) + 4

    footer_text = "Use the buttons below to switch between shops."
    footer_bbox = draw.textbbox((0, 0), footer_text, font=FONT_S)
    footer_text_height = footer_bbox[3] - footer_bbox[1]
    footer_pad_y = 14
    footer_top = 790
    footer_box = (52, footer_top, 908, footer_top + footer_text_height + footer_pad_y * 2)
    draw.rounded_rectangle(footer_box, 20, fill=palette["panel_fill"], outline=palette["outer_outline"], width=2)
    draw.text(
        ((footer_box[0] + footer_box[2]) / 2, (footer_box[1] + footer_box[3]) / 2),
        footer_text,
        font=FONT_S,
        fill=(220, 220, 224),
        anchor="mm",
    )

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


@with_panel_user_context(lambda *args, **kwargs: _ACTIVE_PANEL_USER_ID.get())
def render_duel_partners_shop_page(user_name: str, gold: int, card_dust: int, theme_name: str = "gray") -> io.BytesIO:
    width, height = 960, 720
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_ui_theme_palette(theme_name)

    draw.text((34, 28), f"{user_name}'s Duel Partners", font=FONT_XL, fill=palette["title"])
    draw.text((34, 76), "This shop tab is reserved for a future update.", font=FONT_S, fill=palette["subtitle"])
    draw.text((34, 104), reset_schedule_text(), font=FONT_XS_BOLD, fill=palette["accent_dust"])

    outer = (24, 136, 936, 694)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    card = (78, 206, 882, 624)
    accent = (255, 215, 90)
    draw.rounded_rectangle(card, 28, fill=palette["card_fill"], outline=accent, width=3)

    badge = (106, 230, 292, 268)
    draw.rounded_rectangle(badge, 12, fill=palette["card_fill"], outline=accent, width=2)
    draw.text(((badge[0] + badge[2]) / 2, (badge[1] + badge[3]) / 2 + 1), "COMING SOON", font=FONT_XS_BOLD, fill=palette["title"], anchor="mm")

    lines = [
        "Duel Partners",
        "Not live yet",
        "Use Back to return to the main shop hub.",
    ]
    fonts = [FONT_XL_BOLD, FONT_L_BOLD, FONT_S, FONT_S, FONT_S_BOLD]
    fills = [palette["title"], accent, palette["info_text"], palette["info_text"], palette["accent_dust"]]
    fitted_fonts, _ = _fit_text_block_fonts(draw, (118, 286, 842, 562), lines, fonts[:len(lines)], line_gap=16, min_font_size=14)
    draw_evenly_spaced_text_block(draw, (118, 286, 842, 562), lines, fitted_fonts, fills[:len(lines)], x_align="center", min_gap=12)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


def make_duel_partners_shop_file(user_name: str, user_row: sqlite3.Row) -> discord.File:
    with _PanelUserContext(int(user_row["user_id"])):
        return discord.File(render_duel_partners_shop_page(user_name, int(user_row["gold"]), 0, db.get_ui_theme(int(user_row["user_id"]))), filename="duel_partners_shop.png")


@with_panel_user_context(lambda user_name, user_row, *args, **kwargs: int(user_row["user_id"]))
def render_market_hub_page(user_name: str, user_row: sqlite3.Row, theme_name: str = "gray") -> io.BytesIO:
    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_ui_theme_palette(theme_name)
    active_rows = db.list_user_market_listings(int(user_row["user_id"]), status='active')
    active_count = len(active_rows)

    draw.text((34, 28), f"{user_name}'s Market", font=FONT_XL, fill=palette["title"])
    draw.text((34, 76), "Browse listings, post your own items, or manage your active sales.", font=FONT_S, fill=palette["subtitle"])
    draw.text((34, 104), f"Trade is currently disabled. Active listings: {active_count}", font=FONT_XS_BOLD, fill=palette["accent_dust"])

    outer = (24, 136, 936, 934)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    intro_box = (52, 164, 908, 314)
    draw.rounded_rectangle(intro_box, 24, fill=palette["panel_fill"], outline=palette["info_outline"], width=3)
    intro_title = "Market Overview"
    intro_lines = [
        "• Buy: browse player listings by category, page, or item search.",
        "• Sell: post tickets, monsters, support cards, and equip spells for gold.",
        "• Listings: review your active sales and remove a listing to get the item back.",
        "• Trade: under construction.",
    ]
    intro_font = FONT_XS
    wrapped_intro_lines: List[str] = []
    intro_text_width = intro_box[2] - intro_box[0] - 48
    for line in intro_lines:
        wrapped_intro_lines.extend(fit_wrapped_text(draw, line, intro_font, intro_text_width, 2) or [line])
    title_bbox = draw.textbbox((0, 0), intro_title, font=FONT_L_BOLD)
    title_height = title_bbox[3] - title_bbox[1]
    line_heights = []
    for line in wrapped_intro_lines:
        bbox = draw.textbbox((0, 0), line, font=intro_font)
        line_heights.append(bbox[3] - bbox[1])
    intro_gap = 12
    intro_line_gap = 4
    intro_block_height = title_height + intro_gap + sum(line_heights) + intro_line_gap * max(0, len(line_heights) - 1)
    intro_top = intro_box[1] + max(12, ((intro_box[3] - intro_box[1]) - intro_block_height) // 2)
    draw.text((76, intro_top), intro_title, font=FONT_L_BOLD, fill=palette["title"])
    y = intro_top + title_height + intro_gap
    for line, line_height in zip(wrapped_intro_lines, line_heights):
        draw.text((76, y), line, font=intro_font, fill=palette["info_text"])
        y += line_height + intro_line_gap

    section_layout = [
        ("Buy", 52, 348, 398, 114, "Open the player marketplace and browse listings by category.", palette["accent_binder"]),
        ("Sell", 510, 348, 398, 114, "Create a new listing from tickets, monsters, support cards, or equip spells.", palette["accent_support"]),
        ("Listings", 52, 484, 398, 114, "Review your active listings and remove one to receive the item back.", palette["accent_team"]),
        ("Trade", 510, 484, 398, 114, "This section is under construction until direct trading is added.", (255, 215, 90)),
        ("Wallet", 52, 620, 398, 146, "Your current gold balance is shown here for quick reference.", palette["panel_outline"]),
        ("Active Sales", 510, 620, 398, 146, "Track how many listings you currently have posted on the market.", palette["outer_outline"]),
    ]

    for label, left, top, box_w, box_h, desc, accent in section_layout:
        box = (left, top, left + box_w, top + box_h)
        draw.rounded_rectangle(box, 24, fill=palette["card_fill"], outline=accent, width=3)

        badge_box = (left + 16, top + 14, left + 160, top + 44)
        draw.rounded_rectangle(badge_box, 12, fill=palette["card_fill"], outline=accent, width=2)
        draw.text(((badge_box[0] + badge_box[2]) / 2, (badge_box[1] + badge_box[3]) / 2 + 1), label.upper(), font=FONT_XS_BOLD, fill=palette["title"], anchor="mm")

        body_box = (left + 20, top + 56, left + box_w - 20, top + box_h - 14)
        if label == "Wallet":
            info_lines = [f"{int(user_row['gold'] or 0):,} gold", *wrap_text(draw, desc, FONT_S, body_box[2] - body_box[0])[:2]]
            fonts = [FONT_M_BOLD, FONT_S, FONT_S][:len(info_lines)]
            fills = [accent, palette["info_text"], palette["info_text"]][:len(info_lines)]
        elif label == "Active Sales":
            info_lines = [f"{active_count} live listing{'s' if active_count != 1 else ''}", *wrap_text(draw, desc, FONT_S, body_box[2] - body_box[0])[:2]]
            fonts = [FONT_M_BOLD, FONT_S, FONT_S][:len(info_lines)]
            fills = [accent, palette["info_text"], palette["info_text"]][:len(info_lines)]
        else:
            info_lines = wrap_text(draw, desc, FONT_S, body_box[2] - body_box[0])[:3] or [desc]
            fonts = [FONT_S] * len(info_lines)
            fills = [palette["info_text"]] * len(info_lines)

        line_gap = 4
        metrics = []
        total_height = 0
        for line, font in zip(info_lines, fonts):
            bbox = draw.textbbox((0, 0), str(line), font=font)
            height = bbox[3] - bbox[1]
            metrics.append(height)
            total_height += height
        available_height = max(1, body_box[3] - body_box[1])
        block_height = total_height + line_gap * max(0, len(info_lines) - 1)
        y = body_box[1] + max(0, (available_height - block_height) / 2)
        for line, font, fill, height in zip(info_lines, fonts, fills, metrics):
            draw.text((body_box[0], y), str(line), font=font, fill=fill)
            y += height + line_gap

    footer_text = "Use the market hub buttons below to open Buy, Sell, Listings, or Trade."
    footer_bbox = draw.textbbox((0, 0), footer_text, font=FONT_S)
    footer_text_height = footer_bbox[3] - footer_bbox[1]
    footer_pad_y = 14
    footer_top = 790
    footer_box = (52, footer_top, 908, footer_top + footer_text_height + footer_pad_y * 2)
    draw.rounded_rectangle(footer_box, 20, fill=palette["panel_fill"], outline=palette["outer_outline"], width=2)
    draw.text(((footer_box[0] + footer_box[2]) / 2, (footer_box[1] + footer_box[3]) / 2), footer_text, font=FONT_S, fill=(220, 220, 224), anchor="mm")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


def make_market_hub_file(user_name: str, user_row: sqlite3.Row) -> discord.File:
    return discord.File(render_market_hub_page(user_name, user_row, db.get_ui_theme(int(user_row["user_id"]))), filename="market_hub.png")


def make_equip_shop_file(user_name: str, gold: int, offers: Sequence[sqlite3.Row]) -> discord.File:
    return discord.File(render_equip_shop_page(user_name, gold, offers, db.get_ui_theme(int(offers[0]["user_id"])) if offers else "gray"), filename="equip_shop.png")


def make_monster_shop_file(user_name: str, gold: int, offers: Sequence[sqlite3.Row]) -> discord.File:
    return discord.File(render_monster_shop_page(user_name, gold, offers, db.get_ui_theme(int(offers[0]["user_id"])) if offers else "gray"), filename="monster_shop.png")


def make_card_shop_file(user_name: str, gold: int, offers: Sequence[sqlite3.Row]) -> discord.File:
    return discord.File(render_card_shop_page(user_name, gold, offers, db.get_ui_theme(int(offers[0]["user_id"])) if offers else "gray"), filename="card_shop.png")


def render_customization_shop_page(user_name: str, user_row: sqlite3.Row, theme_name: str = "gray") -> io.BytesIO:
    width, height = 980, 900
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_ui_theme_palette(theme_name)
    owner_id = int(user_row["user_id"])
    showcase_unlocked = bool(int(user_row["showcase_unlocked"] or 0))
    background_unlocked = bool(int((user_row["background_unlocked"] if "background_unlocked" in user_row.keys() else 0) or 0))
    theme_unlocked = bool(int(user_row["ui_theme_unlocked"] or 0))
    gold = int(user_row["gold"] or 0)

    draw.text((34, 28), f"{user_name}'s Customization Shop", font=FONT_XL, fill=palette["title"])
    draw.text((34, 76), "Profile upgrades • Each upgrade costs 5000 gold • Gold: {gold}".format(gold=gold), font=FONT_S, fill=palette["subtitle"])
    draw.text((34, 108), reset_schedule_text(), font=FONT_XS_BOLD, fill=palette["accent_dust"])
    outer = (24, 136, 956, 874)
    paste_binder_panel(img, outer, radius=34, user_id=owner_id)
    draw.rounded_rectangle(outer, 34, outline=palette["outer_outline"], width=3)

    box_w, box_h = 430, 218
    x_positions = [48, 502]
    y_positions = [164, 396, 628]
    upgrades = [
        ("Showcase", showcase_unlocked, "Adds the Showcase upload button inside Customize.", "SHOWCASE"),
        ("Background", background_unlocked, "Adds the Background upload button inside Customize.", "BACKGROUND"),
        ("UI Theme", theme_unlocked, "Adds the UI Theme selector inside Customize.", "UI THEME"),
    ]

    for idx, (label, unlocked, desc, badge_text) in enumerate(upgrades):
        col = idx % 2
        row_idx = idx // 2
        base_x = x_positions[col]
        base_y = y_positions[row_idx]
        box = (base_x, base_y, base_x + box_w, base_y + box_h)
        accent = palette["accent_support"] if unlocked else palette["accent_binder"]
        draw.rounded_rectangle(box, 20, fill=palette["panel_fill"], outline=accent, width=3)

        badge = (base_x + 14, base_y + 14, base_x + 136, base_y + 54)
        draw.rounded_rectangle(badge, 14, fill=palette["card_fill"], outline=accent, width=2)
        draw.text(((badge[0] + badge[2]) / 2, (badge[1] + badge[3]) / 2 + 1), badge_text, font=FONT_XS_BOLD, fill=palette["title"], anchor="mm")

        if unlocked:
            owned_badge = (base_x + box_w - 132, base_y + 14, base_x + box_w - 14, base_y + 54)
            draw.rounded_rectangle(owned_badge, 12, fill=(20, 20, 20), outline=(90, 180, 110), width=2)
            draw.text(((owned_badge[0] + owned_badge[2]) / 2, (owned_badge[1] + owned_badge[3]) / 2), "OWNED", font=FONT_S_BOLD, fill=(90, 180, 110), anchor="mm")

        text_box = (base_x + 22, base_y + 68, base_x + box_w - 18, base_y + box_h - 18)
        title = ellipsize_text(draw, label, FONT_M_BOLD, text_box[2] - text_box[0])
        desc_lines = textwrap.wrap(desc, width=42)[:3]
        info_lines = [title, *desc_lines, "Owned" if unlocked else "Price: 5000 Gold"]
        fonts = [FONT_M_BOLD, *([FONT_XS] * len(desc_lines)), FONT_S_BOLD]
        fills = [palette["title"], *([palette["info_text"]] * len(desc_lines)), (90, 180, 110) if unlocked else palette["accent_dust"]]
        fitted_fonts, _ = _fit_text_block_fonts(draw, text_box, info_lines, fonts, line_gap=6, min_font_size=12)
        draw_evenly_spaced_text_block(draw, text_box, info_lines, fitted_fonts, fills, min_gap=5)

    footer_box = (502, 396, 932, 614)
    draw.rounded_rectangle(footer_box, 20, fill=palette["panel_fill"], outline=palette["panel_outline"], width=3)
    footer_text_box = (footer_box[0] + 22, footer_box[1] + 24, footer_box[2] - 18, footer_box[3] - 22)
    footer_lines = [
        "How it works",
        *textwrap.wrap("Use the dropdown below to buy upgrades. Unlocked options appear the next time you open Customize on your profile.", width=42)[:4],
    ]
    footer_fonts = [FONT_M_BOLD, *([FONT_XS] * (len(footer_lines) - 1))]
    footer_fills = [palette["title"], *([palette["info_text"]] * (len(footer_lines) - 1))]
    fitted_footer_fonts, _ = _fit_text_block_fonts(draw, footer_text_box, footer_lines, footer_fonts, line_gap=6, min_font_size=12)
    draw_evenly_spaced_text_block(draw, footer_text_box, footer_lines, fitted_footer_fonts, footer_fills, min_gap=6)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


def make_customization_shop_file(user_name: str, user_row: sqlite3.Row) -> discord.File:
    return discord.File(render_customization_shop_page(user_name, user_row, db.get_ui_theme(int(user_row["user_id"]))), filename="customization_shop.png")
