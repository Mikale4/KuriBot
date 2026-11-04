"""Extra Deck menus."""

from __future__ import annotations

import asyncio
import io
import math
import sqlite3
from typing import List, Optional, Tuple

import discord
from PIL import Image, ImageDraw
from discord.ext import commands

from kuribot.config import (
    ATTRIBUTES, EXTRA_DECK_MIN_SIZE, EXTRA_DECK_SIZE, PREFIX, SUPPORT_CARD_IMAGE_PATH,
)
from kuribot.database import db
from kuribot.render.fonts import FONT_M_BOLD, FONT_S, FONT_S_BOLD, FONT_XL, FONT_XS
from kuribot.render.themes import get_ui_theme_palette
from kuribot.render.images import (
    _PanelUserContext, apply_rounded_mask, open_binder_canvas_background, open_card_image_contain,
    paste_binder_panel,
)
from kuribot.render.text import ellipsize_text, fit_wrapped_text
from kuribot.monsters import (
    decode_extra_deck_monsters, display_monster_label_from_id, extra_deck_effect_description,
    is_extra_deck_monster_row, resolve_row_combat_stats,
)
from kuribot.ui.common import (
    safe_acknowledge_interaction, safe_edit_interaction_message, safe_send_interaction_message,
)


def extra_deck_summary_lines(user_id: int, deck: sqlite3.Row) -> List[str]:
    lines: List[str] = []
    for idx, instance_id in enumerate(decode_extra_deck_monsters(str(deck["monsters_json"])), start=1):
        row = db.get_inventory_instance(instance_id, user_id)
        if row is None:
            lines.append(f"{idx}. [Missing monster]")
            continue
        lines.append(f"{idx}. **{row['monster_name']}** ({row['attribute']}) — {extra_deck_effect_description(str(row['monster_name']))}")
    return lines


async def send_extra_deck_help(ctx: commands.Context) -> None:
    prefix = PREFIX
    await ctx.send(
        "**Extra Deck commands**\n"
        f"`{prefix}extradeck list` — show your Extra Decks\n"
        f"`{prefix}extradeck create <name> | <monster name/id>, <monster name/id>...` — create an Extra Deck, max {EXTRA_DECK_SIZE}\n"
        f"`{prefix}extradeck equip <team id> <extra deck id|none>` — equip an Extra Deck to a team\n"
        f"`{prefix}extradeck delete <extra deck id>` — delete an Extra Deck\n\n"
        "Extra Deck Monsters cannot be placed in normal teams, cannot use equip spells, and ignore all support card effects."
    )


def find_owned_extra_deck_monster_instance(user_id: int, token: str) -> Optional[str]:
    query = str(token or "").strip()
    if not query:
        return None
    row = db.get_inventory_instance(query, user_id)
    if row is not None and is_extra_deck_monster_row(row):
        return query
    lowered = query.casefold()
    matches = [row for row in db.user_inventory(user_id) if is_extra_deck_monster_row(row) and lowered in str(row["monster_name"]).casefold()]
    if len(matches) == 1:
        return str(matches[0]["instance_id"])
    exact = [row for row in matches if str(row["monster_name"]).casefold() == lowered]
    if exact:
        return str(exact[0]["instance_id"])
    return None


