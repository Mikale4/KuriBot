"""Arena menus."""

from __future__ import annotations

import math
import sqlite3
from typing import List, Sequence

import discord

from kuribot.config import ATTRIBUTES, TEAM_SIZE
from kuribot.models import Fighter
from kuribot.cards.equip_spells import get_equip_spell_def
from kuribot.database import db
from kuribot.discord_helpers import resolve_global_discord_user_name
from kuribot.monsters import (
    decode_monster_types, display_monster_label_from_id, format_monster_types,
    resolve_row_combat_stats, team_slots_from_csv,
)
from kuribot.battle.setup import _active_team_fighters_for_user
from kuribot.arena import (
    _arena_owned_rows, _arena_team_from_slots, ARENA_LEADERBOARD_LIMIT,
    ARENA_LEADERBOARD_PAGE_SIZE, ARENA_MATCH_COUNT, arena_rank_label, arena_record_from_row,
    ARENA_STARTING_ELO, assign_arena_rank_positions, build_arena_duelist_profiles,
    build_arena_pool_entries, get_user_arena_rank_position, select_arena_opponents,
)
from kuribot.render.arena import (
    render_arena_hub_page, render_arena_leaderboard_page, render_arena_opponents_page,
    render_arena_team_builder_page,
)
from kuribot.ui.common import safe_acknowledge_interaction, safe_edit_interaction_message
from kuribot.ui.dueling_grounds import ArenaEnabledDuelingGroundsHubView


class ArenaTeamFilterSelect(discord.ui.Select):
    def __init__(self, builder: 'ArenaTeamBuilderView'):
        self.builder = builder
        options = [discord.SelectOption(label='All', value='all', default=builder.filter_key == 'all')]
        options.extend(discord.SelectOption(label=attr.title(), value=attr, default=builder.filter_key == attr) for attr in ATTRIBUTES)
        super().__init__(placeholder='Filter monsters', min_values=1, max_values=1, options=options[:25], row=0)

    async def callback(self, interaction: discord.Interaction):
        self.builder.filter_key = self.values[0]
        self.builder.page = 0
        self.builder.rebuild()
        await safe_edit_interaction_message(interaction, content=self.builder.summary_text(), attachments_factory=lambda: [self.builder.make_file()], view=self.builder)


