"""Support deck builder menus."""

from __future__ import annotations

import asyncio
import math
import re
import sqlite3
from datetime import datetime
from typing import List, Optional, Sequence, TYPE_CHECKING

import discord

from kuribot.config import ATTRIBUTES, MONSTER_TYPES, SUPPORT_DECK_MIN_SIZE, SUPPORT_DECK_SIZE
from kuribot.models import SupportCardDef
from kuribot.database import db
from kuribot.monsters import display_monster_label_from_id, team_slots_from_csv
from kuribot.cards.support_cards import (
    get_support_card_def, support_card_family_key, support_card_stage_number,
)
from kuribot.cards.support_decks import (
    unique_support_card_ids_from_instance_ids, user_unique_support_card_count,
)
from kuribot.battle.special_support import SPECIAL_SUPPORT_IDS
from kuribot.battle.setup import auto_build_support_deck_instance_ids
from kuribot.ui.common import safe_acknowledge_interaction, safe_edit_interaction_message
from kuribot.ui.teams import SimpleSelectView

if TYPE_CHECKING:  # only for type hints; these modules import this one
    from kuribot.ui.decks import DeckBrowserView


class SupportDeckNameModal(discord.ui.Modal, title="Create Deck"):

    deck_name = discord.ui.TextInput(label="Deck name", min_length=2, max_length=30, placeholder="Type a deck name")

    def __init__(self, parent: "SupportDeckManagerView"):
        super().__init__()
        self.parent_view = parent

    async def on_submit(self, interaction: discord.Interaction):
        builder = SupportDeckBuilderView(self.parent_view.user_id, str(self.deck_name).strip(), manager=self.parent_view)
        await interaction.response.send_message(
            builder.status_text(f"Pick between {SUPPORT_DECK_MIN_SIZE}-{SUPPORT_DECK_SIZE} unique support cards."),
            view=builder,
            ephemeral=True,
        )