def render_extra_deck_page(user_name: str, deck_rows: List[dict], page: int, per_page: int = 8, filter_label: str = "All Extra Decks", sort_label: str = "Name A-Z", theme_name: str = "gray") -> io.BytesIO:
    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_ui_theme_palette(theme_name)

    title = f"{user_name}'s Extra Decks"
    draw.text((34, 28), title, font=FONT_XL, fill=palette["title"])
    total_pages = max(1, math.ceil(max(1, len(deck_rows)) / per_page))
    page = max(1, min(int(page or 1), total_pages))
    draw.text((34, 76), f"Filter: {filter_label} • Sort: {sort_label} • Page {page}/{total_pages}", font=FONT_S, fill=palette["subtitle"])

    outer = (24, 112, 936, 934)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    start = (page - 1) * per_page
    items = deck_rows[start:start + per_page]
    x_positions = [68, 506]
    y_positions = [150, 338, 526, 714]
    art_size = (102, 126)
    accent = (190, 132, 255)

    for idx, row in enumerate(items):
        col = idx % 2
        row_idx = idx // 2
        base_x = x_positions[col]
        base_y = y_positions[row_idx]
        box = (base_x, base_y, base_x + 360, base_y + 150)
        outline = accent
        draw.rounded_rectangle(box, 18, fill=palette["card_fill"], outline=outline, width=3)

        monster_rows = row.get("monster_rows") or []
        first_monster = monster_rows[0] if monster_rows else None
        image_path = str(first_monster["image_path"]) if first_monster is not None else str(SUPPORT_CARD_IMAGE_PATH)
        art = open_card_image_contain(image_path, art_size, radius=18, background=palette["card_fill"] + (255,))
        overlay = Image.new("RGBA", art_size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle((0, 0, art_size[0] - 1, art_size[1] - 1), 16, outline=outline + (255,), width=3)
        art = apply_rounded_mask(Image.alpha_composite(art, overlay), 18)
        img.paste(art, (base_x + 12, base_y + 12), art)

        text_x = base_x + 126
        text_width = box[2] - text_x - 14
        header = str(row.get("name") or "Extra Deck")
        header_lines = fit_wrapped_text(draw, header, FONT_M_BOLD, text_width, 2)
        for i, line in enumerate(header_lines):
            draw.text((text_x, base_y + 12 + i * 22), line, font=FONT_M_BOLD, fill=palette["title"])

        status_y = base_y + 12 + max(1, len(header_lines)) * 22 + 2
        status = "Equipable Extra Deck"
        draw.text((text_x, status_y), ellipsize_text(draw, status, FONT_S_BOLD, text_width), font=FONT_S_BOLD, fill=outline)
        draw.text((text_x, status_y + 22), f"Monsters: {row.get('monster_count', 0)}/{EXTRA_DECK_SIZE}", font=FONT_S, fill=(214, 220, 242))

        preview_names = row.get("preview_names") or []
        preview_text = ", ".join(preview_names[:3]) if preview_names else "No preview"
        preview_top = status_y + 44
        preview_lines = fit_wrapped_text(draw, f"Preview: {preview_text}", FONT_XS, text_width, 3)
        for i, line in enumerate(preview_lines):
            draw.text((text_x, preview_top + i * 16), line, font=FONT_XS, fill=(204, 204, 210))

        draw.text((text_x, base_y + 130), ellipsize_text(draw, "Rule: Extra Deck Monsters only", FONT_XS, text_width), font=FONT_XS, fill=(168, 168, 176))

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


def build_extra_deck_detail_embed(row: dict) -> discord.Embed:
    title = f"#{row.get('deck_id')} — {row.get('name')}"
    embed = discord.Embed(title=title, color=discord.Color.purple())
    embed.add_field(name="Status", value="Equipable", inline=True)
    embed.add_field(name="Monsters", value=f"{row.get('monster_count', 0)}/{EXTRA_DECK_SIZE}", inline=True)
    lines = []
    for idx, monster in enumerate(row.get("monster_rows") or [], start=1):
        monster_name = str(monster["monster_name"])
        lines.append(f"{idx}. **{monster_name}** ({monster['attribute']}) — {extra_deck_effect_description(monster_name)}")
    embed.description = "\n".join(lines) if lines else "Empty Extra Deck."
    return embed


class ExtraDeckItemSelect(discord.ui.Select):
    def __init__(self, browser: "ExtraDeckBrowserView"):
        self.browser = browser
        options: List[discord.SelectOption] = []
        for row in browser.page_rows():
            preview = ", ".join(row.get("preview_names") or []) or "No preview"
            label = str(row["name"])[:100]
            description = f"#{row['deck_id']} • {row['monster_count']}/{EXTRA_DECK_SIZE} • {preview}"[:100]
            options.append(discord.SelectOption(label=label, value=str(row["deck_id"]), description=description, default=(str(row["deck_id"]) == str(browser.selected_deck_id))))
        if not options:
            options = [discord.SelectOption(label="No Extra Decks on this page", value="__none__")]
        super().__init__(placeholder="Open an Extra Deck from this page", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "__none__":
            await safe_acknowledge_interaction(interaction)
            return
        self.browser.selected_deck_id = self.values[0]
        row = self.browser.get_selected_row()
        if row is None:
            await interaction.response.send_message("That Extra Deck is no longer available.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_extra_deck_detail_embed(row), ephemeral=True)


class ExtraDeckFilterSelect(discord.ui.Select):
    def __init__(self, browser: "ExtraDeckBrowserView"):
        self.browser = browser
        options = [discord.SelectOption(label=label[:100], value=key, default=(key == browser.filter_key)) for key, label in browser.filter_options()]
        super().__init__(placeholder="Filter", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.browser.filter_key = self.values[0]
        self.browser.page = 1
        self.browser.rebuild()
        await self.browser.refresh(interaction)


class ExtraDeckSortSelect(discord.ui.Select):
    def __init__(self, browser: "ExtraDeckBrowserView"):
        self.browser = browser
        options = [discord.SelectOption(label=label[:100], value=key, default=(key == browser.sort_key)) for key, label in browser.sort_options()]
        super().__init__(placeholder="Order", min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        self.browser.sort_key = self.values[0]
        self.browser.page = 1
        self.browser.rebuild()
        await self.browser.refresh(interaction)


class ExtraDeckBrowserView(discord.ui.View):
    PER_PAGE = 8

    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.user_name = user_name
        self.page = 1
        self.filter_key = "all"
        self.sort_key = "name_asc"
        self.filtered_rows: List[dict] = []
        self.selected_deck_id: Optional[str] = None
        self.message: Optional[discord.Message] = None
        self.rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This binder belongs to someone else.", ephemeral=True)
            return False
        return True

    def all_rows(self) -> List[dict]:
        rows: List[dict] = []
        for deck in db.list_extra_decks(self.owner_id):
            monster_ids = decode_extra_deck_monsters(str(deck["monsters_json"]))
            monster_rows = []
            for instance_id in monster_ids:
                monster = db.get_inventory_instance(instance_id, self.owner_id)
                if monster is not None:
                    monster_rows.append(monster)
            rows.append({
                "deck_id": int(deck["id"]),
                "instance_id": str(deck["id"]),
                "name": str(deck["name"]),
                "monster_count": len(monster_ids),
                "monster_rows": monster_rows,
                "preview_names": [str(monster["monster_name"]) for monster in monster_rows[:3]],
                "is_active": False,
            })
        return rows

    def filter_options(self) -> List[Tuple[str, str]]:
        return [("all", "All Extra Decks"), ("full", "Full Decks"), ("partial", "Partial Decks")]

    def sort_options(self) -> List[Tuple[str, str]]:
        return [("name_asc", "Name A-Z"), ("name_desc", "Name Z-A"), ("deck_id_desc", "Newest First"), ("deck_id_asc", "Oldest First")]

    def apply_filter(self, rows: List[dict]) -> List[dict]:
        if self.filter_key == "full":
            return [row for row in rows if int(row.get("monster_count") or 0) >= EXTRA_DECK_SIZE]
        if self.filter_key == "partial":
            return [row for row in rows if int(row.get("monster_count") or 0) < EXTRA_DECK_SIZE]
        return list(rows)

    def apply_sort(self, rows: List[dict]) -> List[dict]:
        if self.sort_key == "name_desc":
            return sorted(rows, key=lambda row: (str(row["name"]).lower(), int(row["deck_id"])), reverse=True)
        if self.sort_key == "deck_id_desc":
            return sorted(rows, key=lambda row: int(row["deck_id"]), reverse=True)
        if self.sort_key == "deck_id_asc":
            return sorted(rows, key=lambda row: int(row["deck_id"]))
        return sorted(rows, key=lambda row: (str(row["name"]).lower(), int(row["deck_id"])))

    def total_pages(self) -> int:
        return max(1, math.ceil(max(1, len(self.filtered_rows)) / self.PER_PAGE))

    def page_rows(self) -> List[dict]:
        start = (self.page - 1) * self.PER_PAGE
        return self.filtered_rows[start:start + self.PER_PAGE]

    def get_selected_row(self) -> Optional[dict]:
        if not self.selected_deck_id:
            return None
        return next((row for row in self.filtered_rows if str(row["deck_id"]) == str(self.selected_deck_id)), None)

    def current_filter_label(self) -> str:
        return dict(self.filter_options()).get(self.filter_key, "All Extra Decks")

    def current_sort_label(self) -> str:
        return dict(self.sort_options()).get(self.sort_key, "Name A-Z")

    def rebuild(self, *, reload: bool = True):
        # Page flips pass reload=False: the rows are already loaded, filtered and sorted, so
        # only the page changes. Filters, sorting, searches and edits reload everything.
        if reload or not getattr(self, "_rows_loaded", False):
            self.filtered_rows = self.apply_sort(self.apply_filter(self.all_rows()))
            self._rows_loaded = True
            self._summary_counts = None
        self.page = max(1, min(self.page, self.total_pages()))
        self.clear_items()
        self.prev_btn.disabled = self.page <= 1
        self.next_btn.disabled = self.page >= self.total_pages()
        self.add_item(ExtraDeckItemSelect(self))
        self.add_item(ExtraDeckFilterSelect(self))
        self.add_item(ExtraDeckSortSelect(self))
        self.add_item(self.manage_btn)
        self.add_item(self.back_btn)
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        if self.selected_deck_id and self.get_selected_row() is None:
            page_rows = self.page_rows()
            self.selected_deck_id = str(page_rows[0]["deck_id"]) if page_rows else None
        elif not self.selected_deck_id:
            page_rows = self.page_rows()
            self.selected_deck_id = str(page_rows[0]["deck_id"]) if page_rows else None

    def summary_text(self) -> str:
        if getattr(self, "_summary_counts", None) is None:
            self._summary_counts = (
                len(db.list_extra_decks(self.owner_id)),
                sum(1 for row in db.user_inventory(self.owner_id) if is_extra_deck_monster_row(row)),
            )
        total_owned, owned_monsters = self._summary_counts
        visible_start = 0 if not self.filtered_rows else (self.page - 1) * self.PER_PAGE + 1
        visible_end = min(len(self.filtered_rows), self.page * self.PER_PAGE)
        return f"Showing {visible_start}-{visible_end} of {len(self.filtered_rows)} filtered Extra Decks • {total_owned} total Extra Decks • {owned_monsters} Extra Deck Monster(s) owned"

    def make_file(self) -> discord.File:
        with _PanelUserContext(self.owner_id):
            buffer = render_extra_deck_page(self.user_name, self.filtered_rows, self.page, self.PER_PAGE, self.current_filter_label(), self.current_sort_label(), db.get_ui_theme(self.owner_id))
            return discord.File(buffer, filename="extra_decks.png")

    async def refresh(self, interaction: discord.Interaction):
        await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments_factory=lambda: [self.make_file()], view=self)

    async def refresh_message(self, message: Optional[discord.Message]):
        self.rebuild()
        if message is not None:
            await message.edit(content=self.summary_text(), attachments=[await asyncio.to_thread(self.make_file)], view=self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.primary, row=4)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.ui.binder import binder_hub_summary_text, BinderHubView
        view = BinderHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=binder_hub_summary_text(self.owner_id), attachments_factory=lambda: [view.make_file()], view=view)

    @discord.ui.button(label="Manage", style=discord.ButtonStyle.success, row=4)
    async def manage_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.message = interaction.message
        await safe_send_interaction_message(interaction, content="Manage Extra Decks:", view=ExtraDeckManageView(self, interaction.message), ephemeral=True)

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=4)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(1, self.page - 1)
        self.rebuild(reload=False)
        await self.refresh(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=4)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.total_pages(), self.page + 1)
        self.rebuild(reload=False)
        await self.refresh(interaction)


class ExtraDeckBuilderMonsterSelect(discord.ui.Select):
    def __init__(self, builder: "ExtraDeckBuilderView"):
        self.builder = builder
        rows = builder.page_rows()
        options: List[discord.SelectOption] = []
        for row in rows:
            instance_id = str(row["instance_id"])
            hp, atk, defense, spd = resolve_row_combat_stats(row)
            description = f"{row['attribute']} • ATK {atk} • {extra_deck_effect_description(str(row['monster_name']))}"[:100]
            options.append(discord.SelectOption(
                label=str(row["monster_name"])[:100],
                value=instance_id,
                description=description,
                default=False,
            ))
        if not options:
            options = [discord.SelectOption(label="No valid Extra Deck Monsters", value="__none__")]
        max_values = 1 if options[0].value == "__none__" else min(len(options), max(1, EXTRA_DECK_SIZE - len(builder.picks)))
        super().__init__(placeholder="Choose Extra Deck Monsters to add", min_values=1, max_values=max_values, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "__none__":
            await safe_acknowledge_interaction(interaction)
            return
        added: List[str] = []
        skipped: List[str] = []
        for instance_id in self.values:
            if instance_id in self.builder.picks:
                skipped.append(display_monster_label_from_id(instance_id))
                continue
            if len(self.builder.picks) >= EXTRA_DECK_SIZE:
                break
            self.builder.picks.append(instance_id)
            added.append(display_monster_label_from_id(instance_id))
        self.builder.rebuild()
        parts = []
        if added:
            parts.append("Added " + ", ".join(f"**{name}**" for name in added[:5]))
        if skipped:
            parts.append("Already selected: " + ", ".join(f"**{name}**" for name in skipped[:5]))
        await safe_edit_interaction_message(interaction, content=self.builder.status_text(" • ".join(parts) if parts else "No changes."), view=self.builder)


class ExtraDeckBuilderFilterSelect(discord.ui.Select):
    def __init__(self, builder: "ExtraDeckBuilderView"):
        self.builder = builder
        options = [
            discord.SelectOption(label="All Extra Deck Monsters", value="all", default=builder.filter_key == "all"),
            discord.SelectOption(label="Unselected", value="unselected", default=builder.filter_key == "unselected"),
            discord.SelectOption(label="Selected", value="selected", default=builder.filter_key == "selected"),
            *[discord.SelectOption(label=attribute.title(), value=f"attr:{attribute}", default=builder.filter_key == f"attr:{attribute}") for attribute in ATTRIBUTES],
        ]
        super().__init__(placeholder="Filter valid monsters", min_values=1, max_values=1, options=options[:25], row=1)

    async def callback(self, interaction: discord.Interaction):
        self.builder.filter_key = self.values[0]
        self.builder.page = 0
        self.builder.rebuild()
        await safe_edit_interaction_message(interaction, content=self.builder.status_text(f"Filter: **{self.builder.filter_label()}**."), view=self.builder)


class ExtraDeckBuilderSortSelect(discord.ui.Select):
    def __init__(self, builder: "ExtraDeckBuilderView"):
        self.builder = builder
        options = [
            discord.SelectOption(label="A-Z", value="name_asc", default=builder.sort_key == "name_asc"),
            discord.SelectOption(label="Z-A", value="name_desc", default=builder.sort_key == "name_desc"),
            discord.SelectOption(label="ATK", value="atk", default=builder.sort_key == "atk"),
            discord.SelectOption(label="HP", value="hp", default=builder.sort_key == "hp"),
            discord.SelectOption(label="DEF", value="def", default=builder.sort_key == "def"),
            discord.SelectOption(label="SPD", value="spd", default=builder.sort_key == "spd"),
        ]
        super().__init__(placeholder="Sort valid monsters", min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        self.builder.sort_key = self.values[0]
        self.builder.page = 0
        self.builder.rebuild()
        await safe_edit_interaction_message(interaction, content=self.builder.status_text(f"Sorting by **{self.builder.sort_key.upper()}**."), view=self.builder)


class ExtraDeckBuilderView(discord.ui.View):
    PER_PAGE = 10

    def __init__(self, browser: ExtraDeckBrowserView, browser_message: Optional[discord.Message], deck_name: str):
        super().__init__(timeout=600)
        self.browser = browser
        self.browser_message = browser_message
        self.user_id = browser.owner_id
        self.deck_name = str(deck_name or "").strip()
        self.inventory_rows = [row for row in db.user_inventory(self.user_id) if is_extra_deck_monster_row(row)]
        self.picks: List[str] = []
        self.filter_key = "unselected"
        self.sort_key = "name_asc"
        self.page = 0
        self.message: Optional[discord.Message] = None
        self.rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This Extra Deck builder belongs to someone else.", ephemeral=True)
            return False
        return True

    def filter_label(self) -> str:
        if self.filter_key == "unselected":
            return "Unselected"
        if self.filter_key == "selected":
            return "Selected"
        if self.filter_key.startswith("attr:"):
            return self.filter_key.split(":", 1)[1].title()
        return "All Extra Deck Monsters"

    def apply_filters(self, rows: List[sqlite3.Row]) -> List[sqlite3.Row]:
        filtered = list(rows)
        if self.filter_key == "unselected":
            filtered = [row for row in filtered if str(row["instance_id"]) not in self.picks]
        elif self.filter_key == "selected":
            filtered = [row for row in filtered if str(row["instance_id"]) in self.picks]
        elif self.filter_key.startswith("attr:"):
            attr = self.filter_key.split(":", 1)[1]
            filtered = [row for row in filtered if str(row["attribute"]) == attr]
        return filtered

    def sorted_rows(self) -> List[sqlite3.Row]:
        rows = self.apply_filters(self.inventory_rows)
        if self.sort_key == "name_desc":
            return sorted(rows, key=lambda row: (str(row["monster_name"]).lower(), str(row["instance_id"])), reverse=True)
        if self.sort_key == "name_asc":
            return sorted(rows, key=lambda row: (str(row["monster_name"]).lower(), str(row["instance_id"])))
        stat_index = {"hp": 0, "atk": 1, "def": 2, "spd": 3}.get(self.sort_key, 1)
        return sorted(rows, key=lambda row: (-resolve_row_combat_stats(row)[stat_index], str(row["monster_name"]).lower(), str(row["instance_id"])))

    def page_rows(self) -> List[sqlite3.Row]:
        start = self.page * self.PER_PAGE
        return self.sorted_rows()[start:start + self.PER_PAGE]

    def selected_text(self) -> str:
        return ", ".join(display_monster_label_from_id(instance_id) for instance_id in self.picks) if self.picks else "None yet"

    def status_text(self, extra: str = "") -> str:
        total_rows = len(self.sorted_rows())
        total_pages = max(1, math.ceil(max(1, total_rows) / self.PER_PAGE))
        base = (
            f"Extra Deck **{self.deck_name}** picks ({len(self.picks)}/{EXTRA_DECK_SIZE}): {self.selected_text()}\n"
            f"Filter: **{self.filter_label()}** • Sort: **{self.sort_key.upper()}** • Page **{self.page + 1}/{total_pages}**\n"
            "Choose monsters from the dropdown, then press **Save Extra Deck**."
        )
        if extra:
            return base + f"\n{extra}"
        return base

    def rebuild(self):
        self.clear_items()
        total_rows = len(self.sorted_rows())
        max_page = max(0, math.ceil(max(1, total_rows) / self.PER_PAGE) - 1)
        self.page = max(0, min(self.page, max_page))
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= max_page
        self.save_btn.disabled = len(self.picks) < EXTRA_DECK_MIN_SIZE
        self.clear_btn.disabled = not self.picks
        self.add_item(ExtraDeckBuilderMonsterSelect(self))
        self.add_item(ExtraDeckBuilderFilterSelect(self))
        self.add_item(ExtraDeckBuilderSortSelect(self))
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        self.add_item(self.clear_btn)
        self.add_item(self.save_btn)

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=4)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.status_text(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=4)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_page = max(0, math.ceil(max(1, len(self.sorted_rows())) / self.PER_PAGE) - 1)
        self.page = min(max_page, self.page + 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.status_text(), view=self)

    @discord.ui.button(label="Clear", style=discord.ButtonStyle.danger, row=4)
    async def clear_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.picks = []
        self.page = 0
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.status_text("Selection cleared."), view=self)

    @discord.ui.button(label="Save Extra Deck", style=discord.ButtonStyle.success, row=4)
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.picks) < EXTRA_DECK_MIN_SIZE:
            await interaction.response.send_message(f"Choose at least {EXTRA_DECK_MIN_SIZE} Extra Deck Monster first.", ephemeral=True)
            return
        if not db.create_extra_deck(self.user_id, self.deck_name, self.picks):
            await interaction.response.send_message(f"Could not create that Extra Deck. It must have {EXTRA_DECK_MIN_SIZE}-{EXTRA_DECK_SIZE} owned Extra Deck Monsters and a name of at least 2 characters.", ephemeral=True)
            return
        await safe_edit_interaction_message(interaction, content=f"Created Extra Deck **{self.deck_name}** with **{len(self.picks)}** monster(s).", view=None)
        await self.browser.refresh_message(self.browser_message)
        self.stop()


