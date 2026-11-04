"""Support deck browser and editing menus."""

from __future__ import annotations

import asyncio
import math
import sqlite3
from datetime import datetime
from typing import List, Optional, Tuple

import discord

from kuribot.config import ATTRIBUTES, MONSTER_TYPES, SUPPORT_DECK_MIN_SIZE, SUPPORT_DECK_SIZE
from kuribot.models import SupportCardDef
from kuribot.database import db
from kuribot.render.images import _PanelUserContext
from kuribot.cards.support_cards import (
    get_current_support_card_def_for_user, get_support_card_def, support_card_family_key,
    support_card_stage_number,
)
from kuribot.cards.support_decks import decode_support_cards, user_unique_support_card_count
from kuribot.render.collection import render_support_deck_page
from kuribot.ui.common import (
    safe_acknowledge_interaction, safe_edit_interaction_message, safe_send_interaction_message,
)
from kuribot.ui.collection import build_support_deck_detail_embed
from kuribot.ui.teams import SimpleSelectView
from kuribot.ui.support_decks import (
    SupportDeckAutoNameModal, SupportDeckDeleteSelect, SupportDeckManagerView,
    SupportDeckNameModal,
)


class DeckItemSelect(discord.ui.Select):
    def __init__(self, browser: "DeckBrowserView"):
        self.browser = browser
        rows = browser.page_rows()
        options = []
        for row in rows:
            preview = ", ".join(row.get("preview_names") or []) or "No preview"
            label = str(row["name"])[:100]
            description = f"#{row['deck_id']} • {row['card_count']}/{SUPPORT_DECK_SIZE} • {preview}"[:100]
            options.append(discord.SelectOption(label=label, value=str(row["deck_id"]), description=description, default=(str(row["deck_id"]) == str(browser.selected_deck_id))))
        if not options:
            options = [discord.SelectOption(label="No decks on this page", value="__none__")]
        # Discord select menus consume a full row. Keep every deck browser
        # select on a separate row so ;deck cannot crash with a width overflow.
        super().__init__(placeholder="Open a deck from this page", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "__none__":
            await safe_acknowledge_interaction(interaction)
            return
        self.browser.selected_deck_id = self.values[0]
        row = self.browser.get_selected_row()
        if row is None:
            await interaction.response.send_message("That deck is no longer available.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_support_deck_detail_embed(row), ephemeral=True)


class DeckFilterSelect(discord.ui.Select):
    def __init__(self, browser: "DeckBrowserView"):
        self.browser = browser
        options = [discord.SelectOption(label=label[:100], value=key, default=(key == browser.filter_key)) for key, label in browser.filter_options()]
        super().__init__(placeholder="General Filter", min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        self.browser.filter_key = self.values[0]
        self.browser.page = 1
        self.browser.rebuild()
        await self.browser.refresh(interaction)


class DeckMonsterTypeFilterSelect(discord.ui.Select):
    PAGE_SIZE = 20

    def __init__(self, browser: "DeckBrowserView"):
        self.browser = browser
        page_count = max(1, math.ceil(len(MONSTER_TYPES) / self.PAGE_SIZE))
        current_page = max(0, min(int(getattr(browser, "monster_type_filter_page", 0)), page_count - 1))
        start_index = current_page * self.PAGE_SIZE
        end_index = start_index + self.PAGE_SIZE
        visible_types = MONSTER_TYPES[start_index:end_index]

        options = []
        if current_page > 0:
            options.append(discord.SelectOption(label="‹ Previous Type Page", value=f"typepage:{current_page - 1}"))
        if current_page < page_count - 1:
            options.append(discord.SelectOption(label="Next Type Page ›", value=f"typepage:{current_page + 1}"))
        options.append(discord.SelectOption(label="No Type Filter", value="all", default=(browser.filter_key == "all")))
        options.extend(
            discord.SelectOption(
                label=monster_type[:100],
                value=f"attr:{monster_type}",
                default=(browser.filter_key == f"attr:{monster_type}"),
            )
            for monster_type in visible_types
        )
        placeholder = f"Monster Type Filter ({current_page + 1}/{page_count})"
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value.startswith("typepage:"):
            try:
                self.browser.monster_type_filter_page = int(value.split(":", 1)[1])
            except Exception:
                self.browser.monster_type_filter_page = 0
        else:
            self.browser.filter_key = value
        self.browser.page = 1
        self.browser.rebuild()
        await self.browser.refresh(interaction)


class DeckSortSelect(discord.ui.Select):
    def __init__(self, browser: "DeckBrowserView"):
        self.browser = browser
        options = [discord.SelectOption(label=label[:100], value=key, default=(key == browser.sort_key)) for key, label in browser.sort_options()]
        super().__init__(placeholder="Order", min_values=1, max_values=1, options=options, row=3)

    async def callback(self, interaction: discord.Interaction):
        self.browser.sort_key = self.values[0]
        self.browser.page = 1
        self.browser.rebuild()
        await self.browser.refresh(interaction)


class SupportDeckRenameModal(discord.ui.Modal, title="Rename Deck"):
    deck_name = discord.ui.TextInput(label="Deck name", min_length=2, max_length=30, placeholder="Type the new deck name")

    def __init__(self, owner_id: int, deck_id: int, current_name: str, manager: Optional["SupportDeckManagerView"] = None):
        super().__init__()
        self.owner_id = owner_id
        self.deck_id = deck_id
        self.manager = manager
        self.deck_name.default = current_name

    async def on_submit(self, interaction: discord.Interaction):
        new_name = str(self.deck_name).strip()
        if not db.update_support_deck_name(self.owner_id, self.deck_id, new_name):
            await interaction.response.send_message("Could not rename that deck.", ephemeral=True)
            return
        await interaction.response.send_message(f"Renamed deck to **{new_name}**.", ephemeral=True)
        if self.manager is not None:
            await self.manager.refresh_deck_browser_message()


class SupportDeckEditView(discord.ui.View):
    def __init__(self, owner_id: int, deck_id: int, manager: Optional["SupportDeckManagerView"] = None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.deck_id = deck_id
        self.manager = manager

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This deck belongs to someone else.", ephemeral=True)
            return False
        return True

    def current_row(self) -> Optional[sqlite3.Row]:
        return db.get_support_deck(self.owner_id, self.deck_id) if hasattr(db, "get_support_deck") else next((row for row in db.list_support_decks(self.owner_id) if int(row["id"]) == int(self.deck_id)), None)

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.primary, row=0)
    async def rename_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.current_row()
        if row is None:
            await interaction.response.send_message("That deck could not be found.", ephemeral=True)
            return
        await interaction.response.send_modal(SupportDeckRenameModal(self.owner_id, self.deck_id, str(row["name"]), manager=self.manager))

    @discord.ui.button(label="Swap Cards", style=discord.ButtonStyle.success, row=0)
    async def swap_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.current_row()
        if row is None:
            await interaction.response.send_message("That deck could not be found.", ephemeral=True)
            return
        swap_view = SupportDeckSwapView(self.owner_id, self.deck_id, manager=self.manager)
        await interaction.response.send_message(swap_view.status_text("Choose a deck slot, choose a card you own, then apply the swap."), view=swap_view, ephemeral=True)


class SupportDeckSwapSlotSelect(discord.ui.Select):
    def __init__(self, view: "SupportDeckSwapView"):
        self.swap_view = view
        options: List[discord.SelectOption] = []
        for idx in range(SUPPORT_DECK_SIZE):
            card = view.card_at_index(idx)
            if card is None:
                label = f"Add to Slot {idx + 1}"
                description = "Empty slot"
            else:
                label = f"Replace Slot {idx + 1}"
                description = card.name
            options.append(discord.SelectOption(label=label[:100], value=str(idx), description=description[:100], default=view.selected_slot == idx))
        super().__init__(placeholder="Choose a deck slot", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.swap_view.selected_slot = int(self.values[0])
        self.swap_view.rebuild()
        await safe_edit_interaction_message(interaction, content=self.swap_view.status_text(f"Selected slot **{self.swap_view.selected_slot + 1}**."), view=self.swap_view)


class SupportDeckSwapCardSelect(discord.ui.Select):
    def __init__(self, view: "SupportDeckSwapView"):
        self.swap_view = view
        rows = view.page_rows()
        options: List[discord.SelectOption] = []
        for row in rows:
            card = get_support_card_def(str(row["support_card_id"]))
            if card is None:
                continue
            options.append(discord.SelectOption(
                label=card.name[:100],
                value=str(row["instance_id"]),
                description=view.build_row_description(row)[:100],
                default=view.selected_instance_id == str(row["instance_id"]),
            ))
        if not options:
            options = [discord.SelectOption(label="No support cards", value="0")]
        super().__init__(placeholder="Choose a replacement card", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            await safe_acknowledge_interaction(interaction)
            return
        self.swap_view.selected_instance_id = str(self.values[0])
        self.swap_view.rebuild()
        selected_row = db.get_support_card_instance(self.swap_view.selected_instance_id, self.swap_view.user_id)
        selected_card = get_support_card_def(str(selected_row["support_card_id"])) if selected_row else None
        card_name = selected_card.name if selected_card else "that card"
        await safe_edit_interaction_message(interaction, content=self.swap_view.status_text(f"Selected **{card_name}**."), view=self.swap_view)


class SupportDeckSwapSortSelect(discord.ui.Select):
    def __init__(self, view: "SupportDeckSwapView"):
        self.swap_view = view
        options = [
            discord.SelectOption(label="A-Z", value="name_asc", default=view.sort_key == "name_asc"),
            discord.SelectOption(label="Z-A", value="name_desc", default=view.sort_key == "name_desc"),
            discord.SelectOption(label="Target A-Z", value="target_asc", default=view.sort_key == "target_asc"),
            discord.SelectOption(label="Highest Bonus", value="bonus_desc", default=view.sort_key == "bonus_desc"),
            discord.SelectOption(label="Newest", value="newest", default=view.sort_key == "newest"),
            discord.SelectOption(label="Oldest", value="oldest", default=view.sort_key == "oldest"),
        ]
        super().__init__(placeholder="Sort owned cards", min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        self.swap_view.sort_key = self.values[0]
        self.swap_view.page = 0
        self.swap_view.rebuild()
        await safe_edit_interaction_message(interaction, content=self.swap_view.status_text(f"Sorting by **{self.swap_view.sort_label()}**."), view=self.swap_view)


class SupportDeckSwapView(discord.ui.View):
    PAGE_SIZE = 25

    def __init__(self, user_id: int, deck_id: int, manager: Optional["SupportDeckManagerView"] = None):
        super().__init__(timeout=600)
        self.user_id = user_id
        self.deck_id = deck_id
        self.manager = manager
        self.page = 0
        self.sort_key = "name_asc"
        self.selected_slot = 0
        self.selected_instance_id: Optional[str] = None
        self.inventory_rows = db.user_support_cards(user_id)
        self.rebuild()

    def current_deck_row(self) -> Optional[sqlite3.Row]:
        return db.get_support_deck(self.user_id, self.deck_id)

    def current_support_card_ids(self) -> List[str]:
        row = self.current_deck_row()
        if row is None:
            return []
        return decode_support_cards(row["cards_json"])

    def card_at_index(self, index: int) -> Optional[SupportCardDef]:
        card_ids = self.current_support_card_ids()
        if 0 <= index < len(card_ids):
            return get_current_support_card_def_for_user(self.user_id, str(card_ids[index])) or get_support_card_def(str(card_ids[index]))
        return None

    def deck_cards_text(self) -> str:
        labels: List[str] = []
        for idx in range(SUPPORT_DECK_SIZE):
            card = self.card_at_index(idx)
            if card is None:
                labels.append(f"{idx + 1}. [Empty]")
            else:
                labels.append(f"{idx + 1}. {card.name}")
        return "\n".join(labels)

    def row_card(self, row: sqlite3.Row) -> Optional[SupportCardDef]:
        return get_support_card_def(str(row["support_card_id"]))

    def row_target_name(self, row: sqlite3.Row) -> str:
        card = self.row_card(row)
        if card is None:
            return "Unknown"
        if str(card.stat_type).upper() == "SPECIAL":
            return "Special"
        return str(card.attribute)

    def row_description_core(self, row: sqlite3.Row) -> str:
        card = self.row_card(row)
        if card is None:
            return "Unknown support card"
        if str(card.attribute).upper() == "SPECIAL" or str(card.stat_type).upper() == "SPECIAL":
            return "Special"
        return f"{card.attribute} • +{card.bonus_pct}% {card.stat_type}"

    def build_row_description(self, row: sqlite3.Row) -> str:
        marker = "• In Deck" if self.family_in_deck(row, ignore_selected_slot=True) else "• Available"
        return f"{self.row_description_core(row)} {marker}"

    def family_in_deck(self, row: sqlite3.Row, *, ignore_selected_slot: bool = False) -> bool:
        card = self.row_card(row)
        if card is None:
            return False
        candidate_family = support_card_family_key(card.support_card_id)
        current_ids = self.current_support_card_ids()
        for idx, saved_id in enumerate(current_ids):
            if ignore_selected_slot and idx == self.selected_slot:
                continue
            if support_card_family_key(str(saved_id)) == candidate_family:
                return True
        return False

    def sorted_inventory_rows(self) -> List[sqlite3.Row]:
        rows = list(self.inventory_rows)

        def key_name_asc(row: sqlite3.Row):
            card = self.row_card(row)
            return (str(card.name if card else row["support_card_id"]).lower(), str(self.row_target_name(row)).lower(), str(row["instance_id"]))

        def key_target_asc(row: sqlite3.Row):
            card = self.row_card(row)
            return (str(self.row_target_name(row)).lower(), str(card.name if card else row["support_card_id"]).lower(), str(row["instance_id"]))

        def key_bonus_desc(row: sqlite3.Row):
            card = self.row_card(row)
            bonus = int(card.bonus_pct if card else 0)
            stage = support_card_stage_number(card.support_card_id) if card else 0
            return (-bonus, -stage, str(card.name if card else row["support_card_id"]).lower(), str(row["instance_id"]))

        def key_stage_desc(row: sqlite3.Row):
            card = self.row_card(row)
            stage = support_card_stage_number(card.support_card_id) if card else 0
            bonus = int(card.bonus_pct if card else 0)
            return (-stage, -bonus, str(card.name if card else row["support_card_id"]).lower(), str(row["instance_id"]))

        def safe_timestamp(raw_value: object) -> float:
            try:
                return datetime.fromisoformat(str(raw_value)).timestamp()
            except Exception:
                return 0.0

        if self.sort_key == "name_desc":
            return sorted(rows, key=key_name_asc, reverse=True)
        if self.sort_key == "target_asc":
            return sorted(rows, key=key_target_asc)
        if self.sort_key == "bonus_desc":
            return sorted(rows, key=key_bonus_desc)
        if self.sort_key == "stage_desc":
            return sorted(rows, key=key_stage_desc)
        if self.sort_key == "newest":
            return sorted(rows, key=lambda row: (-safe_timestamp(row["obtained_at"]), str(row["instance_id"])))
        if self.sort_key == "oldest":
            return sorted(rows, key=lambda row: (safe_timestamp(row["obtained_at"]), str(row["instance_id"])))
        return sorted(rows, key=key_name_asc)

    def page_rows(self) -> List[sqlite3.Row]:
        rows = self.sorted_inventory_rows()
        start = self.page * self.PAGE_SIZE
        return rows[start:start + self.PAGE_SIZE]

    def sort_label(self) -> str:
        return {
            "name_asc": "A-Z",
            "name_desc": "Z-A",
            "target_asc": "Target A-Z",
            "bonus_desc": "Highest Bonus",
            "newest": "Newest",
            "oldest": "Oldest",
        }.get(self.sort_key, "A-Z")

    def status_text(self, extra: str = "") -> str:
        deck_row = self.current_deck_row()
        deck_name = str(deck_row["name"]) if deck_row is not None else "Unknown Deck"
        total_rows = len(self.sorted_inventory_rows())
        total_pages = max(1, math.ceil(max(1, total_rows) / self.PAGE_SIZE))
        selected_slot_text = f"Slot {self.selected_slot + 1}"
        selected_row = db.get_support_card_instance(self.selected_instance_id, self.user_id) if self.selected_instance_id else None
        selected_card = get_support_card_def(str(selected_row["support_card_id"])) if selected_row else None
        selected_card_text = selected_card.name if selected_card else "None"
        base = (
            f"Deck **{deck_name}**\n"
            f"Selected slot: **{selected_slot_text}** • Selected card: **{selected_card_text}** • Sort: **{self.sort_label()}** • Page **{self.page + 1}/{total_pages}**\n"
            f"{self.deck_cards_text()}"
        )
        if extra:
            return base + f"\n{extra}"
        return base

    def apply_swap(self) -> str:
        if self.selected_instance_id is None:
            return "Choose a card you own first."
        row = db.get_support_card_instance(self.selected_instance_id, self.user_id)
        if row is None:
            return "You do not own that support card."
        card = get_support_card_def(str(row["support_card_id"]))
        if card is None:
            return "That support card no longer exists."
        current_ids = list(self.current_support_card_ids())
        if self.selected_slot < len(current_ids):
            replaced_card = get_current_support_card_def_for_user(self.user_id, str(current_ids[self.selected_slot])) or get_support_card_def(str(current_ids[self.selected_slot]))
            current_ids[self.selected_slot] = card.support_card_id
            action = f"Replaced **{replaced_card.name if replaced_card else 'that card'}** with **{card.name}** in slot **{self.selected_slot + 1}**."
        else:
            while len(current_ids) < self.selected_slot:
                current_ids.append("")
            current_ids.append(card.support_card_id)
            action = f"Added **{card.name}** to slot **{self.selected_slot + 1}**."
        cleaned_ids = [saved_id for saved_id in current_ids if str(saved_id).strip()]
        if len(cleaned_ids) > SUPPORT_DECK_SIZE:
            return f"That deck cannot exceed {SUPPORT_DECK_SIZE} cards."
        families = [support_card_family_key(str(saved_id)) for saved_id in cleaned_ids]
        if len(families) != len(set(families)):
            return "That support card family is already in this deck. Support decks can only use unique cards."
        owned_instance_ids: List[str] = []
        for saved_id in cleaned_ids:
            owned_row = db.get_support_card_instance_by_family(self.user_id, support_card_family_key(str(saved_id)))
            if owned_row is None:
                return "You no longer own one of the cards in this deck."
            owned_instance_ids.append(str(owned_row["instance_id"]))
        if not db.update_support_deck_cards(self.user_id, self.deck_id, owned_instance_ids):
            return "Could not update that deck."
        self.inventory_rows = db.user_support_cards(self.user_id)
        self.selected_instance_id = None
        self.rebuild()
        return action

    def rebuild(self):
        self.clear_items()
        total_pages = max(1, math.ceil(max(1, len(self.sorted_inventory_rows())) / self.PAGE_SIZE))
        self.page = max(0, min(self.page, total_pages - 1))
        self.add_item(SupportDeckSwapSlotSelect(self))
        self.add_item(SupportDeckSwapCardSelect(self))
        self.add_item(SupportDeckSwapSortSelect(self))
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.apply_button)
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= total_pages - 1
        self.apply_button.disabled = self.selected_instance_id is None

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=3)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.status_text(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=3)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        total_pages = max(1, math.ceil(max(1, len(self.sorted_inventory_rows())) / self.PAGE_SIZE))
        self.page = min(total_pages - 1, self.page + 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.status_text(), view=self)

    @discord.ui.button(label="Apply Swap", style=discord.ButtonStyle.success, row=3)
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        result = self.apply_swap()
        await safe_edit_interaction_message(interaction, content=self.status_text(result), view=self)
        if self.manager is not None:
            await self.manager.refresh_deck_browser_message()


class SupportDeckEditSelect(discord.ui.Select):
    def __init__(self, user_id: int, manager: Optional["SupportDeckManagerView"] = None):
        self.user_id = user_id
        self.manager = manager
        decks = db.list_support_decks(user_id)
        options = [discord.SelectOption(label=f"{deck['name']} (#{deck['id']})", value=str(deck['id'])) for deck in decks] or [discord.SelectOption(label="No decks", value="0")]
        super().__init__(placeholder="Choose a deck to edit", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            await safe_acknowledge_interaction(interaction)
            return
        deck_id = int(self.values[0])
        row = next((deck for deck in db.list_support_decks(self.user_id) if int(deck["id"]) == deck_id), None)
        if row is None:
            await interaction.response.send_message("That deck could not be found.", ephemeral=True)
            return
        await interaction.response.send_message(f"Editing deck **{row['name']}**.", view=SupportDeckEditView(self.user_id, deck_id, manager=self.manager), ephemeral=True)


class DeckManageView(discord.ui.View):
    def __init__(self, browser: "DeckBrowserView"):
        super().__init__(timeout=300)
        self.browser = browser
        self.owner_id = browser.owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This binder belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Create", style=discord.ButtonStyle.success, row=0)
    async def create_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if user_unique_support_card_count(self.owner_id) < SUPPORT_DECK_MIN_SIZE:
            await interaction.response.send_message("You do not own any support cards yet.", ephemeral=True)
            return
        proxy = DeckBrowserManagerProxy(self.browser)
        proxy.browser_message = interaction.message
        await interaction.response.send_modal(SupportDeckNameModal(proxy))

    @discord.ui.button(label="Auto Create", style=discord.ButtonStyle.success, row=0)
    async def auto_create_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if user_unique_support_card_count(self.owner_id) < SUPPORT_DECK_MIN_SIZE:
            await interaction.response.send_message("You do not own any support cards yet.", ephemeral=True)
            return
        if not db.list_teams(self.owner_id):
            await interaction.response.send_message("Create a team first so Auto Create has something to build around.", ephemeral=True)
            return
        proxy = DeckBrowserManagerProxy(self.browser)
        proxy.browser_message = interaction.message
        await interaction.response.send_modal(SupportDeckAutoNameModal(proxy))

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, row=0)
    async def edit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        proxy = DeckBrowserManagerProxy(self.browser)
        proxy.browser_message = interaction.message
        await interaction.response.send_message("Choose the deck to edit.", view=SimpleSelectView(SupportDeckEditSelect(self.owner_id, manager=proxy)), ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, row=0)
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        proxy = DeckBrowserManagerProxy(self.browser)
        proxy.browser_message = interaction.message
        await interaction.response.send_message("Choose the deck to delete.", view=SimpleSelectView(SupportDeckDeleteSelect(self.owner_id, manager=proxy)), ephemeral=True)


class DeckBrowserView(discord.ui.View):
    PER_PAGE = 8

    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.page = 1
        self.filter_key = "all"
        self.sort_key = "name_asc"
        self.filtered_rows: List[dict] = []
        self.selected_deck_id: Optional[str] = None
        self.rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This binder belongs to someone else.", ephemeral=True)
            return False
        return True

    def all_rows(self) -> List[dict]:
        rows: List[dict] = []
        for deck in db.list_support_decks(self.owner_id):
            card_ids = decode_support_cards(deck["cards_json"])
            cards = [get_support_card_def(card_id) for card_id in card_ids]
            cards = [card for card in cards if card is not None]
            attributes = sorted({card.attribute for card in cards})
            stats = sorted({card.stat_type for card in cards})
            rows.append({
                "deck_id": deck["id"],
                "instance_id": str(deck["id"]),
                "name": deck["name"],
                "card_count": len(card_ids),
                "cards": cards,
                "preview_names": [card.name for card in cards[:3]],
                "attribute_set": attributes,
                "stat_set": stats,
                "is_active": False,
            })
        return rows

    def filter_options(self) -> List[Tuple[str, str]]:
        attribute_options = [(f"attr:{attribute}", attribute.title()) for attribute in ATTRIBUTES]
        stat_options = [("stat:ATK", "Has ATK Cards"), ("stat:DEF", "Has DEF Cards"), ("stat:SPD", "Has SPD Cards")]
        return [("all", "All Decks")] + attribute_options + stat_options

    def sort_options(self) -> List[Tuple[str, str]]:
        return [("name_asc", "Name A-Z"), ("name_desc", "Name Z-A"), ("deck_id_desc", "Newest First"), ("deck_id_asc", "Oldest First")]

    def apply_filter(self, rows: List[dict]) -> List[dict]:
        if self.filter_key == "all":
            return list(rows)
        if self.filter_key.startswith("attr:"):
            attr = self.filter_key.split(":", 1)[1]
            return [row for row in rows if attr in row.get("attribute_set", [])]
        if self.filter_key.startswith("stat:"):
            stat_type = self.filter_key.split(":", 1)[1].upper()
            return [row for row in rows if stat_type in row.get("stat_set", [])]
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
        for row in self.filtered_rows:
            if str(row["deck_id"]) == str(self.selected_deck_id):
                return row
        return None

    def current_filter_label(self) -> str:
        if self.filter_key.startswith("attr:"):
            target = self.filter_key.split(":", 1)[1]
            if target in ATTRIBUTES:
                return target.title()
            if target in MONSTER_TYPES:
                return target
        return dict(self.filter_options()).get(self.filter_key, "All Decks")

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
        self.add_item(DeckItemSelect(self))
        self.add_item(DeckMonsterTypeFilterSelect(self))
        self.add_item(DeckFilterSelect(self))
        self.add_item(DeckSortSelect(self))
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
            self._summary_counts = len(db.list_support_decks(self.owner_id))
        total_owned = self._summary_counts
        visible_start = 0 if not self.filtered_rows else (self.page - 1) * self.PER_PAGE + 1
        visible_end = min(len(self.filtered_rows), self.page * self.PER_PAGE)
        return f"Showing {visible_start}-{visible_end} of {len(self.filtered_rows)} filtered decks • {total_owned} total decks • Decks attach to teams"

    def make_file(self) -> discord.File:
        with _PanelUserContext(self.owner_id):
            buffer = render_support_deck_page(self.user_name, self.filtered_rows, self.page, self.PER_PAGE, self.current_filter_label(), self.current_sort_label(), db.get_ui_theme(self.owner_id))
            return discord.File(buffer, filename="decks.png")

    async def refresh(self, interaction: discord.Interaction):
        await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments_factory=lambda: [self.make_file()], view=self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.primary, row=4)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.ui.binder import binder_hub_summary_text, BinderHubView
        view = BinderHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=binder_hub_summary_text(self.owner_id), attachments_factory=lambda: [view.make_file()], view=view)

    @discord.ui.button(label="Manage", style=discord.ButtonStyle.success, row=4)
    async def manage_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_send_interaction_message(interaction, content="Manage decks:", view=DeckManageView(self), ephemeral=True)

    @discord.ui.button(label="Create", style=discord.ButtonStyle.success, row=0)
    async def create_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if user_unique_support_card_count(self.owner_id) < SUPPORT_DECK_MIN_SIZE:
            await interaction.response.send_message("You do not own any support cards yet.", ephemeral=True)
            return
        proxy = DeckBrowserManagerProxy(self)
        proxy.browser_message = interaction.message
        await interaction.response.send_modal(SupportDeckNameModal(proxy))

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, row=1)
    async def edit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        proxy = DeckBrowserManagerProxy(self)
        proxy.browser_message = interaction.message
        await interaction.response.send_message("Choose the deck to edit.", view=SimpleSelectView(SupportDeckEditSelect(self.owner_id, manager=proxy)), ephemeral=True)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, row=2)
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        proxy = DeckBrowserManagerProxy(self)
        proxy.browser_message = interaction.message
        await interaction.response.send_message("Choose the deck to delete.", view=SimpleSelectView(SupportDeckDeleteSelect(self.owner_id, manager=proxy)), ephemeral=True)

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


class DeckBrowserManagerProxy:
    def __init__(self, browser: "DeckBrowserView"):
        self.user_id = browser.owner_id
        self.browser = browser
        self.browser_message: Optional[discord.Message] = None

    async def refresh_deck_browser_message(self):
        self.browser.rebuild()
        if self.browser_message is None:
            return
        try:
            await self.browser_message.edit(content=self.browser.summary_text(), attachments=[await asyncio.to_thread(self.browser.make_file)], view=self.browser)
        except Exception:
            pass