class SupportCardSelect(discord.ui.Select):
    def __init__(self, builder: "SupportDeckBuilderView"):
        self.builder = builder
        rows = builder.page_rows()
        options = []
        for row in rows:
            card = get_support_card_def(row["support_card_id"])
            if card is None:
                continue
            options.append(discord.SelectOption(
                label=card.name[:100],
                value=row["instance_id"],
                description=builder.build_row_description(row)[:100],
            ))
        if not options:
            options = [discord.SelectOption(label="No support cards", value="0")]
        super().__init__(placeholder="Choose support cards", min_values=1, max_values=min(len(options), 10), options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values == ["0"]:
            await safe_acknowledge_interaction(interaction)
            return
        results = [self.builder.add_pick(value) for value in self.values]
        await safe_edit_interaction_message(interaction, content=self.builder.status_text("\n".join(results[-3:])), view=self.builder)


class SupportDeckSortSelect(discord.ui.Select):
    def __init__(self, builder: "SupportDeckBuilderView"):
        self.builder = builder
        options = [
            discord.SelectOption(label="A-Z", value="name_asc", default=builder.sort_key == "name_asc"),
            discord.SelectOption(label="Z-A", value="name_desc", default=builder.sort_key == "name_desc"),
            discord.SelectOption(label="Target A-Z", value="target_asc", default=builder.sort_key == "target_asc"),
            discord.SelectOption(label="Highest Bonus", value="bonus_desc", default=builder.sort_key == "bonus_desc"),
            discord.SelectOption(label="Newest", value="newest", default=builder.sort_key == "newest"),
            discord.SelectOption(label="Oldest", value="oldest", default=builder.sort_key == "oldest"),
        ]
        super().__init__(placeholder="Sort support cards", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.builder.sort_key = self.values[0]
        self.builder.page = 0
        self.builder.rebuild()
        await safe_edit_interaction_message(interaction, content=self.builder.status_text(f"Sorting by **{self.builder.sort_label()}**."), view=self.builder)


class SupportDeckAttributeFilterSelect(discord.ui.Select):
    def __init__(self, builder: "SupportDeckBuilderView"):
        self.builder = builder
        options = [discord.SelectOption(label="All Attributes", value="__all__", default=builder.attribute_filter == "__all__")]
        options.extend(
            discord.SelectOption(label=attribute.title(), value=attribute, default=builder.attribute_filter == attribute)
            for attribute in ATTRIBUTES
        )
        super().__init__(placeholder="Filter by attribute", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        self.builder.attribute_filter = self.values[0]
        self.builder.page = 0
        self.builder.rebuild()
        await safe_edit_interaction_message(interaction, content=self.builder.status_text(f"Attribute filter: **{self.builder.attribute_filter_label()}**."), view=self.builder)


class SupportDeckTypeFilterSelect(discord.ui.Select):
    PAGE_SIZE = 22

    def __init__(self, builder: "SupportDeckBuilderView"):
        self.builder = builder
        page_count = max(1, math.ceil(len(MONSTER_TYPES) / self.PAGE_SIZE))
        current_page = max(0, min(int(getattr(builder, "type_filter_page", 0)), page_count - 1))
        start_index = current_page * self.PAGE_SIZE
        end_index = start_index + self.PAGE_SIZE
        visible_types = MONSTER_TYPES[start_index:end_index]

        options = []
        if current_page > 0:
            options.append(discord.SelectOption(label="‹ Previous Type Page", value=f"typepage:{current_page - 1}"))
        if current_page < page_count - 1:
            options.append(discord.SelectOption(label="Next Type Page ›", value=f"typepage:{current_page + 1}"))
        options.append(discord.SelectOption(label="All Monster Types", value="__all__", default=builder.type_filter == "__all__"))
        options.extend(
            discord.SelectOption(label=monster_type, value=monster_type, default=builder.type_filter == monster_type)
            for monster_type in visible_types
        )
        placeholder = f"Filter by monster type ({current_page + 1}/{page_count})"
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value.startswith("typepage:"):
            try:
                self.builder.type_filter_page = int(value.split(":", 1)[1])
            except Exception:
                self.builder.type_filter_page = 0
            self.builder.rebuild()
            await interaction.response.edit_message(content="Choose a monster type filter.", view=SupportDeckTypeFilterView(self.builder))
            return

        self.builder.type_filter = value
        self.builder.page = 0
        self.builder.rebuild()
        await safe_edit_interaction_message(interaction, content=self.builder.status_text(f"Monster type filter: **{self.builder.type_filter_label()}**."), view=self.builder)


class SupportDeckSpecialFilterSelect(discord.ui.Select):
    PAGE_SIZE = 20

    def __init__(self, builder: "SupportDeckBuilderView"):
        self.builder = builder
        special_cards = []
        for support_card_id in SPECIAL_SUPPORT_IDS:
            card = get_support_card_def(support_card_id)
            if card is not None:
                special_cards.append(card)

        page_count = max(1, math.ceil(len(special_cards) / self.PAGE_SIZE))
        current_page = max(0, min(int(getattr(builder, "special_filter_page", 0)), page_count - 1))
        start_index = current_page * self.PAGE_SIZE
        end_index = start_index + self.PAGE_SIZE
        visible_cards = special_cards[start_index:end_index]

        options = [
            discord.SelectOption(label="All Support Cards", value="__all__", default=builder.special_filter == "__all__"),
            discord.SelectOption(label="Special Support Cards", value="__special_all__", default=builder.special_filter == "__special_all__"),
            discord.SelectOption(label="Non-Special Support Cards", value="__non_special__", default=builder.special_filter == "__non_special__"),
        ]
        if current_page > 0:
            options.append(discord.SelectOption(label="← Previous Page", value=f"specialpage:{current_page - 1}"))
        for card in visible_cards:
            options.append(discord.SelectOption(label=card.name[:100], value=card.support_card_id, default=builder.special_filter == card.support_card_id))
        if current_page < page_count - 1:
            options.append(discord.SelectOption(label="Next Page →", value=f"specialpage:{current_page + 1}"))

        placeholder = "Filter special support cards"
        if page_count > 1:
            placeholder = f"Filter special support cards (Page {current_page + 1}/{page_count})"
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value.startswith("specialpage:"):
            try:
                self.builder.special_filter_page = int(value.split(":", 1)[1])
            except Exception:
                self.builder.special_filter_page = 0
            self.builder.rebuild()
            await interaction.response.edit_message(content="Choose a special support card filter.", view=SupportDeckSpecialFilterView(self.builder))
            return

        self.builder.special_filter = value
        self.builder.page = 0
        self.builder.rebuild()
        await safe_edit_interaction_message(interaction, content=self.builder.status_text(f"Special filter: **{self.builder.special_filter_label()}**."), view=self.builder)


class SupportDeckAttributeFilterView(discord.ui.View):
    def __init__(self, builder: "SupportDeckBuilderView"):
        super().__init__(timeout=180)
        self.add_item(SupportDeckAttributeFilterSelect(builder))


class SupportDeckTypeFilterView(discord.ui.View):
    def __init__(self, builder: "SupportDeckBuilderView"):
        super().__init__(timeout=180)
        self.add_item(SupportDeckTypeFilterSelect(builder))


class SupportDeckSpecialFilterView(discord.ui.View):
    def __init__(self, builder: "SupportDeckBuilderView"):
        super().__init__(timeout=180)
        self.add_item(SupportDeckSpecialFilterSelect(builder))


class SupportDeckSearchModal(discord.ui.Modal, title="Search Support Card"):
    card_name = discord.ui.TextInput(label="Card or target name", min_length=1, max_length=100, placeholder="Search by exact or partial card/target name")

    def __init__(self, builder: "SupportDeckBuilderView"):
        super().__init__()
        self.builder = builder
        if getattr(builder, "search_query", ""):
            self.card_name.default = str(builder.search_query)

    async def on_submit(self, interaction: discord.Interaction):
        raw_value = str(self.card_name).strip()
        normalized = raw_value.casefold()
        if normalized in {"all", "clear", "reset"}:
            self.builder.search_query = ""
            self.builder.page = 0
            self.builder.rebuild()
            await safe_edit_interaction_message(interaction, content=self.builder.status_text("Search cleared."), view=self.builder)
            return
        self.builder.search_query = raw_value
        self.builder.page = 0
        self.builder.rebuild()
        await safe_edit_interaction_message(interaction, content=self.builder.status_text(f"Search: **{raw_value}**."), view=self.builder)


class SupportDeckBuilderView(discord.ui.View):
    def __init__(self, user_id: int, deck_name: str, manager: Optional["SupportDeckManagerView"] = None, deck_id: Optional[int] = None, initial_picks: Optional[Sequence[str]] = None):
        super().__init__(timeout=600)
        self.user_id = user_id
        self.deck_name = deck_name
        self.inventory_rows = db.user_support_cards(user_id)
        self.manager = manager
        self.deck_id = deck_id
        self.page = 0
        self.picks: List[str] = list(initial_picks or [])[:SUPPORT_DECK_SIZE]
        self.sort_key = "name_asc"
        self.attribute_filter = "__all__"
        self.type_filter = "__all__"
        self.type_filter_page = 0
        self.special_filter = "__all__"
        self.special_filter_page = 0
        self.search_query = ""
        self.rebuild()

    def sort_label(self) -> str:
        return {
            "name_asc": "A-Z",
            "name_desc": "Z-A",
            "target_asc": "Target A-Z",
            "bonus_desc": "Highest Bonus",
            "newest": "Newest",
            "oldest": "Oldest",
        }.get(self.sort_key, "A-Z")

    def attribute_filter_label(self) -> str:
        if self.attribute_filter == "__all__":
            return "All Attributes"
        return str(self.attribute_filter).title()

    def type_filter_label(self) -> str:
        if self.type_filter == "__all__":
            return "All Monster Types"
        return str(self.type_filter)

    def special_filter_label(self) -> str:
        if self.special_filter == "__all__":
            return "All Support Cards"
        if self.special_filter == "__special_all__":
            return "Special Support Cards"
        if self.special_filter == "__non_special__":
            return "Non-Special Support Cards"
        card = get_support_card_def(self.special_filter)
        return card.name if card else "Special Support Cards"

    def status_text(self, extra: str = "") -> str:
        filtered_count = len(self.filtered_inventory_rows())
        max_page = max(1, math.ceil(max(1, filtered_count) / 10))
        base = (
            f"Deck **{self.deck_name}** picks ({len(self.picks)}/{SUPPORT_DECK_SIZE}): {self.picks_text()}\n"
            f"Sort: **{self.sort_label()}** • Attribute: **{self.attribute_filter_label()}** • "
            f"Type: **{self.type_filter_label()}** • Special: **{self.special_filter_label()}**\n"
            f"Showing **{filtered_count}** cards • Page **{self.page + 1}/{max_page}**"
        )
        if extra:
            return base + f"\n{extra}"
        return base

    def row_card(self, row: sqlite3.Row) -> Optional[SupportCardDef]:
        return get_support_card_def(str(row["support_card_id"]))

    def row_target_name(self, row: sqlite3.Row) -> str:
        card = self.row_card(row)
        if card is None:
            return "Unknown"
        if str(card.stat_type).upper() == "SPECIAL":
            return "Special"
        return str(card.attribute)

    def row_is_special(self, row: sqlite3.Row) -> bool:
        card = self.row_card(row)
        if card is None:
            return False
        return str(card.attribute).upper() == "SPECIAL" or str(card.stat_type).upper() == "SPECIAL"

    def filtered_inventory_rows(self) -> List[sqlite3.Row]:
        rows: List[sqlite3.Row] = []
        for row in self.inventory_rows:
            card = self.row_card(row)
            if card is None:
                continue
            if self.attribute_filter != "__all__":
                if self.row_is_special(row) or str(card.attribute).upper() != str(self.attribute_filter).upper():
                    continue
            if self.type_filter != "__all__":
                if self.row_is_special(row) or str(card.attribute).strip().lower() != str(self.type_filter).strip().lower():
                    continue
            if self.special_filter == "__special_all__" and not self.row_is_special(row):
                continue
            if self.special_filter == "__non_special__" and self.row_is_special(row):
                continue
            if self.special_filter not in {"__all__", "__special_all__", "__non_special__"} and str(card.support_card_id) != str(self.special_filter):
                continue
            if str(self.search_query).strip():
                query = str(self.search_query).casefold().strip()
                haystack = f"{card.name} {self.row_target_name(row)} {card.attribute} {card.stat_type}".casefold()
                tokens = [token for token in re.split(r"\s+", query) if token]
                if query not in haystack and not all(token in haystack for token in tokens):
                    continue
            rows.append(row)
        return self.sorted_inventory_rows(rows)

    def sorted_inventory_rows(self, rows: Optional[Sequence[sqlite3.Row]] = None) -> List[sqlite3.Row]:
        working_rows = list(rows if rows is not None else self.inventory_rows)

        def key_name_asc(row: sqlite3.Row):
            card = self.row_card(row)
            return (
                str(card.name if card else row["support_card_id"]).lower(),
                str(self.row_target_name(row)).lower(),
                str(row["instance_id"]),
            )

        def key_target_asc(row: sqlite3.Row):
            card = self.row_card(row)
            return (
                str(self.row_target_name(row)).lower(),
                str(card.name if card else row["support_card_id"]).lower(),
                str(row["instance_id"]),
            )

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

        def key_newest(row: sqlite3.Row):
            return (-safe_timestamp(row["obtained_at"]), str(row["instance_id"]))

        def key_oldest(row: sqlite3.Row):
            return (safe_timestamp(row["obtained_at"]), str(row["instance_id"]))

        if self.sort_key == "name_desc":
            return sorted(working_rows, key=key_name_asc, reverse=True)
        if self.sort_key == "target_asc":
            return sorted(working_rows, key=key_target_asc)
        if self.sort_key == "bonus_desc":
            return sorted(working_rows, key=key_bonus_desc)
        if self.sort_key == "stage_desc":
            return sorted(working_rows, key=key_stage_desc)
        if self.sort_key == "newest":
            return sorted(working_rows, key=key_newest)
        if self.sort_key == "oldest":
            return sorted(working_rows, key=key_oldest)
        return sorted(working_rows, key=key_name_asc)

    def page_rows(self) -> List[sqlite3.Row]:
        start = self.page * 10
        rows = self.filtered_inventory_rows()
        return rows[start:start + 10]

    def build_row_description(self, row: sqlite3.Row) -> str:
        card = self.row_card(row)
        if card is None:
            return "Unknown support card"
        if self.row_is_special(row):
            return f"Special • {card.name}"
        return f"{card.attribute} • +{card.bonus_pct}% {card.stat_type}"

    def picks_text(self) -> str:
        labels = []
        for instance_id in self.picks[:15]:
            row = db.get_support_card_instance(instance_id, self.user_id)
            card = get_support_card_def(row["support_card_id"]) if row else None
            if card:
                labels.append(card.name)
        return ", ".join(labels) if labels else "None yet"

    def add_pick(self, instance_id: str) -> str:
        if len(self.picks) >= SUPPORT_DECK_SIZE:
            return f"Your deck already has {SUPPORT_DECK_SIZE} cards."
        if instance_id in self.picks:
            return "That support card copy is already in this deck."
        row = db.get_support_card_instance(instance_id, self.user_id)
        if not row:
            return "You do not own that support card copy."
        card = get_support_card_def(row["support_card_id"])
        if not card:
            return "That support card no longer exists."
        picked_families = {
            support_card_family_key(str(existing_row["support_card_id"]))
            for picked_id in self.picks
            for existing_row in [db.get_support_card_instance(picked_id, self.user_id)]
            if existing_row is not None
        }
        card_family = support_card_family_key(card.support_card_id)
        if card_family in picked_families:
            return "That support card family is already in this deck. Support decks can only use unique cards."
        self.picks.append(instance_id)
        self.rebuild()
        unique_count = len(unique_support_card_ids_from_instance_ids(self.user_id, self.picks))
        return f"Added **{card.name}** ({unique_count}/{SUPPORT_DECK_SIZE})."

    def rebuild(self):
        self.clear_items()
        self.add_item(SupportDeckSortSelect(self))
        self.add_item(SupportCardSelect(self))
        max_page = max(0, math.ceil(max(1, len(self.filtered_inventory_rows())) / 10) - 1)
        self.page = min(self.page, max_page)
        self.add_item(self.filter_attribute_button)
        self.add_item(self.filter_type_button)
        self.add_item(self.filter_special_button)
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.search_button)
        self.add_item(self.save_button)
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= max_page
        self.save_button.disabled = not (SUPPORT_DECK_MIN_SIZE <= len(self.picks) <= SUPPORT_DECK_SIZE)

    @discord.ui.button(label="Attribute Filter", style=discord.ButtonStyle.primary, row=2)
    async def filter_attribute_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Choose an attribute filter.", view=SupportDeckAttributeFilterView(self), ephemeral=True)

    @discord.ui.button(label="Type Filter", style=discord.ButtonStyle.primary, row=2)
    async def filter_type_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Choose a monster type filter.", view=SupportDeckTypeFilterView(self), ephemeral=True)

    @discord.ui.button(label="Special Filter", style=discord.ButtonStyle.primary, row=2)
    async def filter_special_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Choose a special support card filter.", view=SupportDeckSpecialFilterView(self), ephemeral=True)

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=3)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.status_text(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=3)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_page = max(0, math.ceil(max(1, len(self.filtered_inventory_rows())) / 10) - 1)
        self.page = min(max_page, self.page + 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.status_text(), view=self)

    @discord.ui.button(label="Search", style=discord.ButtonStyle.primary, row=3)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SupportDeckSearchModal(self))

    @discord.ui.button(label="Save Deck", style=discord.ButtonStyle.success, row=3)
    async def save_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        unique_card_ids = unique_support_card_ids_from_instance_ids(self.user_id, self.picks)
        if not (SUPPORT_DECK_MIN_SIZE <= len(unique_card_ids) <= SUPPORT_DECK_SIZE):
            await interaction.response.send_message(f"You need between {SUPPORT_DECK_MIN_SIZE} and {SUPPORT_DECK_SIZE} unique support cards.", ephemeral=True)
            return
        if self.deck_id is not None:
            ok = db.update_support_deck_cards(self.user_id, self.deck_id, self.picks)
            action_word = "Updated"
        else:
            ok = db.create_support_deck(self.user_id, self.deck_name, self.picks)
            action_word = "Saved"
        if not ok:
            await interaction.response.send_message(f"Could not save that deck. Make sure it contains between {SUPPORT_DECK_MIN_SIZE} and {SUPPORT_DECK_SIZE} unique support cards.", ephemeral=True)
            return
        await safe_edit_interaction_message(interaction, content=f"{action_word} deck **{self.deck_name}** with {len(unique_card_ids)} unique cards. Attach it to a team from that team's Deck or Swap Deck button.", view=None)
        if self.manager is not None:
            await self.manager.refresh_deck_browser_message()
        self.stop()