class ExtraDeckNameModal(discord.ui.Modal, title="Create Extra Deck"):
    deck_name = discord.ui.TextInput(label="Extra Deck name", min_length=2, max_length=30, placeholder="Type the Extra Deck name")

    def __init__(self, browser: ExtraDeckBrowserView, browser_message: Optional[discord.Message]):
        super().__init__()
        self.browser = browser
        self.browser_message = browser_message

    async def on_submit(self, interaction: discord.Interaction):
        name = str(self.deck_name).strip()
        builder = ExtraDeckBuilderView(self.browser, self.browser_message, name)
        await interaction.response.send_message(builder.status_text(), view=builder, ephemeral=True)
        try:
            builder.message = await interaction.original_response()
        except Exception:
            builder.message = None


class ExtraDeckRenameModal(discord.ui.Modal, title="Rename Extra Deck"):
    deck_name = discord.ui.TextInput(label="New Extra Deck name", min_length=2, max_length=30)

    def __init__(self, browser: ExtraDeckBrowserView, deck_id: int, current_name: str, browser_message: Optional[discord.Message]):
        super().__init__()
        self.browser = browser
        self.deck_id = int(deck_id)
        self.browser_message = browser_message
        self.deck_name.default = current_name

    async def on_submit(self, interaction: discord.Interaction):
        new_name = str(self.deck_name).strip()
        if not db.update_extra_deck_name(self.browser.owner_id, self.deck_id, new_name):
            await interaction.response.send_message("Could not rename that Extra Deck.", ephemeral=True)
            return
        await interaction.response.send_message(f"Renamed Extra Deck to **{new_name}**.", ephemeral=True)
        await self.browser.refresh_message(self.browser_message)


