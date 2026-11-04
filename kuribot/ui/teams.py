"""Team builder, team editing and the team browser."""

from __future__ import annotations

import asyncio
import math
import re
import sqlite3
from typing import List, Optional, Sequence, Tuple

import discord

from kuribot.config import (
    ATTRIBUTES, EXTRA_DECK_SIZE, MONSTER_TYPES, TEAM_ACTIVE_MODE_LABELS, TEAM_SIZE,
)
from kuribot.cards.equip_spells import get_equip_spell_def
from kuribot.database import db
from kuribot.render.images import _PanelUserContext
from kuribot.discord_helpers import fit_discord_message_content
from kuribot.monsters import (
    category_title_for_monster, decode_extra_deck_monsters, decode_monster_types,
    display_monster_label_from_id, format_monster_types, is_boss_monster_row,
    is_signature_monster_row, resolve_row_combat_stats, team_slots_from_csv,
)
from kuribot.cards.support_decks import decode_support_cards
from kuribot.render.collection import render_team_page
from kuribot.ui.common import (
    safe_acknowledge_interaction, safe_edit_interaction_message, safe_send_interaction_message,
)
from kuribot.ui.collection import (
    build_monster_detail_embed, make_monster_detail_file, MonsterDetailView,
)


class TeamNameModal(discord.ui.Modal, title="Create Team"):
    team_name = discord.ui.TextInput(label="Team name", min_length=2, max_length=30, placeholder="Type a team name")

    def __init__(self, parent: "TeamManagerView"):
        super().__init__()
        self.parent_view = parent

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.pending_team_name = str(self.team_name).strip()
        builder = TeamBuilderView(self.parent_view.user_id, self.parent_view.pending_team_name, manager=self.parent_view)
        await interaction.response.send_message(
            builder.status_text(f"Pick {TEAM_SIZE} monsters you own."),
            view=builder,
            ephemeral=True,
        )
        try:
            builder.message = await interaction.original_response()
        except Exception:
            builder.message = None


class TeamRenameModal(discord.ui.Modal, title="Rename Team"):
    team_name = discord.ui.TextInput(label="Team name", min_length=2, max_length=30, placeholder="Type the new team name")

    def __init__(self, detail_view: "TeamDetailView", row: dict):
        super().__init__()
        self.detail_view = detail_view
        self.row = row
        self.team_name.default = str(row["name"])

    async def on_submit(self, interaction: discord.Interaction):
        new_name = str(self.team_name).strip()
        ok = db.update_team_name(self.detail_view.owner_id, int(self.row["team_id"]), new_name)
        if not ok:
            await interaction.response.send_message("Could not rename that team.", ephemeral=True)
            return
        self.detail_view.browser.rebuild()
        updated_row = self.detail_view.browser.row_by_team_id(int(self.row["team_id"]))
        if updated_row is None:
            await safe_edit_interaction_message(interaction, content="That team is no longer available.", embed=None, view=None)
            await self.detail_view.refresh_browser_message()
            return
        await safe_edit_interaction_message(interaction, embed=build_team_detail_embed(updated_row), view=TeamDetailView(self.detail_view.owner_id, self.detail_view.browser, updated_row))
        await self.detail_view.refresh_browser_message()


def monster_name_matches_query(monster_name: str, query: str) -> bool:
    normalized_name = str(monster_name or "").casefold()
    normalized_query = str(query or "").casefold().strip()
    if not normalized_query:
        return True
    if normalized_query in normalized_name:
        return True
    tokens = [token for token in re.split(r"\s+", normalized_query) if token]
    return bool(tokens) and all(token in normalized_name for token in tokens)


class MonsterSearchModal(discord.ui.Modal, title="Search Monster"):

    monster_name = discord.ui.TextInput(label="Monster name", min_length=1, max_length=100, placeholder="Search by exact or partial monster name")

    def __init__(self, builder: "TeamBuilderView"):
        super().__init__()
        self.builder = builder

    async def on_submit(self, interaction: discord.Interaction):
        query = str(self.monster_name).strip()
        normalized = query.casefold()
        if normalized in {"all", "clear", "reset"}:
            self.builder.search_query = ""
            self.builder.page = 0
            self.builder.rebuild()
            await interaction.response.send_message("Monster search cleared.", ephemeral=True)
            await self.builder.refresh_message("Monster search cleared.")
            return

        matches = [row for row in self.builder.inventory_rows if monster_name_matches_query(str(row["monster_name"]), query)]
        if not matches:
            await interaction.response.send_message("You do not own a monster matching that search.", ephemeral=True)
            return

        self.builder.search_query = query
        self.builder.page = 0
        if len(matches) == 1:
            result = self.builder.add_pick(str(matches[0]["instance_id"]))
        else:
            self.builder.rebuild()
            result = f"Found **{len(matches)}** monsters matching **{query}**. Choose one from the dropdown on this same team builder message."

        await interaction.response.send_message(result, ephemeral=True)
        await self.builder.refresh_message(result)