class ArenaTeamSortSelect(discord.ui.Select):
    def __init__(self, builder: 'ArenaTeamBuilderView'):
        self.builder = builder
        options = [
            discord.SelectOption(label='A-Z', value='name_asc', default=builder.sort_key == 'name_asc'),
            discord.SelectOption(label='Z-A', value='name_desc', default=builder.sort_key == 'name_desc'),
            discord.SelectOption(label='HP', value='hp', default=builder.sort_key == 'hp'),
            discord.SelectOption(label='ATK', value='atk', default=builder.sort_key == 'atk'),
            discord.SelectOption(label='DEF', value='def', default=builder.sort_key == 'def'),
            discord.SelectOption(label='SPD', value='spd', default=builder.sort_key == 'spd'),
        ]
        super().__init__(placeholder='Sort monsters', min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        self.builder.sort_key = self.values[0]
        self.builder.page = 0
        self.builder.rebuild()
        await safe_edit_interaction_message(interaction, content=self.builder.summary_text(), attachments_factory=lambda: [self.builder.make_file()], view=self.builder)


class ArenaTeamMemberSelect(discord.ui.Select):
    def __init__(self, builder: 'ArenaTeamBuilderView'):
        self.builder = builder
        rows = builder.page_rows()
        options = [
            discord.SelectOption(label=str(row['monster_name'])[:100], value=str(row['instance_id']), description=builder.build_row_description(row)[:100])
            for row in rows
        ] or [discord.SelectOption(label='No monsters match this filter', value='__none__')]
        super().__init__(placeholder='Choose a monster to add', min_values=1, max_values=1, options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value == '__none__':
            await safe_acknowledge_interaction(interaction)
            return
        message = self.builder.add_pick(value)
        await safe_edit_interaction_message(interaction, content=message, attachments_factory=lambda: [self.builder.make_file()], view=self.builder)


class ArenaTeamBuilderView(discord.ui.View):
    PAGE_SIZE = 8

    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.user_name = user_name
        existing = db.get_arena_team(owner_id)
        self.picks = team_slots_from_csv(str(existing['slots_csv'] or ''))[:TEAM_SIZE] if existing else []
        self.sort_key = 'name_asc'
        self.filter_key = 'all'
        self.page = 0
        self.rebuild()

    def filtered_rows(self) -> List[sqlite3.Row]:
        return _arena_owned_rows(self.owner_id, self.filter_key, self.sort_key)

    def page_rows(self) -> List[sqlite3.Row]:
        rows = self.filtered_rows()
        start = self.page * self.PAGE_SIZE
        return rows[start:start + self.PAGE_SIZE]

    def build_row_description(self, row: sqlite3.Row) -> str:
        hp, atk, defense, spd = resolve_row_combat_stats(row)
        return f"{row['attribute']} • {format_monster_types(decode_monster_types(row['monster_types']))} • HP {hp} ATK {atk} DEF {defense} SPD {spd}"

    def summary_text(self) -> str:
        current = ', '.join(display_monster_label_from_id(mid) for mid in self.picks) if self.picks else 'No defense monsters set yet.'
        return f"Arena Defense Team ({len(self.picks)}/{TEAM_SIZE})\nCurrent picks: {current}\nFilter: **{self.filter_key.upper()}** • Sort: **{self.sort_key.upper()}**"

    def make_file(self) -> discord.File:
        total_rows = len(self.filtered_rows())
        total_pages = max(1, math.ceil(max(1, total_rows) / self.PAGE_SIZE))
        return discord.File(render_arena_team_builder_page(self.owner_id, self.user_name, self.picks, self.page_rows(), self.page + 1, total_pages, self.filter_key, self.sort_key), filename='arena_defense_team.png')

    def add_pick(self, instance_id: str) -> str:
        label = display_monster_label_from_id(instance_id)
        if instance_id in self.picks:
            return f"**{label}** is already on your Arena defense team.\n{self.summary_text()}"
        if len(self.picks) >= TEAM_SIZE:
            return f"Your Arena defense team already has {TEAM_SIZE} monsters. Clear it or save a new set.\n{self.summary_text()}"
        self.picks.append(instance_id)
        self.rebuild()
        return f"Added **{label}** to your Arena defense team.\n{self.summary_text()}"

    def rebuild(self):
        self.clear_items()
        self.add_item(ArenaTeamFilterSelect(self))
        self.add_item(ArenaTeamSortSelect(self))
        self.add_item(ArenaTeamMemberSelect(self))
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.clear_button)
        self.add_item(self.use_active_team_button)
        self.add_item(self.save_button)
        self.add_item(self.back_button)
        total_rows = len(self.filtered_rows())
        max_page = max(0, math.ceil(max(1, total_rows) / self.PAGE_SIZE) - 1)
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= max_page
        active_team = db.get_active_team(self.owner_id, "arena_offense")
        active_slots = team_slots_from_csv(str(active_team['slots_csv'] or ''))[:TEAM_SIZE] if active_team else []
        self.use_active_team_button.disabled = len(active_slots) != TEAM_SIZE
        self.save_button.disabled = len(self.picks) != TEAM_SIZE

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This Arena team builder belongs to someone else.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Prev', style=discord.ButtonStyle.secondary, row=3)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments_factory=lambda: [self.make_file()], view=self)

    @discord.ui.button(label='Next', style=discord.ButtonStyle.secondary, row=3)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        total_rows = len(self.filtered_rows())
        max_page = max(0, math.ceil(max(1, total_rows) / self.PAGE_SIZE) - 1)
        self.page = min(max_page, self.page + 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments_factory=lambda: [self.make_file()], view=self)

    @discord.ui.button(label='Clear', style=discord.ButtonStyle.danger, row=3)
    async def clear_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.picks = []
        self.rebuild()
        await safe_edit_interaction_message(interaction, content='Cleared your Arena defense picks.\n' + self.summary_text(), attachments_factory=lambda: [self.make_file()], view=self)

    @discord.ui.button(label='Use Active Team', style=discord.ButtonStyle.primary, row=4)
    async def use_active_team_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        active_team = db.get_active_team(self.owner_id, "arena_offense")
        if not active_team:
            await interaction.response.send_message(f'You need an active Arena Offense team with {TEAM_SIZE} monsters before using this button.', ephemeral=True)
            return
        active_slots = team_slots_from_csv(str(active_team['slots_csv'] or ''))[:TEAM_SIZE]
        if len(active_slots) != TEAM_SIZE:
            await interaction.response.send_message(f'You need an active Arena Offense team with {TEAM_SIZE} monsters before using this button.', ephemeral=True)
            return
        self.picks = list(active_slots)
        self.rebuild()
        await safe_edit_interaction_message(interaction, content='Copied your active Arena Offense team into your Arena defense picks.\n' + self.summary_text(), attachments_factory=lambda: [self.make_file()], view=self)

    @discord.ui.button(label='Save Arena Team', style=discord.ButtonStyle.success, row=3)
    async def save_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if len(self.picks) != TEAM_SIZE:
            await interaction.response.send_message(f'You need exactly {TEAM_SIZE} monsters for Arena.', ephemeral=True)
            return
        try:
            db.set_arena_team(self.owner_id, self.picks)
        except ValueError as exc:
            error_text = str(exc)
            if error_text.startswith('duplicate_equip_spell:'):
                spell = get_equip_spell_def(error_text.split(':', 1)[1])
                await interaction.response.send_message(f"That Arena team would duplicate equip spells: **{spell.name if spell else 'Unknown Spell'}**.", ephemeral=True)
                return
            if error_text in {"Extra Deck Monsters cannot be placed in normal teams. Put them in an Extra Deck instead."}:
                await interaction.response.send_message(error_text, ephemeral=True)
                return
            raise
        await safe_edit_interaction_message(interaction, content='Saved your Arena defense team.\n' + self.summary_text(), attachments_factory=lambda: [self.make_file()], view=self)

    @discord.ui.button(label='Back', style=discord.ButtonStyle.secondary, row=4)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ArenaHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)