class ExtraDeckMonstersModal(discord.ui.Modal, title="Update Extra Deck Monsters"):
    monsters = discord.ui.TextInput(label="Monsters", style=discord.TextStyle.paragraph, max_length=1000, placeholder="Monster name/id, Monster name/id, ...")

    def __init__(self, browser: ExtraDeckBrowserView, deck_id: int, browser_message: Optional[discord.Message]):
        super().__init__()
        self.browser = browser
        self.deck_id = int(deck_id)
        self.browser_message = browser_message
        deck = db.get_extra_deck(browser.owner_id, self.deck_id)
        if deck is not None:
            current_names = []
            for instance_id in decode_extra_deck_monsters(str(deck["monsters_json"])):
                row = db.get_inventory_instance(instance_id, browser.owner_id)
                current_names.append(str(row["monster_name"]) if row is not None else instance_id)
            self.monsters.default = ", ".join(current_names)

    async def on_submit(self, interaction: discord.Interaction):
        user_id = self.browser.owner_id
        picks: List[str] = []
        missing: List[str] = []
        for part in [piece.strip() for piece in str(self.monsters).split(",") if piece.strip()]:
            instance_id = find_owned_extra_deck_monster_instance(user_id, part)
            if instance_id:
                picks.append(instance_id)
            else:
                missing.append(part)
        if missing:
            await interaction.response.send_message("I could not find these owned Extra Deck Monsters: " + ", ".join(f"**{item}**" for item in missing[:8]), ephemeral=True)
            return
        if not db.update_extra_deck_monsters(user_id, self.deck_id, picks):
            await interaction.response.send_message(f"Could not update that Extra Deck. It must have {EXTRA_DECK_MIN_SIZE}-{EXTRA_DECK_SIZE} owned Extra Deck Monsters.", ephemeral=True)
            return
        await interaction.response.send_message(f"Updated Extra Deck #{self.deck_id} with **{len(picks)}** monster(s).", ephemeral=True)
        await self.browser.refresh_message(self.browser_message)