class SupportDeckAutoNameModal(discord.ui.Modal, title="Auto Create Support Deck"):
    deck_name = discord.ui.TextInput(label="Deck name", min_length=2, max_length=30, placeholder="Name the auto-built deck")

    def __init__(self, parent: "SupportDeckManagerView"):
        super().__init__()
        self.parent_view = parent

    async def on_submit(self, interaction: discord.Interaction):
        deck_name = str(self.deck_name).strip()
        teams = db.list_teams(self.parent_view.user_id)
        if not teams:
            await interaction.response.send_message("Create a team first so Auto Create has something to build around.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Choose the team for **{deck_name}**. Auto Create will only use support cards relevant to that team.",
            view=PaginatedAutoDeckTeamView(self.parent_view, deck_name, owner_id=interaction.user.id),
            ephemeral=True,
        )


class AutoDeckTeamSelect(discord.ui.Select):
    PAGE_SIZE = 25

    def __init__(self, parent: "PaginatedAutoDeckTeamView"):
        self.parent_view = parent
        teams = db.list_teams(parent.manager.user_id)
        start = parent.page * self.PAGE_SIZE
        selected_teams = teams[start:start + self.PAGE_SIZE]
        options = [
            discord.SelectOption(
                label=f"{team['name']} (#{team['id']})",
                value=str(team['id']),
                description=", ".join(display_monster_label_from_id(mid) for mid in team_slots_from_csv(team["slots_csv"]))[:100] or "Empty team",
            )
            for team in selected_teams
        ] or [discord.SelectOption(label="No teams", value="0")]
        total_pages = max(1, math.ceil(max(1, len(teams)) / self.PAGE_SIZE))
        super().__init__(
            placeholder=f"Choose a team (Page {parent.page + 1}/{total_pages})",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            await safe_acknowledge_interaction(interaction)
            return
        team_id = int(self.values[0])
        result_text = self.parent_view.create_deck_from_team(team_id)
        await safe_edit_interaction_message(interaction, content=result_text, view=None)
        await self.parent_view.manager.refresh_deck_browser_message()


class PaginatedAutoDeckTeamView(discord.ui.View):
    def __init__(self, manager: "SupportDeckManagerView", deck_name: str, owner_id: int, page: int = 0):
        super().__init__(timeout=180)
        self.manager = manager
        self.deck_name = deck_name
        self.owner_id = owner_id
        self.page = max(0, int(page))
        self.teams = db.list_teams(manager.user_id)
        self.total_pages = max(1, math.ceil(max(1, len(self.teams)) / AutoDeckTeamSelect.PAGE_SIZE))
        self.page = min(self.page, self.total_pages - 1)
        self.rebuild_items()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This team list belongs to someone else.", ephemeral=True)
            return False
        return True

    def rebuild_items(self):
        self.clear_items()
        self.add_item(AutoDeckTeamSelect(self))
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.total_pages - 1
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)

    def create_deck_from_team(self, team_id: int) -> str:
        team_row = next((team for team in db.list_teams(self.manager.user_id) if int(team["id"]) == team_id), None)
        if team_row is None:
            return "That team could not be found."
        picks = auto_build_support_deck_instance_ids(self.manager.user_id, team_row)
        if len(picks) < SUPPORT_DECK_MIN_SIZE:
            return f"Could not auto-build **{self.deck_name}**. You do not own any support cards relevant to **{team_row['name']}**."
        if not db.create_support_deck(self.manager.user_id, self.deck_name, picks):
            return f"Could not auto-build **{self.deck_name}**. You need between {SUPPORT_DECK_MIN_SIZE} and {SUPPORT_DECK_SIZE} unique relevant support cards."
        preview = []
        for instance_id in picks[:5]:
            row = db.get_support_card_instance(instance_id, self.manager.user_id)
            card = get_support_card_def(row["support_card_id"]) if row else None
            if card:
                preview.append(card.name)
        return (
            f"Auto-created deck **{self.deck_name}** from team **{team_row['name']}** with **{len(picks)}** relevant unique cards. "
            f"Preview: {', '.join(preview) if preview else 'No preview'}."
        )

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=1)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.rebuild_items()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.total_pages - 1, self.page + 1)
        self.rebuild_items()
        await interaction.response.edit_message(view=self)