class ArenaOpponentSelect(discord.ui.Select):
    def __init__(self, arena_view: 'ArenaBattleView'):
        self.arena_view = arena_view
        options = []
        for idx, entry in enumerate(arena_view.opponents, start=1):
            options.append(discord.SelectOption(label=f"#{idx} {entry['name']}"[:100], value=str(idx - 1), description=str(entry.get('rank_label', arena_rank_label(entry.get('rank_position', idx))))[:100]))
        if not options:
            options = [discord.SelectOption(label='No opponents available', value='-1')]
        super().__init__(placeholder='Choose an Arena opponent', min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        from kuribot.modes.arena import start_arena_battle
        value = int(self.values[0])
        if value < 0 or value >= len(self.arena_view.opponents):
            await safe_acknowledge_interaction(interaction)
            return
        await safe_edit_interaction_message(interaction, content='Starting Arena battle...', attachments=[], embed=None, view=None)
        await start_arena_battle(interaction, self.arena_view.owner_id, self.arena_view.user_name, self.arena_view.opponents[value], roster_opponents=self.arena_view.opponents)


class ArenaBattleView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, opponents: Sequence[dict]):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.opponents = list(opponents)[:ARENA_MATCH_COUNT]
        self.add_item(ArenaOpponentSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This Arena menu belongs to someone else.', ephemeral=True)
            return False
        return True

    def make_file(self) -> discord.File:
        return discord.File(render_arena_opponents_page(self.user_name, self.owner_id, self.opponents), filename='arena_opponents.png')

    def summary_text(self) -> str:
        return 'Choose one of the 9 Arena opponents below. Offense uses your active team; defense teams come from Arena Set Team.'

    @discord.ui.button(label='Refresh', style=discord.ButtonStyle.secondary, row=1)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pool = await build_arena_pool_entries(self.owner_id)
        owner_rank_position = get_user_arena_rank_position(self.owner_id)
        self.opponents = select_arena_opponents(pool, owner_rank_position, ARENA_MATCH_COUNT, previous_opponents=self.opponents)
        self.clear_items()
        self.add_item(ArenaOpponentSelect(self))
        self.add_item(self.refresh_button)
        self.add_item(self.back_button)
        await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments_factory=lambda: [self.make_file()], embed=None, view=self)

    @discord.ui.button(label='Back', style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ArenaHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)