class ExtraDeckManageSelect(discord.ui.Select):
    def __init__(self, manage_view: "ExtraDeckManageView", action: str):
        self.manage_view = manage_view
        self.action = action
        options: List[discord.SelectOption] = []
        for deck in db.list_extra_decks(manage_view.browser.owner_id)[:25]:
            options.append(discord.SelectOption(label=f"{deck['name']} (#{deck['id']})"[:100], value=str(deck["id"])))
        if not options:
            options = [discord.SelectOption(label="No Extra Decks", value="0")]
        labels = {"rename": "Choose an Extra Deck to rename", "monsters": "Choose an Extra Deck to update", "delete": "Choose an Extra Deck to delete"}
        super().__init__(placeholder=labels.get(action, "Choose an Extra Deck"), min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            await safe_acknowledge_interaction(interaction)
            return
        deck_id = int(self.values[0])
        browser = self.manage_view.browser
        deck = db.get_extra_deck(browser.owner_id, deck_id)
        if deck is None:
            await interaction.response.send_message("That Extra Deck was not found.", ephemeral=True)
            return
        if self.action == "rename":
            await interaction.response.send_modal(ExtraDeckRenameModal(browser, deck_id, str(deck["name"]), self.manage_view.browser_message))
            return
        if self.action == "monsters":
            await interaction.response.send_modal(ExtraDeckMonstersModal(browser, deck_id, self.manage_view.browser_message))
            return
        if self.action == "delete":
            if db.delete_extra_deck(browser.owner_id, deck_id):
                await interaction.response.send_message(f"Deleted Extra Deck #{deck_id}.", ephemeral=True)
                await browser.refresh_message(self.manage_view.browser_message)
            else:
                await interaction.response.send_message("That Extra Deck was not found.", ephemeral=True)


class ExtraDeckManageActionView(discord.ui.View):
    def __init__(self, manage_view: "ExtraDeckManageView", action: str):
        super().__init__(timeout=180)
        self.add_item(ExtraDeckManageSelect(manage_view, action))


class ExtraDeckManageView(discord.ui.View):
    def __init__(self, browser: ExtraDeckBrowserView, browser_message: Optional[discord.Message]):
        super().__init__(timeout=180)
        self.browser = browser
        self.browser_message = browser_message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.browser.owner_id:
            await interaction.response.send_message("This Extra Deck menu belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Create", style=discord.ButtonStyle.success, row=0)
    async def create_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        owned_count = sum(1 for row in db.user_inventory(self.browser.owner_id) if is_extra_deck_monster_row(row))
        if owned_count < EXTRA_DECK_MIN_SIZE:
            await interaction.response.send_message("You do not own enough Extra Deck Monsters yet.", ephemeral=True)
            return
        await interaction.response.send_modal(ExtraDeckNameModal(self.browser, self.browser_message))

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.primary, row=0)
    async def rename_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Choose the Extra Deck to rename.", view=ExtraDeckManageActionView(self, "rename"), ephemeral=True)

    @discord.ui.button(label="Monsters", style=discord.ButtonStyle.primary, row=0)
    async def monsters_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Choose the Extra Deck to update.", view=ExtraDeckManageActionView(self, "monsters"), ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, row=1)
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Choose the Extra Deck to delete.", view=ExtraDeckManageActionView(self, "delete"), ephemeral=True)