class SupportDeckDeleteSelect(discord.ui.Select):
    def __init__(self, user_id: int, manager: Optional["SupportDeckManagerView"] = None):
        self.user_id = user_id
        self.manager = manager
        decks = db.list_support_decks(user_id)
        options = [discord.SelectOption(label=f"{deck['name']} (#{deck['id']})", value=str(deck['id'])) for deck in decks] or [discord.SelectOption(label="No support decks", value="0")]
        super().__init__(placeholder="Choose a support deck to delete", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            await safe_acknowledge_interaction(interaction)
            return
        ok = db.delete_support_deck(self.user_id, int(self.values[0]))
        await safe_edit_interaction_message(interaction, content="Support deck deleted." if ok else "Could not delete support deck.", view=None)
        if ok and self.manager is not None:
            await self.manager.refresh_deck_browser_message()


class SupportDeckManagerView(discord.ui.View):
    def __init__(self, user_id: int, browser: Optional["DeckBrowserView"] = None, browser_message: Optional[discord.Message] = None):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.browser = browser
        self.browser_message = browser_message

    async def refresh_deck_browser_message(self):
        if self.browser is None or self.browser_message is None:
            return
        self.browser.rebuild()
        try:
            await self.browser_message.edit(content=self.browser.summary_text(), attachments=[await asyncio.to_thread(self.browser.make_file)], view=self.browser)
        except Exception:
            pass

    @discord.ui.button(label="Create", style=discord.ButtonStyle.success)
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if user_unique_support_card_count(self.user_id) < SUPPORT_DECK_SIZE:
            await interaction.response.send_message(f"You need at least {SUPPORT_DECK_SIZE} unique support cards before you can build a deck.", ephemeral=True)
            return
        await interaction.response.send_modal(SupportDeckNameModal(self))

    @discord.ui.button(label="Auto Create", style=discord.ButtonStyle.success)
    async def auto_create_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if user_unique_support_card_count(self.user_id) < SUPPORT_DECK_SIZE:
            await interaction.response.send_message(f"You need at least {SUPPORT_DECK_SIZE} unique support cards before you can auto-build a deck.", ephemeral=True)
            return
        await interaction.response.send_modal(SupportDeckAutoNameModal(self))

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Choose the support deck to delete.", view=SimpleSelectView(SupportDeckDeleteSelect(self.user_id, manager=self)), ephemeral=True)