class ArenaLeaderboardView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, entries: Sequence[dict], page: int = 0):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.entries = list(entries)[:ARENA_LEADERBOARD_LIMIT]
        self.page = page
        self.max_page = max(0, math.ceil(max(1, len(self.entries)) / ARENA_LEADERBOARD_PAGE_SIZE) - 1)
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This Arena leaderboard belongs to someone else.', ephemeral=True)
            return False
        return True

    def _sync_buttons(self):
        self.page = max(0, min(self.page, self.max_page))
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.max_page

    def make_file(self) -> discord.File:
        return discord.File(render_arena_leaderboard_page(self.owner_id, self.entries, self.page, self.max_page), filename='arena_leaderboard.png')

    def summary_text(self) -> str:
        return f"Arena leaderboard • Page {self.page + 1}/{self.max_page + 1} • Top {min(ARENA_LEADERBOARD_LIMIT, len(self.entries))}"

    @discord.ui.button(label='Previous', style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments_factory=lambda: [self.make_file()], embed=None, view=self)

    @discord.ui.button(label='Next', style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.max_page, self.page + 1)
        self._sync_buttons()
        await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments_factory=lambda: [self.make_file()], embed=None, view=self)

    @discord.ui.button(label='Back', style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ArenaHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)


class ArenaHubView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This Arena hub belongs to someone else.', ephemeral=True)
            return False
        return True

    def make_file(self) -> discord.File:
        return discord.File(render_arena_hub_page(self.user_name, self.owner_id), filename='arena_hub.png')

    def summary_text(self) -> str:
        arena_team = db.get_arena_team(self.owner_id)
        defense_text = 'Set' if arena_team and len(team_slots_from_csv(str(arena_team['slots_csv'] or ''))) == TEAM_SIZE else 'Not set'
        user_row = db.get_user(self.owner_id)
        return f"Arena hub • **{arena_rank_label(get_user_arena_rank_position(self.owner_id))}** • Defense Team: **{defense_text}** • Arena Packs: **{int(user_row['arena_tickets'] or 0)}**"

    @discord.ui.button(label='Set Team', style=discord.ButtonStyle.primary)
    async def set_team_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        builder = ArenaTeamBuilderView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=builder.summary_text(), attachments_factory=lambda: [builder.make_file()], embed=None, view=builder)

    @discord.ui.button(label='Battle', style=discord.ButtonStyle.success)
    async def battle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        active_team = _active_team_fighters_for_user(self.owner_id, "arena_offense")
        if len(active_team) != TEAM_SIZE:
            await interaction.response.send_message(f'You need an active Arena Offense team with {TEAM_SIZE} monsters before entering Arena.', ephemeral=True)
            return
        pool = await build_arena_pool_entries(self.owner_id)
        owner_rank_position = get_user_arena_rank_position(self.owner_id)
        opponents = select_arena_opponents(pool, owner_rank_position, ARENA_MATCH_COUNT)
        if not opponents:
            await interaction.response.send_message('No Arena opponents are available right now.', ephemeral=True)
            return
        view = ArenaBattleView(self.owner_id, self.user_name, opponents)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)

    @discord.ui.button(label='Leaderboard', style=discord.ButtonStyle.danger)
    async def leaderboard_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        entries: List[dict] = []
        for row in db.arena_leaderboard_users():
            user_id = int(row['user_id'])
            arena_team_row = db.get_arena_team(user_id)
            team: List[Fighter] = []
            if arena_team_row is not None:
                team = _arena_team_from_slots(str(arena_team_row['slots_csv'] or ''), user_id=user_id)
            entries.append({
                'kind': 'user',
                'id': user_id,
                'name': await resolve_global_discord_user_name(user_id),
                'elo': int(row['arena_elo'] or ARENA_STARTING_ELO),
                'record': arena_record_from_row(row),
                'team': team,
                'team_slots_csv': str(arena_team_row['slots_csv'] or '') if arena_team_row is not None else '',
            })
        entries.extend(build_arena_duelist_profiles())
        entries = assign_arena_rank_positions(entries)
        view = ArenaLeaderboardView(self.owner_id, self.user_name, entries[:ARENA_LEADERBOARD_LIMIT])
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)

    @discord.ui.button(label='Back', style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = ArenaEnabledDuelingGroundsHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)