class TeamMemberSelect(discord.ui.Select):
    def __init__(self, builder: "TeamBuilderView"):
        self.builder = builder
        rows = builder.page_rows()
        options = [
            discord.SelectOption(
                label=row["monster_name"][:100],
                value=row["instance_id"],
                description=builder.build_row_description(row),
            )
            for row in rows
        ] or [discord.SelectOption(label="No monsters on this page", value="__none__")]
        super().__init__(placeholder="Choose a monster to add", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        if selected == "__none__":
            await safe_acknowledge_interaction(interaction)
            return
        self.builder.message = interaction.message
        result = self.builder.add_pick(selected)
        await safe_edit_interaction_message(interaction, content=self.builder.status_text(result), view=self.builder)


class TeamBuilderSortSelect(discord.ui.Select):
    def __init__(self, builder: "TeamBuilderView"):
        self.builder = builder
        options = [
            discord.SelectOption(label="A-Z", value="name_asc", default=builder.sort_key == "name_asc"),
            discord.SelectOption(label="Z-A", value="name_desc", default=builder.sort_key == "name_desc"),
            discord.SelectOption(label="HP", value="hp", default=builder.sort_key == "hp"),
            discord.SelectOption(label="ATK", value="atk", default=builder.sort_key == "atk"),
            discord.SelectOption(label="DEF", value="def", default=builder.sort_key == "def"),
            discord.SelectOption(label="SPD", value="spd", default=builder.sort_key == "spd"),
        ]
        super().__init__(placeholder="Sort monsters", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        self.builder.sort_key = self.values[0]
        self.builder.page = 0
        self.builder.rebuild()
        self.builder.message = interaction.message
        await safe_edit_interaction_message(interaction, 
            content=self.builder.status_text(f"Sorting by **{self.builder.sort_key.upper()}**."),
            view=self.builder,
        )


class TeamBuilderFilterSelect(discord.ui.Select):
    def __init__(self, builder: "TeamBuilderView"):
        self.builder = builder
        options = [
            discord.SelectOption(label="All Monsters", value="all", default=builder.filter_key == "all"),
            *[discord.SelectOption(label=attribute.title(), value=f"attr:{attribute}", default=builder.filter_key == f"attr:{attribute}") for attribute in ATTRIBUTES],
            discord.SelectOption(label="Single-Type", value="single", default=builder.filter_key == "single"),
            discord.SelectOption(label="Dual-Type", value="dual", default=builder.filter_key == "dual"),
            discord.SelectOption(label="Extra Deck Monster", value="boss", default=builder.filter_key == "boss"),
        ]
        super().__init__(placeholder="General Filter", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.builder.filter_key = self.values[0]
        self.builder.page = 0
        self.builder.rebuild()
        self.builder.message = interaction.message
        await safe_edit_interaction_message(
            interaction,
            content=self.builder.status_text(f"Filter: **{self.builder.filter_label()}**."),
            view=self.builder,
        )


class TeamBuilderTypeFilterSelect(discord.ui.Select):
    PAGE_SIZE = 20

    def __init__(self, builder: "TeamBuilderView"):
        self.builder = builder
        page_count = max(1, math.ceil(len(MONSTER_TYPES) / self.PAGE_SIZE))
        current_page = max(0, min(int(builder.monster_type_filter_page), page_count - 1))
        start_index = current_page * self.PAGE_SIZE
        visible_types = MONSTER_TYPES[start_index:start_index + self.PAGE_SIZE]
        options: List[discord.SelectOption] = []
        if current_page > 0:
            options.append(discord.SelectOption(label="‹ Previous Type Page", value=f"typepage:{current_page - 1}"))
        if current_page < page_count - 1:
            options.append(discord.SelectOption(label="Next Type Page ›", value=f"typepage:{current_page + 1}"))
        options.append(discord.SelectOption(label="No Type Filter", value="all", default=builder.type_filter_key == "all"))
        options.extend(
            discord.SelectOption(label=monster_type[:100], value=f"type:{monster_type}", default=builder.type_filter_key == f"type:{monster_type}")
            for monster_type in visible_types
        )
        super().__init__(placeholder=f"Monster Type Filter ({current_page + 1}/{page_count})", min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value.startswith("typepage:"):
            try:
                self.builder.monster_type_filter_page = int(value.split(":", 1)[1])
            except Exception:
                self.builder.monster_type_filter_page = 0
        else:
            self.builder.type_filter_key = value
        self.builder.page = 0
        self.builder.rebuild()
        self.builder.message = interaction.message
        await safe_edit_interaction_message(
            interaction,
            content=self.builder.status_text(f"Type filter: **{self.builder.type_filter_label()}**."),
            view=self.builder,
        )


def team_deck_display_name(user_id: int, deck_id: Optional[int]) -> str:
    if deck_id is None:
        return "No Deck"
    try:
        deck = db.get_support_deck(user_id, int(deck_id))
    except Exception:
        deck = None
    return str(deck["name"]) if deck else "No Deck"


def team_extra_deck_display_name(user_id: int, deck_id: Optional[int]) -> str:
    if deck_id is None:
        return "No Extra Deck"
    try:
        deck = db.get_extra_deck(user_id, int(deck_id)) if hasattr(db, "get_extra_deck") else None
    except Exception:
        deck = None
    return str(deck["name"]) if deck else "No Extra Deck"


def build_team_mode_embed(team_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="Choose this team's active battle modes",
        description=(
            "Teams are no longer universally active. Select every battle mode this team should own. Selecting a mode here moves that mode away from any other team; leaving a mode unchecked removes it from this team.\n\n"
            "**Campaign** is used for story duels.\n"
            "**Ranked** is used for ranked Dueling Grounds matches.\n"
            "**Arena Offense** is used when you attack in Arena.\n"
            "**Lobby** is used for Lobby and normal Dueling Grounds battles.\n"
            "**Guild Boss** is used for guild boss fights."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"Team: {team_name}")
    return embed


class TeamDeckSelect(discord.ui.Select):
    PAGE_SIZE = 25

    def __init__(self, view: "PaginatedTeamDeckSelectView"):
        self.deck_view = view
        decks = db.list_support_decks(view.user_id)
        start = view.page * self.PAGE_SIZE
        visible = decks[start:start + self.PAGE_SIZE]
        options: List[discord.SelectOption] = []
        options.append(discord.SelectOption(label="No Deck", value="0", description="Use this team without a support deck", default=view.current_deck_id is None))
        for deck in visible[:24]:
            card_count = len(decode_support_cards(deck["cards_json"]))
            options.append(discord.SelectOption(
                label=f"{deck['name']} (#{deck['id']})"[:100],
                value=str(deck["id"]),
                description=f"{card_count} support cards"[:100],
                default=(view.current_deck_id == int(deck["id"])),
            ))
        super().__init__(placeholder="Choose a deck for this team", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        raw = self.values[0]
        deck_id = None if raw == "0" else int(raw)
        if self.deck_view.builder is not None:
            self.deck_view.builder.selected_deck_id = deck_id
            self.deck_view.builder.rebuild()
            picks_text = ', '.join(display_monster_label_from_id(mid) for mid in self.deck_view.builder.picks) if self.deck_view.builder.picks else 'None yet'
            await safe_edit_interaction_message(
                interaction,
                content=f"Team **{self.deck_view.builder.team_name}** picks: {picks_text}\nDeck: **{team_deck_display_name(self.deck_view.user_id, deck_id)}**",
                view=self.deck_view.builder,
            )
            return
        if self.deck_view.team_id is None:
            await interaction.response.send_message("No team was selected for this deck change.", ephemeral=True)
            return
        ok = db.update_team_deck(self.deck_view.user_id, int(self.deck_view.team_id), deck_id)
        if not ok:
            await interaction.response.send_message("Could not update that team's deck.", ephemeral=True)
            return
        if self.deck_view.browser is not None:
            self.deck_view.browser.rebuild()
        await safe_edit_interaction_message(interaction, content=f"Team deck updated to **{team_deck_display_name(self.deck_view.user_id, deck_id)}**.", view=None)
        if self.deck_view.browser is not None and getattr(self.deck_view.browser, "message", None) is not None:
            try:
                await self.deck_view.browser.message.edit(content=self.deck_view.browser.summary_text(), attachments=[await asyncio.to_thread(self.deck_view.browser.make_file)], view=self.deck_view.browser)
            except Exception:
                pass


class PaginatedTeamDeckSelectView(discord.ui.View):
    def __init__(self, user_id: int, *, builder: Optional["TeamBuilderView"] = None, team_id: Optional[int] = None, browser: Optional["TeamBrowserView"] = None, current_deck_id: Optional[int] = None, owner_id: Optional[int] = None, page: int = 0):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.builder = builder
        self.team_id = team_id
        self.browser = browser
        self.owner_id = int(owner_id if owner_id is not None else user_id)
        self.page = max(0, int(page))
        self.current_deck_id = current_deck_id
        self.total_pages = max(1, math.ceil(max(1, len(db.list_support_decks(user_id))) / TeamDeckSelect.PAGE_SIZE))
        self.page = min(self.page, self.total_pages - 1)
        self.rebuild_items()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This deck picker belongs to someone else.", ephemeral=True)
            return False
        return True

    def rebuild_items(self):
        self.clear_items()
        self.add_item(TeamDeckSelect(self))
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.total_pages - 1
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)

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


class TeamExtraDeckSelect(discord.ui.Select):
    PAGE_SIZE = 25

    def __init__(self, view: "PaginatedTeamExtraDeckSelectView"):
        self.deck_view = view
        decks = db.list_extra_decks(view.user_id) if hasattr(db, "list_extra_decks") else []
        start = view.page * self.PAGE_SIZE
        visible = decks[start:start + self.PAGE_SIZE]
        options: List[discord.SelectOption] = []
        options.append(discord.SelectOption(label="No Extra Deck", value="0", description="Use this team without an Extra Deck", default=view.current_extra_deck_id is None))
        for deck in visible[:24]:
            monster_count = len(decode_extra_deck_monsters(str(deck["monsters_json"])))
            options.append(discord.SelectOption(
                label=f"{deck['name']} (#{deck['id']})"[:100],
                value=str(deck["id"]),
                description=f"{monster_count}/{EXTRA_DECK_SIZE} Extra Deck Monsters"[:100],
                default=(view.current_extra_deck_id == int(deck["id"])),
            ))
        super().__init__(placeholder="Choose an Extra Deck for this team", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        raw = self.values[0]
        deck_id = None if raw == "0" else int(raw)
        if self.deck_view.builder is not None:
            self.deck_view.builder.selected_extra_deck_id = deck_id
            self.deck_view.builder.rebuild()
            await safe_edit_interaction_message(
                interaction,
                content=self.deck_view.builder.status_text(f"Extra Deck: **{team_extra_deck_display_name(self.deck_view.user_id, deck_id)}**"),
                view=self.deck_view.builder,
            )
            return
        if self.deck_view.team_id is None:
            await interaction.response.send_message("No team was selected for this Extra Deck change.", ephemeral=True)
            return
        ok = db.update_team_extra_deck(self.deck_view.user_id, int(self.deck_view.team_id), deck_id)
        if not ok:
            await interaction.response.send_message("Could not update that team's Extra Deck.", ephemeral=True)
            return
        if self.deck_view.browser is not None:
            self.deck_view.browser.rebuild()
        await safe_edit_interaction_message(interaction, content=f"Team Extra Deck updated to **{team_extra_deck_display_name(self.deck_view.user_id, deck_id)}**.", view=None)
        if self.deck_view.browser is not None and getattr(self.deck_view.browser, "message", None) is not None:
            try:
                await self.deck_view.browser.message.edit(content=self.deck_view.browser.summary_text(), attachments=[await asyncio.to_thread(self.deck_view.browser.make_file)], view=self.deck_view.browser)
            except Exception:
                pass


class PaginatedTeamExtraDeckSelectView(discord.ui.View):
    def __init__(self, user_id: int, *, builder: Optional["TeamBuilderView"] = None, team_id: Optional[int] = None, browser: Optional["TeamBrowserView"] = None, current_extra_deck_id: Optional[int] = None, owner_id: Optional[int] = None, page: int = 0):
        super().__init__(timeout=180)
        self.user_id = int(user_id)
        self.builder = builder
        self.team_id = team_id
        self.browser = browser
        self.owner_id = int(owner_id if owner_id is not None else user_id)
        self.page = max(0, int(page))
        self.current_extra_deck_id = current_extra_deck_id
        decks = db.list_extra_decks(user_id) if hasattr(db, "list_extra_decks") else []
        self.total_pages = max(1, math.ceil(max(1, len(decks)) / TeamExtraDeckSelect.PAGE_SIZE))
        self.page = min(self.page, self.total_pages - 1)
        self.rebuild_items()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This Extra Deck picker belongs to someone else.", ephemeral=True)
            return False
        return True

    def rebuild_items(self):
        self.clear_items()
        self.add_item(TeamExtraDeckSelect(self))
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.total_pages - 1
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)

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


class TeamActiveModeSelect(discord.ui.Select):
    def __init__(self, user_id: int, team_id: int, team_name: str, manager: Optional["TeamManagerView"] = None, browser: Optional["TeamBrowserView"] = None):
        self.user_id = user_id
        self.team_id = int(team_id)
        self.team_name = team_name
        self.manager = manager
        self.browser = browser
        current_modes = set(db.get_team_active_modes(user_id, int(team_id)))
        options = [
            discord.SelectOption(
                label=label,
                value=mode,
                description=f"Use this team for {label}",
                default=(mode in current_modes),
            )
            for mode, label in TEAM_ACTIVE_MODE_LABELS.items()
        ]
        super().__init__(
            placeholder="Choose active battle modes",
            min_values=0,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        selected_modes = list(self.values)
        try:
            ok = db.set_team_active_modes(self.user_id, self.team_id, selected_modes)
        except ValueError as exc:
            if str(exc).startswith("duplicate_equip_spell:"):
                spell = get_equip_spell_def(str(exc).split(":", 1)[1])
                name = spell.name if spell else "that equip spell"
                await interaction.response.send_message(f"That team cannot be used for these modes because it contains duplicate equip spells: **{name}**.", ephemeral=True)
                return
            raise
        labels = [TEAM_ACTIVE_MODE_LABELS.get(mode, mode.title()) for mode in selected_modes]
        label_text = ", ".join(labels) if labels else "no active battle modes"
        await safe_edit_interaction_message(
            interaction,
            content=(f"**{self.team_name}** is now assigned to **{label_text}**." if ok else "Could not update those battle modes."),
            embed=None,
            view=None,
        )
        if ok and self.manager is not None:
            await self.manager.refresh_team_browser_message()
        if ok and self.browser is not None:
            self.browser.rebuild()
            if getattr(self.browser, "message", None) is not None:
                try:
                    await self.browser.message.edit(content=self.browser.summary_text(), attachments=[await asyncio.to_thread(self.browser.make_file)], view=self.browser)
                except Exception:
                    pass


class TeamPostSaveActiveModeView(discord.ui.View):
    def __init__(self, user_id: int, team_id: int, team_name: str, manager: Optional["TeamManagerView"] = None, browser: Optional["TeamBrowserView"] = None):
        super().__init__(timeout=300)
        self.add_item(TeamActiveModeSelect(user_id, team_id, team_name, manager=manager, browser=browser))


class TeamBuilderView(discord.ui.View):
    def __init__(
        self,
        user_id: int,
        team_name: str,
        manager: Optional["TeamManagerView"] = None,
        team_id: Optional[int] = None,
        initial_picks: Optional[Sequence[str]] = None,
        detail_view: Optional["TeamDetailView"] = None,
        inventory_rows: Optional[Sequence[sqlite3.Row]] = None,
        save_callback: Optional[object] = None,
        selected_deck_id: Optional[int] = None,
        selected_extra_deck_id: Optional[int] = None,
    ):
        super().__init__(timeout=600)
        self.user_id = user_id
        self.team_name = team_name
        self.manager = manager
        self.team_id = team_id
        self.detail_view = detail_view
        self.save_callback = save_callback
        self.inventory_rows = list(inventory_rows) if inventory_rows is not None else db.user_inventory(user_id)
        self.sort_key = "name_asc"
        self.filter_key = "all"
        self.type_filter_key = "all"
        self.monster_type_filter_page = 0
        self.search_query = ""
        self.page = 0
        self.picks: List[str] = list(initial_picks or [])[:TEAM_SIZE]
        self.selected_deck_id = selected_deck_id
        self.selected_extra_deck_id = selected_extra_deck_id
        self.message: Optional[discord.Message] = None
        self.rebuild()

    def apply_filters(self, rows: List[sqlite3.Row]) -> List[sqlite3.Row]:
        filtered = list(rows)
        if self.filter_key.startswith("attr:"):
            attr = self.filter_key.split(":", 1)[1]
            filtered = [row for row in filtered if str(row["attribute"]) == attr]
        elif self.filter_key == "single":
            filtered = [row for row in filtered if len(decode_monster_types(row["monster_types"])) == 1]
        elif self.filter_key == "dual":
            filtered = [row for row in filtered if len(decode_monster_types(row["monster_types"])) > 1]
        elif self.filter_key == "boss":
            filtered = [row for row in filtered if is_boss_monster_row(row)]
        elif self.filter_key == "signature":
            filtered = [row for row in filtered if is_signature_monster_row(row)]
        elif self.filter_key == "boss":
            filtered = [row for row in filtered if is_boss_monster_row(row)]
        elif self.filter_key == "signature":
            filtered = [row for row in filtered if is_signature_monster_row(row)]
        if self.type_filter_key.startswith("type:"):
            wanted_type = self.type_filter_key.split(":", 1)[1]
            filtered = [row for row in filtered if wanted_type in decode_monster_types(row["monster_types"])]
        if self.search_query.strip():
            query = self.search_query.strip()
            filtered = [row for row in filtered if monster_name_matches_query(str(row["monster_name"]), query)]
        return filtered

    def sorted_inventory_rows(self) -> List[sqlite3.Row]:
        rows = self.apply_filters(self.inventory_rows)
        if self.sort_key == "name_desc":
            return sorted(
                rows,
                key=lambda row: (
                    str(row["monster_name"]).lower(),
                    str(row["attribute"]).lower(),
                    str(row["instance_id"]),
                ),
                reverse=True,
            )
        if self.sort_key == "name_asc":
            return sorted(
                rows,
                key=lambda row: (
                    str(row["monster_name"]).lower(),
                    str(row["attribute"]).lower(),
                    str(row["instance_id"]),
                ),
            )
        stat_index = {"hp": 0, "atk": 1, "def": 2, "spd": 3}.get(self.sort_key, 0)
        return sorted(
            rows,
            key=lambda row: (
                -resolve_row_combat_stats(row)[stat_index],
                str(row["monster_name"]).lower(),
                str(row["attribute"]).lower(),
                str(row["instance_id"]),
            ),
        )

    def page_rows(self) -> List[sqlite3.Row]:
        start = self.page * 10
        rows = self.sorted_inventory_rows()
        return rows[start:start + 10]

    def build_row_description(self, row: sqlite3.Row) -> str:
        hp, atk, defense, spd = resolve_row_combat_stats(row)
        details = f"{row['attribute']} • {format_monster_types(decode_monster_types(row['monster_types']))}"
        category_title = category_title_for_monster(str(row["monster_name"]))
        if category_title:
            details += f" • {category_title}"
        if self.sort_key in {"hp", "atk", "def", "spd"}:
            stat_value = {"hp": hp, "atk": atk, "def": defense, "spd": spd}[self.sort_key]
            details += f" • {self.sort_key.upper()} {stat_value}"
        return details

    def filter_label(self) -> str:
        if self.filter_key == "all":
            return "All Monsters"
        if self.filter_key == "single":
            return "Single-Type"
        if self.filter_key == "dual":
            return "Dual-Type"
        if self.filter_key == "boss":
            return "Extra Deck Monster"
        if self.filter_key.startswith("attr:"):
            return self.filter_key.split(":", 1)[1].title()
        return "All Monsters"

    def type_filter_label(self) -> str:
        if self.type_filter_key == "all":
            return "No Type Filter"
        if self.type_filter_key.startswith("type:"):
            return self.type_filter_key.split(":", 1)[1]
        return "No Type Filter"

    def search_label(self) -> str:
        return self.search_query if self.search_query.strip() else "None"

    def status_text(self, extra: str = "") -> str:
        picks_text = ', '.join(display_monster_label_from_id(mid) for mid in self.picks) if self.picks else 'None yet'
        total_rows = len(self.sorted_inventory_rows())
        total_pages = max(1, math.ceil(max(1, total_rows) / 10))
        deck_text = team_deck_display_name(self.user_id, self.selected_deck_id)
        extra_deck_text = team_extra_deck_display_name(self.user_id, self.selected_extra_deck_id)
        base = (
            f"Team **{self.team_name}** picks ({len(self.picks)}/{TEAM_SIZE}): {picks_text}\n"
            f"Deck: **{deck_text}** • Extra Deck: **{extra_deck_text}**\n"
            f"Filter: **{self.filter_label()}** • Type: **{self.type_filter_label()}** • Search: **{self.search_label()}** • Sort: **{self.sort_key.upper()}** • Page **{self.page + 1}/{total_pages}**"
        )
        if extra:
            return base + f"\n{extra}"
        return base

    async def refresh_message(self, extra: str = ""):
        if self.message is None:
            return
        try:
            await self.message.edit(content=self.status_text(extra), view=self)
        except Exception:
            pass

    def add_pick(self, instance_id: str) -> str:
        label = display_monster_label_from_id(instance_id)
        if len(self.picks) >= TEAM_SIZE:
            return f"Your team already has {TEAM_SIZE} monsters. Save it or remove one by reopening the builder."
        if instance_id in self.picks:
            return f"**{label}** is already in this team. Current picks: {', '.join(display_monster_label_from_id(mid) for mid in self.picks)}"
        self.picks.append(instance_id)
        self.rebuild()
        if len(self.picks) == TEAM_SIZE:
            return f"Added **{label}**. Team ready: {', '.join(display_monster_label_from_id(mid) for mid in self.picks)}"
        return f"Added **{label}**. Current picks ({len(self.picks)}/{TEAM_SIZE}): {', '.join(display_monster_label_from_id(mid) for mid in self.picks)}"

    def rebuild(self):
        self.clear_items()
        self.add_item(TeamBuilderSortSelect(self))
        self.add_item(TeamBuilderFilterSelect(self))
        self.add_item(TeamBuilderTypeFilterSelect(self))

        team_ready = len(self.picks) == TEAM_SIZE
        # Discord views only allow 5 rows, and each select menu uses the full row width.
        # The team builder already needs 3 filter/sort select rows plus one action row
        # for Prev/Next/Search/Deck/Extra Deck. When the team is incomplete, row 3 is
        # used by the monster picker. Once the team is complete, swap that picker for
        # Save Team so row 3 never exceeds Discord's width limit.
        if team_ready:
            self.add_item(self.save_button)
        else:
            self.add_item(TeamMemberSelect(self))

        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.search_button)
        self.add_item(self.deck_button)
        self.add_item(self.extra_deck_button)

        total_rows = len(self.sorted_inventory_rows())
        max_page = max(0, math.ceil(max(1, total_rows) / 10) - 1)
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= max_page
        self.save_button.disabled = not team_ready

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=4)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.rebuild()
        self.message = interaction.message
        await safe_edit_interaction_message(interaction, content=self.status_text(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=4)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_page = max(0, math.ceil(max(1, len(self.sorted_inventory_rows())) / 10) - 1)
        self.page = min(max_page, self.page + 1)
        self.rebuild()
        self.message = interaction.message
        await safe_edit_interaction_message(interaction, content=self.status_text(), view=self)

    @discord.ui.button(label="Search", style=discord.ButtonStyle.primary, row=4)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.message = interaction.message
        await interaction.response.send_modal(MonsterSearchModal(self))

    @discord.ui.button(label="Deck", style=discord.ButtonStyle.primary, row=4)
    async def deck_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Choose a deck to attach to this team.",
            view=PaginatedTeamDeckSelectView(self.user_id, builder=self, current_deck_id=self.selected_deck_id, owner_id=interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Extra Deck", style=discord.ButtonStyle.primary, row=4)
    async def extra_deck_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Choose an Extra Deck to attach to this team.",
            view=PaginatedTeamExtraDeckSelectView(self.user_id, builder=self, current_extra_deck_id=self.selected_extra_deck_id, owner_id=interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Save Team", style=discord.ButtonStyle.success, row=3)
    async def save_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.picks) != TEAM_SIZE:
            await interaction.response.send_message(f"You need exactly {TEAM_SIZE} monsters.", ephemeral=True)
            return
        team_text = ', '.join(display_monster_label_from_id(mid) for mid in self.picks)
        if self.team_id is None:
            new_team_id = db.create_team(self.user_id, self.team_name, self.picks, support_deck_id=self.selected_deck_id)
            if self.selected_extra_deck_id is not None:
                db.update_team_extra_deck(self.user_id, new_team_id, self.selected_extra_deck_id)
            deck_text = team_deck_display_name(self.user_id, self.selected_deck_id)
            extra_deck_text = team_extra_deck_display_name(self.user_id, self.selected_extra_deck_id)
            await safe_edit_interaction_message(interaction, content=f"Saved team **{self.team_name}** with: {team_text}\nDeck: **{deck_text}**\nExtra Deck: **{extra_deck_text}**", view=None)
            await interaction.followup.send(
                embed=build_team_mode_embed(self.team_name),
                view=TeamPostSaveActiveModeView(self.user_id, new_team_id, self.team_name, manager=self.manager, browser=(self.manager.browser if self.manager else None)),
                ephemeral=True,
            )
        else:
            try:
                ok = db.update_team_members(self.user_id, self.team_id, self.picks)
            except ValueError as exc:
                if str(exc).startswith("duplicate_equip_spell:"):
                    spell = get_equip_spell_def(str(exc).split(":", 1)[1])
                    name = spell.name if spell else "that equip spell"
                    await interaction.followup.send(f"That team would contain duplicate equip spells: **{name}**.", ephemeral=True)
                    return
                raise
            if not ok:
                await interaction.response.send_message("Could not update that team.", ephemeral=True)
                return
            await safe_edit_interaction_message(interaction, content=f"Updated team **{self.team_name}** with: {team_text}", view=None)
            if self.detail_view is not None:
                self.detail_view.browser.rebuild()
                updated_row = self.detail_view.browser.row_by_team_id(self.team_id)
                if updated_row is not None:
                    await interaction.followup.send(embed=build_team_detail_embed(updated_row), view=TeamDetailView(self.user_id, self.detail_view.browser, updated_row), ephemeral=True)
        if self.save_callback is not None:
            await self.save_callback(interaction, self)
        if self.manager is not None:
            await self.manager.refresh_team_browser_message()
        elif self.detail_view is not None:
            await self.detail_view.refresh_browser_message()
        self.stop()


class DeleteTeamSelect(discord.ui.Select):
    def __init__(self, user_id: int, manager: Optional["TeamManagerView"] = None):
        self.user_id = user_id
        self.manager = manager
        teams = db.list_teams(user_id)
        options = [
            discord.SelectOption(label=f"{t['name']} (#{t['id']})", value=str(t["id"]), description=", ".join(display_monster_label_from_id(mid) for mid in team_slots_from_csv(t["slots_csv"]))[:100])
            for t in teams
        ] or [discord.SelectOption(label="No teams", value="0")]
        super().__init__(placeholder="Choose a team to delete", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        if val == "0":
            await safe_acknowledge_interaction(interaction)
            return
        ok = db.delete_team(self.user_id, int(val))
        await safe_edit_interaction_message(interaction, content="Team deleted." if ok else "Could not delete team.", view=None)
        if ok and self.manager is not None:
            await self.manager.refresh_team_browser_message()


class SimpleSelectView(discord.ui.View):
    def __init__(self, select: discord.ui.Select):
        super().__init__(timeout=180)
        self.add_item(select)


class TeamManagerView(discord.ui.View):
    def __init__(self, user_id: int, browser: Optional["TeamBrowserView"] = None, browser_message: Optional[discord.Message] = None):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.pending_team_name = ""
        self.browser = browser
        self.browser_message = browser_message

    async def refresh_team_browser_message(self):
        if self.browser is None or self.browser_message is None:
            return
        self.browser.rebuild()
        try:
            await self.browser_message.edit(content=self.browser.summary_text(), attachments=[await asyncio.to_thread(self.browser.make_file)], view=self.browser)
        except Exception:
            pass

    @discord.ui.button(label="Create", style=discord.ButtonStyle.success)
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TeamNameModal(self))

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Choose the team to delete.", view=SimpleSelectView(DeleteTeamSelect(self.user_id, manager=self)), ephemeral=True)


class TeamEditView(discord.ui.View):
    def __init__(self, owner_id: int, browser: "TeamBrowserView", row: dict):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.browser = browser
        self.team_id = int(row["team_id"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This team belongs to someone else.", ephemeral=True)
            return False
        return True

    def current_row(self) -> Optional[dict]:
        self.browser.rebuild()
        return self.browser.row_by_team_id(self.team_id)

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.primary, row=0)
    async def rename_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.current_row()
        if row is None:
            await interaction.response.send_message("That team is no longer available.", ephemeral=True)
            return
        detail_view = TeamDetailView(self.owner_id, self.browser, row)
        await interaction.response.send_modal(TeamRenameModal(detail_view, row))

    @discord.ui.button(label="Swap Monster", style=discord.ButtonStyle.success, row=0)
    async def swap_monster_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.current_row()
        if row is None:
            await interaction.response.send_message("That team is no longer available.", ephemeral=True)
            return
        swap_view = TeamMonsterSwapView(self.owner_id, self.browser, row)
        await interaction.response.send_message(
            swap_view.status_text("Choose a team slot, filter/order your monsters, choose one you own, then apply the swap."),
            view=swap_view,
            ephemeral=True,
        )
        try:
            swap_view.message = await interaction.original_response()
        except Exception:
            swap_view.message = None

    @discord.ui.button(label="Swap Deck", style=discord.ButtonStyle.success, row=0)
    async def swap_deck_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.current_row()
        if row is None:
            await interaction.response.send_message("That team is no longer available.", ephemeral=True)
            return
        current_deck_id = row.get("support_deck_id")
        await interaction.response.send_message(
            "Choose the deck to attach to this team.",
            view=PaginatedTeamDeckSelectView(self.owner_id, team_id=self.team_id, browser=self.browser, current_deck_id=current_deck_id, owner_id=interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Extra Deck", style=discord.ButtonStyle.success, row=1)
    async def swap_extra_deck_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.current_row()
        if row is None:
            await interaction.response.send_message("That team is no longer available.", ephemeral=True)
            return
        current_extra_deck_id = row.get("extra_deck_id")
        await interaction.response.send_message(
            "Choose the Extra Deck to attach to this team.",
            view=PaginatedTeamExtraDeckSelectView(self.owner_id, team_id=self.team_id, browser=self.browser, current_extra_deck_id=current_extra_deck_id, owner_id=interaction.user.id),
            ephemeral=True,
        )

    @discord.ui.button(label="Equip", style=discord.ButtonStyle.danger, row=0)
    async def equip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.current_row()
        if row is None:
            await interaction.response.send_message("That team is no longer available.", ephemeral=True)
            return
        equip_view = TeamMonsterEquipView(self.owner_id, row)
        await interaction.response.send_message(
            equip_view.status_text("Choose a monster from this team to open its stat page."),
            view=equip_view,
            ephemeral=True,
        )

    @discord.ui.button(label="Battle Modes", style=discord.ButtonStyle.primary, row=1)
    async def battle_modes_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.current_row()
        if row is None:
            await interaction.response.send_message("That team is no longer available.", ephemeral=True)
            return
        team_name = str(row.get("name") or f"Team {self.team_id}")
        await interaction.response.send_message(
            embed=build_team_mode_embed(team_name),
            view=TeamPostSaveActiveModeView(self.owner_id, self.team_id, team_name, browser=self.browser),
            ephemeral=True,
        )


class TeamDetailView(discord.ui.View):
    def __init__(self, owner_id: int, browser: "TeamBrowserView", row: dict):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.browser = browser
        self.team_id = int(row["team_id"])
        current_row = self.browser.row_by_team_id(self.team_id) or row
        self.row = current_row

    async def _share_public_team_message(self, interaction: discord.Interaction, row: dict):
        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message("I couldn't find a channel to post this team in.", ephemeral=True)
            return
        public_embed = build_team_detail_embed(row)
        await channel.send(
            content=f"**{interaction.user.display_name}** shared team **{row['name']}**.",
            embed=public_embed,
        )
        if interaction.response.is_done():
            await interaction.followup.send("Team posted publicly.", ephemeral=True)
        else:
            await interaction.response.send_message("Team posted publicly.", ephemeral=True)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This team belongs to someone else.", ephemeral=True)
            return False
        return True

    async def refresh_browser_message(self):
        self.browser.rebuild()
        if getattr(self.browser, "message", None) is None:
            return
        try:
            await self.browser.message.edit(content=self.browser.summary_text(), attachments=[await asyncio.to_thread(self.browser.make_file)], view=self.browser)
        except Exception:
            pass

    def current_row(self) -> Optional[dict]:
        self.browser.rebuild()
        self.row = self.browser.row_by_team_id(self.team_id)
        return self.row


    @discord.ui.button(label="Edit", style=discord.ButtonStyle.success)
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_row = self.current_row()
        if current_row is None:
            await interaction.response.send_message("That team is no longer available.", ephemeral=True)
            await self.refresh_browser_message()
            return
        team_name = str(current_row.get("name") or f"Team {self.team_id}")
        await safe_edit_interaction_message(
            interaction,
            content=f"Editing **{team_name}**. Use **Battle Modes** to stack active roles, **Swap Monster** to replace a slot, **Equip** to open a team monster's stat page, or **Rename** to rename the team.",
            embed=None,
            attachments=[],
            view=TeamEditView(self.owner_id, self.browser, current_row),
        )

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.secondary)
    async def rename_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_row = self.current_row()
        if current_row is None:
            await interaction.response.send_message("That team is no longer available.", ephemeral=True)
            await self.refresh_browser_message()
            return
        await interaction.response.send_modal(TeamRenameModal(self, current_row))

    @discord.ui.button(label="Show Team", style=discord.ButtonStyle.secondary)
    async def show_team_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_row = self.current_row()
        if current_row is None:
            await interaction.response.send_message("That team is no longer available.", ephemeral=True)
            await self.refresh_browser_message()
            return
        await self._share_public_team_message(interaction, current_row)

    @discord.ui.button(label="Active", style=discord.ButtonStyle.success)
    async def active_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_row = self.current_row()
        if current_row is None:
            await interaction.response.send_message("That team is no longer available.", ephemeral=True)
            await self.refresh_browser_message()
            return
        team_name = str(current_row.get("name") or f"Team {self.team_id}")
        await interaction.response.send_message(
            embed=build_team_mode_embed(team_name),
            view=TeamPostSaveActiveModeView(self.owner_id, self.team_id, team_name, browser=self.browser),
            ephemeral=True,
        )

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok = db.delete_team(self.owner_id, self.team_id)
        if not ok:
            await interaction.response.send_message("Could not delete that team.", ephemeral=True)
            return
        self.browser.rebuild()
        await safe_edit_interaction_message(interaction, content="Team deleted.", embed=None, view=None)
        await self.refresh_browser_message()


class TeamMonsterEquipView(discord.ui.View):
    def __init__(self, owner_id: int, row: dict):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.team_id = int(row["team_id"])
        self.team_name = str(row.get("name") or f"Team {self.team_id}")
        member_ids = list(row.get("member_ids") or [])
        self.member_rows = [
            monster_row
            for monster_row in (db.get_inventory_instance(instance_id, owner_id) for instance_id in member_ids)
            if monster_row is not None
        ]
        self.add_item(TeamMonsterJumpSelect(self))

    def status_text(self, extra: str = "") -> str:
        lines = [f"Team **{self.team_name}**"]
        if self.member_rows:
            lines.append("Choose a monster to open its stat page.")
        else:
            lines.append("This team does not have any monsters right now.")
        if extra:
            lines.append(extra)
        return fit_discord_message_content("\n".join(lines))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This team belongs to someone else.", ephemeral=True)
            return False
        return True


class TeamMonsterJumpSelect(discord.ui.Select):
    def __init__(self, parent: "discord.ui.View"):
        self.parent_view = parent
        options: List[discord.SelectOption] = []
        for slot_index, monster_row in enumerate(parent.member_rows, start=1):
            if monster_row is None:
                continue
            spell_row = db.get_equipped_spell_for_monster(monster_row["instance_id"], parent.owner_id)
            spell = get_equip_spell_def(spell_row["equip_spell_id"]) if spell_row else None
            description = f"Slot {slot_index} • {monster_row['attribute']} • {format_monster_types(decode_monster_types(monster_row['monster_types']))}"
            if spell:
                description = f"{description} • {spell.name}"
            options.append(discord.SelectOption(
                label=str(monster_row["monster_name"])[:100],
                value=str(monster_row["instance_id"]),
                description=description[:100],
            ))
        if not options:
            options = [discord.SelectOption(label="No monsters in team", value="0")]
        super().__init__(placeholder="Choose a team monster", min_values=1, max_values=1, options=options, disabled=not bool(parent.member_rows))

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            await safe_acknowledge_interaction(interaction)
            return
        monster_row = db.get_inventory_instance(self.values[0], self.parent_view.owner_id)
        if monster_row is None:
            await interaction.response.send_message("That monster is no longer available.", ephemeral=True)
            return
        embed = build_monster_detail_embed(monster_row)
        detail_file = make_monster_detail_file(monster_row)
        kwargs = {
            "content": f"Viewing **{monster_row['monster_name']}** from **{self.parent_view.team_name}**.",
            "embed": embed,
            "view": MonsterDetailView(self.parent_view.owner_id, monster_row),
        }
        if detail_file is not None:
            kwargs["attachments"] = [detail_file]
        else:
            kwargs["attachments"] = []
        await safe_edit_interaction_message(interaction, **kwargs)


class TeamMonsterSwapSortSelect(discord.ui.Select):
    def __init__(self, swap_view: "TeamMonsterSwapView"):
        self.swap_view = swap_view
        options = [
            discord.SelectOption(label="A-Z", value="name_asc", default=swap_view.sort_key == "name_asc"),
            discord.SelectOption(label="Z-A", value="name_desc", default=swap_view.sort_key == "name_desc"),
            discord.SelectOption(label="Highest HP", value="hp_desc", default=swap_view.sort_key == "hp_desc"),
            discord.SelectOption(label="Highest ATK", value="atk_desc", default=swap_view.sort_key == "atk_desc"),
            discord.SelectOption(label="Highest DEF", value="def_desc", default=swap_view.sort_key == "def_desc"),
            discord.SelectOption(label="Highest SPD", value="spd_desc", default=swap_view.sort_key == "spd_desc"),
            discord.SelectOption(label="Newest", value="newest", default=swap_view.sort_key == "newest"),
            discord.SelectOption(label="Oldest", value="oldest", default=swap_view.sort_key == "oldest"),
        ]
        super().__init__(placeholder="Sort monsters", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        self.swap_view.sort_key = self.values[0]
        self.swap_view.page = 0
        self.swap_view.rebuild()
        await interaction.response.edit_message(content=f"Sorting by **{self.swap_view.sort_label()}**.", view=None)
        await self.swap_view.refresh_message(f"Sorting by **{self.swap_view.sort_label()}**.")


class TeamMonsterSwapSortView(discord.ui.View):
    def __init__(self, swap_view: "TeamMonsterSwapView"):
        super().__init__(timeout=180)
        self.swap_view = swap_view
        self.add_item(TeamMonsterSwapSortSelect(swap_view))


class TeamMonsterSwapSearchModal(discord.ui.Modal, title="Search Monster"):
    monster_name = discord.ui.TextInput(
        label="Monster name",
        min_length=1,
        max_length=100,
        placeholder="Type part of a monster name (use clear/reset/all to clear search)",
    )

    def __init__(self, swap_view: "TeamMonsterSwapView"):
        super().__init__()
        self.swap_view = swap_view
        if getattr(swap_view, "search_query", ""):
            self.monster_name.default = str(swap_view.search_query)

    async def on_submit(self, interaction: discord.Interaction):
        raw_value = str(self.monster_name).strip()
        normalized = raw_value.casefold()
        if normalized in {"all", "clear", "reset"}:
            self.swap_view.search_query = ""
            self.swap_view.page = 0
            self.swap_view.rebuild()
            await interaction.response.send_message("Monster search cleared.", ephemeral=True)
            await self.swap_view.refresh_message("Monster search cleared.")
            return

        matches = [
            row for row in self.swap_view.inventory_rows
            if monster_name_matches_query(str(row["monster_name"]), raw_value)
        ]
        if not matches:
            await interaction.response.send_message("No monsters matched that search.", ephemeral=True)
            return

        self.swap_view.search_query = raw_value
        self.swap_view.page = 0
        if len(matches) == 1:
            self.swap_view.selected_instance_id = str(matches[0]["instance_id"])
        self.swap_view.rebuild()
        match_word = "match" if len(matches) == 1 else "matches"
        selected_text = ""
        if len(matches) == 1:
            selected_text = f" Auto-selected **{display_monster_label_from_id(matches[0]['instance_id'])}**."
        await interaction.response.send_message(
            f"Found **{len(matches)}** {match_word} for **{raw_value}**. The swap menu now shows those search results with pagination.{selected_text}",
            ephemeral=True,
        )
        await self.swap_view.refresh_message(f"Search: **{raw_value}** • {len(matches)} {match_word}.")


class TeamMonsterSwapSlotSelect(discord.ui.Select):
    def __init__(self, view: "TeamMonsterSwapView"):
        self.swap_view = view
        options: List[discord.SelectOption] = []
        current_ids = view.current_member_ids()
        for idx in range(TEAM_SIZE):
            current_id = current_ids[idx] if idx < len(current_ids) else ""
            monster_row = db.get_inventory_instance(current_id, view.owner_id) if current_id else None
            label = f"Swap Slot {idx + 1}"
            description = display_monster_label_from_id(current_id) if monster_row is not None else "Empty slot"
            options.append(discord.SelectOption(label=label[:100], value=str(idx), description=description[:100], default=view.selected_slot == idx))
        super().__init__(placeholder="Choose a team slot", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        self.swap_view.selected_slot = int(self.values[0])
        self.swap_view.rebuild()
        await safe_edit_interaction_message(interaction, content=self.swap_view.status_text(f"Selected slot **{self.swap_view.selected_slot + 1}**."), view=self.swap_view)


class TeamMonsterSwapSelect(discord.ui.Select):
    def __init__(self, view: "TeamMonsterSwapView"):
        self.swap_view = view
        rows = view.page_rows()
        options: List[discord.SelectOption] = []
        for row in rows:
            options.append(discord.SelectOption(
                label=str(row["monster_name"])[:100],
                value=str(row["instance_id"]),
                description=view.build_row_description(row)[:100],
                default=view.selected_instance_id == str(row["instance_id"]),
            ))
        if not options:
            options = [discord.SelectOption(label="No monsters", value="0")]
        super().__init__(placeholder="Choose a replacement monster", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            await safe_acknowledge_interaction(interaction)
            return
        self.swap_view.selected_instance_id = str(self.values[0])
        self.swap_view.rebuild()
        selected_row = db.get_inventory_instance(self.swap_view.selected_instance_id, self.swap_view.owner_id)
        selected_name = display_monster_label_from_id(self.swap_view.selected_instance_id) if selected_row else "that monster"
        await safe_edit_interaction_message(interaction, content=self.swap_view.status_text(f"Selected **{selected_name}**."), view=self.swap_view)


class TeamMonsterSwapFilterSelect(discord.ui.Select):
    def __init__(self, view: "TeamMonsterSwapView"):
        self.swap_view = view
        options = [
            discord.SelectOption(label="All Monsters", value="all", default=view.filter_key == "all"),
            *[discord.SelectOption(label=attribute.title(), value=f"attr:{attribute}", default=view.filter_key == f"attr:{attribute}") for attribute in ATTRIBUTES],
            discord.SelectOption(label="Single-Type", value="single", default=view.filter_key == "single"),
            discord.SelectOption(label="Dual-Type", value="dual", default=view.filter_key == "dual"),
            discord.SelectOption(label="Extra Deck Monster", value="boss", default=view.filter_key == "boss"),
        ]
        super().__init__(placeholder="General Filter", min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        self.swap_view.filter_key = self.values[0]
        self.swap_view.page = 0
        self.swap_view.rebuild()
        await safe_edit_interaction_message(interaction, content=self.swap_view.status_text(f"Filter: **{self.swap_view.filter_label()}**."), view=self.swap_view)


class TeamMonsterSwapTypeFilterSelect(discord.ui.Select):
    PAGE_SIZE = 20

    def __init__(self, view: "TeamMonsterSwapView"):
        self.swap_view = view
        page_count = max(1, math.ceil(len(MONSTER_TYPES) / self.PAGE_SIZE))
        current_page = max(0, min(int(view.monster_type_filter_page), page_count - 1))
        start_index = current_page * self.PAGE_SIZE
        visible_types = MONSTER_TYPES[start_index:start_index + self.PAGE_SIZE]
        options: List[discord.SelectOption] = []
        if current_page > 0:
            options.append(discord.SelectOption(label="‹ Previous Type Page", value=f"typepage:{current_page - 1}"))
        if current_page < page_count - 1:
            options.append(discord.SelectOption(label="Next Type Page ›", value=f"typepage:{current_page + 1}"))
        options.append(discord.SelectOption(label="No Type Filter", value="all", default=view.type_filter_key == "all"))
        options.extend(
            discord.SelectOption(label=monster_type[:100], value=f"type:{monster_type}", default=view.type_filter_key == f"type:{monster_type}")
            for monster_type in visible_types
        )
        super().__init__(placeholder=f"Monster Type Filter ({current_page + 1}/{page_count})", min_values=1, max_values=1, options=options, row=3)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value.startswith("typepage:"):
            try:
                self.swap_view.monster_type_filter_page = int(value.split(":", 1)[1])
            except Exception:
                self.swap_view.monster_type_filter_page = 0
        else:
            self.swap_view.type_filter_key = value
        self.swap_view.page = 0
        self.swap_view.rebuild()
        await safe_edit_interaction_message(interaction, content=self.swap_view.status_text(f"Type filter: **{self.swap_view.type_filter_label()}**."), view=self.swap_view)


class TeamMonsterSwapView(discord.ui.View):
    PAGE_SIZE = 25

    def __init__(self, owner_id: int, browser: "TeamBrowserView", row: dict):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.browser = browser
        self.team_id = int(row["team_id"])
        self.team_name = str(row.get("name") or f"Team {self.team_id}")
        self.page = 0
        self.sort_key = "name_asc"
        self.filter_key = "all"
        self.type_filter_key = "all"
        self.search_query = ""
        self.monster_type_filter_page = 0
        self.selected_slot = 0
        self.selected_instance_id: Optional[str] = None
        self.inventory_rows = list(db.user_inventory(owner_id))
        self.message: Optional[discord.Message] = None
        self.rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This team belongs to someone else.", ephemeral=True)
            return False
        return True

    async def refresh_message(self, extra: str = ""):
        if self.message is None:
            return
        try:
            await self.message.edit(content=self.status_text(extra), view=self)
        except Exception:
            pass

    def current_team_row(self) -> Optional[dict]:
        self.browser.rebuild()
        return self.browser.row_by_team_id(self.team_id)

    def current_member_ids(self) -> List[str]:
        row = self.current_team_row()
        return list(row.get("member_ids") or []) if row else []

    def member_at_index(self, index: int) -> Optional[sqlite3.Row]:
        current_ids = self.current_member_ids()
        if 0 <= index < len(current_ids):
            return db.get_inventory_instance(current_ids[index], self.owner_id)
        return None

    def team_slots_text(self) -> str:
        labels: List[str] = []
        current_ids = self.current_member_ids()
        for idx in range(TEAM_SIZE):
            if idx < len(current_ids) and str(current_ids[idx]).strip():
                labels.append(f"{idx + 1}. {display_monster_label_from_id(current_ids[idx])}")
            else:
                labels.append(f"{idx + 1}. [Empty]")
        return "\n".join(labels)

    def apply_filters(self, rows: List[sqlite3.Row]) -> List[sqlite3.Row]:
        filtered = list(rows)
        if self.filter_key.startswith("attr:"):
            attr = self.filter_key.split(":", 1)[1]
            filtered = [row for row in filtered if str(row["attribute"]) == attr]
        elif self.filter_key == "single":
            filtered = [row for row in filtered if len(decode_monster_types(row["monster_types"])) == 1]
        elif self.filter_key == "dual":
            filtered = [row for row in filtered if len(decode_monster_types(row["monster_types"])) > 1]
        elif self.filter_key == "boss":
            filtered = [row for row in filtered if is_boss_monster_row(row)]
        elif self.filter_key == "signature":
            filtered = [row for row in filtered if is_signature_monster_row(row)]
        if self.type_filter_key.startswith("type:"):
            wanted_type = self.type_filter_key.split(":", 1)[1]
            filtered = [row for row in filtered if wanted_type in decode_monster_types(row["monster_types"])]
        if self.search_query.strip():
            query = self.search_query.casefold()
            filtered = [row for row in filtered if query in str(row["monster_name"]).casefold()]
        return filtered

    def apply_sort(self, rows: List[sqlite3.Row]) -> List[sqlite3.Row]:
        def base_name(row: sqlite3.Row):
            return (str(row["monster_name"]).lower(), str(row["attribute"]).lower(), str(row["instance_id"]))

        def stat_sort(row: sqlite3.Row, stat_index: int):
            hp, atk, defense, spd = resolve_row_combat_stats(row)
            value = (hp, atk, defense, spd)[stat_index]
            return (-value, *base_name(row))

        if self.sort_key == "name_desc":
            return sorted(rows, key=base_name, reverse=True)
        if self.sort_key == "newest":
            return sorted(rows, key=lambda row: (str(row["obtained_at"]), str(row["instance_id"])), reverse=True)
        if self.sort_key == "oldest":
            return sorted(rows, key=lambda row: (str(row["obtained_at"]), str(row["instance_id"])))
        if self.sort_key == "hp_desc":
            return sorted(rows, key=lambda row: stat_sort(row, 0))
        if self.sort_key == "atk_desc":
            return sorted(rows, key=lambda row: stat_sort(row, 1))
        if self.sort_key == "def_desc":
            return sorted(rows, key=lambda row: stat_sort(row, 2))
        if self.sort_key == "spd_desc":
            return sorted(rows, key=lambda row: stat_sort(row, 3))
        return sorted(rows, key=base_name)

    def sorted_inventory_rows(self) -> List[sqlite3.Row]:
        return self.apply_sort(self.apply_filters(self.inventory_rows))

    def page_rows(self) -> List[sqlite3.Row]:
        rows = self.sorted_inventory_rows()
        start = self.page * self.PAGE_SIZE
        return rows[start:start + self.PAGE_SIZE]

    def build_row_description(self, row: sqlite3.Row) -> str:
        hp, atk, defense, spd = resolve_row_combat_stats(row)
        marker = "• In Team" if str(row["instance_id"]) in self.current_member_ids() else "• Available"
        category_title = category_title_for_monster(str(row["monster_name"]))
        category_part = f" • {category_title}" if category_title else ""
        return f"{row['attribute']} • {format_monster_types(decode_monster_types(row['monster_types']))}{category_part} • HP {hp} ATK {atk} DEF {defense} SPD {spd} {marker}"

    def filter_label(self) -> str:
        if self.filter_key == "all":
            return "All Monsters"
        if self.filter_key == "single":
            return "Single-Type"
        if self.filter_key == "dual":
            return "Dual-Type"
        if self.filter_key == "boss":
            return "Extra Deck Monster"
        if self.filter_key.startswith("attr:"):
            return self.filter_key.split(":", 1)[1].title()
        return "All Monsters"

    def type_filter_label(self) -> str:
        if self.type_filter_key == "all":
            return "No Type Filter"
        if self.type_filter_key.startswith("type:"):
            return self.type_filter_key.split(":", 1)[1]
        return "No Type Filter"

    def search_label(self) -> str:
        return self.search_query if self.search_query.strip() else "None"

    def sort_label(self) -> str:
        return {
            "name_asc": "A-Z",
            "name_desc": "Z-A",
            "hp_desc": "Highest HP",
            "atk_desc": "Highest ATK",
            "def_desc": "Highest DEF",
            "spd_desc": "Highest SPD",
            "newest": "Newest",
            "oldest": "Oldest",
        }.get(self.sort_key, "A-Z")

    def status_text(self, extra: str = "") -> str:
        total_rows = len(self.sorted_inventory_rows())
        total_pages = max(1, math.ceil(max(1, total_rows) / self.PAGE_SIZE))
        selected_slot_text = f"Slot {self.selected_slot + 1}"
        selected_row = db.get_inventory_instance(self.selected_instance_id, self.owner_id) if self.selected_instance_id else None
        selected_text = display_monster_label_from_id(self.selected_instance_id) if selected_row else "None"
        base = (
            f"Team **{self.team_name}**\n"
            f"Selected slot: **{selected_slot_text}** • Selected monster: **{selected_text}** • Filter: **{self.filter_label()}** • Type: **{self.type_filter_label()}** • Search: **{self.search_label()}** • Sort: **{self.sort_label()}** • Page **{self.page + 1}/{total_pages}**\n"
            f"{self.team_slots_text()}"
        )
        if extra:
            return base + f"\n{extra}"
        return base

    def apply_swap(self) -> str:
        if self.selected_instance_id is None:
            return "Choose a monster you own first."
        selected_row = db.get_inventory_instance(self.selected_instance_id, self.owner_id)
        if selected_row is None:
            return "You do not own that monster anymore."
        member_ids = list(self.current_member_ids())
        if self.selected_slot >= len(member_ids):
            return "That team slot does not exist."
        if self.selected_instance_id in member_ids and member_ids[self.selected_slot] != self.selected_instance_id:
            return "That monster is already in this team. Teams cannot use the same monster copy twice."
        replaced_id = member_ids[self.selected_slot]
        replaced_label = display_monster_label_from_id(replaced_id) if replaced_id else "that monster"
        member_ids[self.selected_slot] = self.selected_instance_id
        try:
            ok = db.update_team_members(self.owner_id, self.team_id, member_ids)
        except ValueError as exc:
            if str(exc).startswith("duplicate_equip_spell:"):
                spell = get_equip_spell_def(str(exc).split(":", 1)[1])
                return f"That swap would create duplicate equip spells: **{spell.name if spell else 'that equip spell'}**."
            raise
        if not ok:
            return "Could not update that team."
        self.inventory_rows = list(db.user_inventory(self.owner_id))
        self.selected_instance_id = None
        self.browser.rebuild()
        self.rebuild()
        return f"Replaced **{replaced_label}** with **{display_monster_label_from_id(member_ids[self.selected_slot])}** in slot **{self.selected_slot + 1}**."

    def rebuild(self):
        self.clear_items()
        total_pages = max(1, math.ceil(max(1, len(self.sorted_inventory_rows())) / self.PAGE_SIZE))
        self.page = max(0, min(self.page, total_pages - 1))
        self.add_item(TeamMonsterSwapSlotSelect(self))
        self.add_item(TeamMonsterSwapSelect(self))
        self.add_item(TeamMonsterSwapFilterSelect(self))
        self.add_item(TeamMonsterSwapTypeFilterSelect(self))
        self.add_item(self.sort_button)
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.search_button)
        self.add_item(self.apply_button)
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= total_pages - 1
        self.apply_button.disabled = self.selected_instance_id is None

    @discord.ui.button(label="Sort", style=discord.ButtonStyle.primary, row=4)
    async def sort_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Choose how to sort your monsters.", ephemeral=True, view=TeamMonsterSwapSortView(self))

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=4)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.status_text(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=4)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        total_pages = max(1, math.ceil(max(1, len(self.sorted_inventory_rows())) / self.PAGE_SIZE))
        self.page = min(total_pages - 1, self.page + 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.status_text(), view=self)

    @discord.ui.button(label="Search", style=discord.ButtonStyle.secondary, row=4)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.message = interaction.message
        await interaction.response.send_modal(TeamMonsterSwapSearchModal(self))

    @discord.ui.button(label="Apply Swap", style=discord.ButtonStyle.success, row=4)
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        result = self.apply_swap()
        await safe_edit_interaction_message(interaction, content=self.status_text(result), view=self)
        browser_row = self.browser.row_by_team_id(self.team_id)
        if browser_row is not None:
            detail_view = TeamDetailView(self.owner_id, self.browser, browser_row)
            await detail_view.refresh_browser_message()


class TeamItemSelect(discord.ui.Select):
    def __init__(self, browser: "TeamBrowserView"):
        self.browser = browser
        rows = browser.page_rows()
        options = []
        for row in rows:
            preview = ", ".join(row.get("preview_names") or []) or "No preview"
            label = str(row["name"])[:100]
            description = f"#{row['team_id']} • {row['member_count']}/{TEAM_SIZE} • {preview}"[:100]
            options.append(discord.SelectOption(label=label, value=str(row["team_id"]), description=description, default=(str(row["team_id"]) == str(browser.selected_team_id))))
        if not options:
            options = [discord.SelectOption(label="No teams on this page", value="__none__")]
        super().__init__(placeholder="Open a team from this page", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "__none__":
            await safe_acknowledge_interaction(interaction)
            return
        self.browser.selected_team_id = self.values[0]
        row = self.browser.get_selected_row()
        if row is None:
            await interaction.response.send_message("That team is no longer available.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_team_detail_embed(row), view=TeamDetailView(self.browser.owner_id, self.browser, row), ephemeral=True)


class TeamFilterSelect(discord.ui.Select):
    def __init__(self, browser: "TeamBrowserView"):
        self.browser = browser
        options = [discord.SelectOption(label=label[:100], value=key, default=(key == browser.filter_key)) for key, label in browser.filter_options()]
        super().__init__(placeholder="Filter", min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        self.browser.filter_key = self.values[0]
        self.browser.page = 1
        self.browser.rebuild()
        await self.browser.refresh(interaction)


class TeamSortSelect(discord.ui.Select):
    def __init__(self, browser: "TeamBrowserView"):
        self.browser = browser
        options = [discord.SelectOption(label=label[:100], value=key, default=(key == browser.sort_key)) for key, label in browser.sort_options()]
        super().__init__(placeholder="Order", min_values=1, max_values=1, options=options, row=3)

    async def callback(self, interaction: discord.Interaction):
        self.browser.sort_key = self.values[0]
        self.browser.page = 1
        self.browser.rebuild()
        await self.browser.refresh(interaction)


class TeamBrowserView(discord.ui.View):
    PER_PAGE = 8

    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.page = 1
        self.filter_key = "all"
        self.sort_key = "name_asc"
        self.filtered_rows: List[dict] = []
        self.selected_team_id: Optional[str] = None
        self.message: Optional[discord.Message] = None
        self.rebuild()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This binder belongs to someone else.", ephemeral=True)
            return False
        return True

    def all_rows(self) -> List[dict]:
        rows: List[dict] = []
        for team in db.list_teams(self.owner_id):
            member_ids = team_slots_from_csv(team["slots_csv"])
            preview_names = [display_monster_label_from_id(instance_id) for instance_id in member_ids]
            rows.append({
                "team_id": team["id"],
                "user_id": self.owner_id,
                "instance_id": str(team["id"]),
                "name": team["name"],
                "member_ids": member_ids,
                "member_count": len(member_ids),
                "preview_names": preview_names,
                "support_deck_id": team["support_deck_id"] if "support_deck_id" in team.keys() else None,
                "extra_deck_id": team["extra_deck_id"] if "extra_deck_id" in team.keys() else None,
                "active_modes": db.get_team_active_modes(self.owner_id, int(team["id"])),
                "is_active": bool(team["is_active"]),
            })
        return rows

    def filter_options(self) -> List[Tuple[str, str]]:
        return [("all", "All Teams"), *[(f"mode:{mode}", f"Active: {label}") for mode, label in TEAM_ACTIVE_MODE_LABELS.items()]]

    def sort_options(self) -> List[Tuple[str, str]]:
        return [("name_asc", "Name A-Z"), ("name_desc", "Name Z-A"), ("team_id_desc", "Newest First"), ("team_id_asc", "Oldest First")]

    def apply_filter(self, rows: List[dict]) -> List[dict]:
        if self.filter_key.startswith("mode:"):
            mode = self.filter_key.split(":", 1)[1]
            return [row for row in rows if mode in row.get("active_modes", [])]
        return list(rows)

    def apply_sort(self, rows: List[dict]) -> List[dict]:
        if self.sort_key == "name_desc":
            return sorted(rows, key=lambda row: (str(row["name"]).lower(), int(row["team_id"])), reverse=True)
        if self.sort_key == "team_id_desc":
            return sorted(rows, key=lambda row: int(row["team_id"]), reverse=True)
        if self.sort_key == "team_id_asc":
            return sorted(rows, key=lambda row: int(row["team_id"]))
        return sorted(rows, key=lambda row: (str(row["name"]).lower(), int(row["team_id"])))

    def total_pages(self) -> int:
        return max(1, math.ceil(max(1, len(self.filtered_rows)) / self.PER_PAGE))

    def page_rows(self) -> List[dict]:
        start = (self.page - 1) * self.PER_PAGE
        return self.filtered_rows[start:start + self.PER_PAGE]

    def get_selected_row(self) -> Optional[dict]:
        if not self.selected_team_id:
            return None
        for row in self.filtered_rows:
            if str(row["team_id"]) == str(self.selected_team_id):
                return row
        return None

    def row_by_team_id(self, team_id: int) -> Optional[dict]:
        for row in self.all_rows():
            if int(row["team_id"]) == int(team_id):
                return row
        return None

    def current_filter_label(self) -> str:
        return dict(self.filter_options()).get(self.filter_key, "All Teams")

    def current_sort_label(self) -> str:
        return dict(self.sort_options()).get(self.sort_key, "Name A-Z")

    def rebuild(self):
        self.filtered_rows = self.apply_sort(self.apply_filter(self.all_rows()))
        self.page = max(1, min(self.page, self.total_pages()))
        self.clear_items()
        self.prev_btn.disabled = self.page <= 1
        self.next_btn.disabled = self.page >= self.total_pages()
        self.add_item(TeamItemSelect(self))
        self.add_item(TeamFilterSelect(self))
        self.add_item(TeamSortSelect(self))
        self.add_item(self.manage_btn)
        self.add_item(self.back_btn)
        self.add_item(self.prev_btn)
        self.add_item(self.next_btn)
        if self.selected_team_id and self.get_selected_row() is None:
            page_rows = self.page_rows()
            self.selected_team_id = str(page_rows[0]["team_id"]) if page_rows else None
        elif not self.selected_team_id:
            page_rows = self.page_rows()
            self.selected_team_id = str(page_rows[0]["team_id"]) if page_rows else None
        
    def summary_text(self) -> str:
        total_owned = len(db.list_teams(self.owner_id))
        visible_start = 0 if not self.filtered_rows else (self.page - 1) * self.PER_PAGE + 1
        visible_end = min(len(self.filtered_rows), self.page * self.PER_PAGE)
        mode_texts = []
        for mode, label in TEAM_ACTIVE_MODE_LABELS.items():
            row = db.get_active_team(self.owner_id, mode)
            mode_texts.append(f"{label}: {row['name'] if row else 'None'}")
        return f"Showing {visible_start}-{visible_end} of {len(self.filtered_rows)} filtered teams • {total_owned} total teams • " + " • ".join(mode_texts)

    def make_file(self) -> discord.File:
        with _PanelUserContext(self.owner_id):
            buffer = render_team_page(self.user_name, self.filtered_rows, self.page, self.PER_PAGE, self.current_filter_label(), self.current_sort_label(), db.get_ui_theme(self.owner_id))
            return discord.File(buffer, filename="teams.png")

    async def refresh(self, interaction: discord.Interaction):
        await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments_factory=lambda: [self.make_file()], view=self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.primary, row=4)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.ui.binder import binder_hub_summary_text, BinderHubView
        view = BinderHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=binder_hub_summary_text(self.owner_id), attachments_factory=lambda: [view.make_file()], view=view)

    @discord.ui.button(label="View Selected", style=discord.ButtonStyle.success, row=4)
    async def view_selected_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        row = self.get_selected_row()
        if row is None:
            await interaction.response.send_message("Select a team from the dropdown first.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_team_detail_embed(row), view=TeamDetailView(self.owner_id, self, row), ephemeral=True)

    @discord.ui.button(label="Manage", style=discord.ButtonStyle.success, row=4)
    async def manage_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_send_interaction_message(interaction, content="Manage teams:", view=TeamManagerView(self.owner_id, browser=self, browser_message=interaction.message), ephemeral=True)

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=4)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(1, self.page - 1)
        self.rebuild()
        await self.refresh(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=4)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.total_pages(), self.page + 1)
        self.rebuild()
        await self.refresh(interaction)


def build_team_detail_embed(row: dict) -> discord.Embed:
    member_ids = row.get("member_ids") or []
    embed = discord.Embed(title=f"👥 Team: {row['name']}", color=discord.Color.blurple())
    embed.description = "╭────────── ✦ Lineup ✦ ──────────╮"
    active_modes = row.get("active_modes") or []
    mode_names = [TEAM_ACTIVE_MODE_LABELS.get(mode, mode.title()) for mode in active_modes]
    embed.add_field(
        name="Battle Modes",
        value=(", ".join(mode_names) if mode_names else "Not assigned to a battle mode"),
        inline=False,
    )
    team_user_id = int(row.get("user_id") or row.get("owner_id") or 0)
    embed.add_field(
        name="Deck",
        value=team_deck_display_name(team_user_id, row.get("support_deck_id")) if team_user_id else "No Deck",
        inline=True,
    )
    embed.add_field(
        name="Extra Deck",
        value=team_extra_deck_display_name(team_user_id, row.get("extra_deck_id")) if team_user_id else "No Extra Deck",
        inline=True,
    )

    for slot_index in range(TEAM_SIZE):
        if slot_index < len(member_ids):
            instance_id = member_ids[slot_index]
            monster_row = db.get_inventory_instance(instance_id)
            if monster_row is None:
                field_value = f"*Missing monster*\n`{instance_id}`"
            else:
                hp, atk, defense, spd = resolve_row_combat_stats(monster_row)
                monster_types = format_monster_types(decode_monster_types(monster_row["monster_types"]))
                equipped_row = db.get_equipped_spell_for_monster(instance_id)
                equipped_spell = get_equip_spell_def(equipped_row["equip_spell_id"]) if equipped_row else None
                equip_text = equipped_spell.name if equipped_spell else "None"
                field_value = (
                    f"**{monster_row['monster_name']}**\n"
                    f"*{monster_row['attribute']} • {monster_types}*\n"
                    f"`HP {hp}` `ATK {atk}`\n"
                    f"`DEF {defense}` `SPD {spd}`\n"
                    f"**Equip:** {equip_text}"
                )
        else:
            field_value = "*Empty slot*"

        embed.add_field(name=f"✦ Slot {slot_index + 1} ✦", value=field_value, inline=True)

    embed.set_footer(text="Use the buttons below to manage or share this team.")
    return embed
