"""Battle log, stats and after-battle buttons."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

import discord

from kuribot.config import CAMPAIGN_AREAS
from kuribot.campaign import get_campaign_area, get_campaign_duelist
from kuribot.arena import (
    ARENA_MATCH_COUNT, build_arena_pool_entries, get_user_arena_rank_position,
    select_arena_opponents,
)
from kuribot.ui.common import safe_edit_interaction_message


class BattleLogPagerView(discord.ui.View):
    def __init__(self, owner_id: int, battle_title: str, entries: Sequence[str], page_size: int = 8):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.battle_title = battle_title
        self.entries = list(entries)
        self.page_size = max(1, page_size)
        self.page = 0
        self.total_pages = max(1, math.ceil(max(1, len(self.entries)) / self.page_size))
        self.sync_buttons()

    def sync_buttons(self):
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.total_pages - 1

    def make_embed(self) -> discord.Embed:
        start = self.page * self.page_size
        page_entries = self.entries[start:start + self.page_size]
        description = "\n".join(f"**{start + idx + 1}.** {line}" for idx, line in enumerate(page_entries)) or "No battle events recorded."
        embed = discord.Embed(title=f"Battle Log — {self.battle_title}", description=description, color=discord.Color.blurple())
        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages} • Entries {start + 1}-{start + len(page_entries)} of {len(self.entries)}")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This battle log belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.sync_buttons()
        await safe_edit_interaction_message(interaction, embed=self.make_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.total_pages - 1, self.page + 1)
        self.sync_buttons()
        await safe_edit_interaction_message(interaction, embed=self.make_embed(), view=self)


class BattleStatsView(discord.ui.View):
    def __init__(self, owner_id: int, battle_title: str, battle_stats: Optional[Dict[str, List[Dict[str, int | str]]]] = None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.battle_title = battle_title
        self.battle_stats = battle_stats or {}

    def _format_lines(self, category_key: str) -> str:
        entries = list(self.battle_stats.get(category_key, []))[:3]
        if not entries:
            return "1. No data\n2. No data\n3. No data"
        lines = []
        for idx in range(3):
            if idx < len(entries):
                entry = entries[idx]
                lines.append(f"{idx + 1}. {entry['name']} ({entry['attribute']}) — {entry['value']}")
            else:
                lines.append(f"{idx + 1}. —")
        return "\n".join(lines)

    def make_embed(self) -> discord.Embed:
        embed = discord.Embed(title=f"Battle Stats — {self.battle_title}", color=discord.Color.gold())
        embed.add_field(name="Top Damage", value=self._format_lines("damage_dealt"), inline=False)
        embed.add_field(name="Top Healing", value=self._format_lines("healing_done"), inline=False)
        embed.add_field(name="Top Damage Taken", value=self._format_lines("damage_taken"), inline=False)
        embed.add_field(name="Top Kills", value=self._format_lines("kills"), inline=False)
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True


class BattleAgainButton(discord.ui.Button):
    def __init__(self, label: str = "Battle Again"):
        super().__init__(label=label, style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        from kuribot.modes.guild_boss import start_guild_boss_battle
        from kuribot.modes.campaign import start_campaign_stage_battle
        from kuribot.modes.normal import start_dueling_grounds_normal_battle
        from kuribot.modes.ranked import start_dueling_grounds_ranked_battle
        view = self.view
        if not isinstance(view, BattleResultView):
            await interaction.response.send_message("This battle can't be replayed right now.", ephemeral=True)
            return
        battle_mode = getattr(view, 'battle_mode', 'normal')
        if battle_mode == 'campaign':
            area_key = str(getattr(view, 'campaign_area_key', '') or '').strip()
            duelist_key = str(getattr(view, 'campaign_duelist_key', '') or '').strip()
            if not area_key or not duelist_key:
                await interaction.response.send_message("This campaign battle can't be replayed right now.", ephemeral=True)
                return
            area = get_campaign_area(area_key) or CAMPAIGN_AREAS[0]
            duelist = get_campaign_duelist(area_key, duelist_key) or {'name': duelist_key}
            enemy_name = duelist.get('name', duelist_key)
            area_display_label = area['label'] + (" - Hard Mode" if bool(getattr(view, 'campaign_hard_mode', False)) else "")
            await safe_edit_interaction_message(
                interaction,
                content=f"⚔️ {area_display_label} • {enemy_name} duel in progress...",
                attachments=[],
                embed=None,
                view=None,
            )
            await start_campaign_stage_battle(interaction, view.owner_id, view.user_name, area_key, duelist_key, hard_mode=bool(getattr(view, 'campaign_hard_mode', False)))
            return
        if battle_mode == 'ranked':
            await safe_edit_interaction_message(interaction, content="Searching the Ranked ladder for a challenger...", attachments=[], embed=None, view=None)
            await start_dueling_grounds_ranked_battle(interaction, view.owner_id, view.user_name)
            return
        if battle_mode == 'guild_boss':
            await safe_edit_interaction_message(interaction, content="Challenging the Guild Boss again...", attachments=[], embed=None, view=None)
            await start_guild_boss_battle(interaction, view.owner_id, view.user_name)
            return
        await safe_edit_interaction_message(interaction, content="Searching the Dueling Grounds for a challenger...", attachments=[], embed=None, view=None)
        await start_dueling_grounds_normal_battle(interaction, view.owner_id, view.user_name)


class DuelingGroundsNpcButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Duelist", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        from kuribot.ui.dueling_grounds import DuelingGroundsLobbyNpcView
        view = self.view
        if not isinstance(view, BattleResultView):
            await interaction.response.send_message("This NPC menu can't be opened right now.", ephemeral=True)
            return
        npc_view = DuelingGroundsLobbyNpcView(view.owner_id, view.user_name)
        await safe_edit_interaction_message(
            interaction,
            content=npc_view.summary_text(),
            attachments=[],
            embed=None,
            view=npc_view,
        )


class BackToLobbyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Back to Lobby", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        from kuribot.ui.dueling_grounds import (
            ActiveCustomLobbyView, DUELING_GROUNDS_CUSTOM_LOBBIES, DuelingGroundsLobbyView,
        )
        view = self.view
        if not isinstance(view, BattleResultView):
            await interaction.response.send_message("This lobby can't be reopened right now.", ephemeral=True)
            return
        lobby_code = str(getattr(view, 'lobby_code', '') or '').strip().upper()
        if lobby_code:
            lobby = DUELING_GROUNDS_CUSTOM_LOBBIES.get(lobby_code)
            if lobby is not None:
                creator_name = str(lobby.get('creator_name') or view.user_name or interaction.user.display_name)
                lobby_view = ActiveCustomLobbyView(view.owner_id, creator_name, lobby_code)
                await safe_edit_interaction_message(interaction, content=lobby_view.summary_text(), attachments=[], embed=None, view=lobby_view)
                return
        lobby_view = DuelingGroundsLobbyView(view.owner_id, view.user_name)
        await safe_edit_interaction_message(interaction, content=lobby_view.summary_text(), attachments=[], embed=None, view=lobby_view)


class BackToArenaRosterButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Back to Challenge Roster", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        from kuribot.ui.arena import ArenaBattleView
        view = self.view
        if not isinstance(view, BattleResultView):
            await interaction.response.send_message("This Arena roster can't be reopened right now.", ephemeral=True)
            return
        pool = await build_arena_pool_entries(view.owner_id)
        owner_rank_position = get_user_arena_rank_position(view.owner_id)
        previous_opponents = getattr(view, 'arena_roster_opponents', [])
        opponents = select_arena_opponents(pool, owner_rank_position, ARENA_MATCH_COUNT, previous_opponents=previous_opponents)
        if not opponents:
            opponents = select_arena_opponents(pool, owner_rank_position, ARENA_MATCH_COUNT)
        arena_view = ArenaBattleView(view.owner_id, view.user_name, opponents)
        await safe_edit_interaction_message(interaction, content=arena_view.summary_text(), attachments_factory=lambda: [arena_view.make_file()], embed=None, view=arena_view)


class BackToCampaignAreaButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Back", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        from kuribot.ui.campaign import CampaignAreaView
        view = self.view
        if not isinstance(view, BattleResultView):
            await interaction.response.send_message("This campaign area can't be reopened right now.", ephemeral=True)
            return
        area_key = str(getattr(view, 'campaign_area_key', '') or '').strip()
        if not area_key:
            await interaction.response.send_message("This campaign area can't be reopened right now.", ephemeral=True)
            return
        area = get_campaign_area(area_key) or CAMPAIGN_AREAS[0]
        area_view = CampaignAreaView(view.owner_id, view.user_name, area['key'], hard_mode=bool(getattr(view, 'campaign_hard_mode', False)))
        area_label = area['label'] + (" - Hard Mode" if bool(getattr(view, 'campaign_hard_mode', False)) else "")
        await safe_edit_interaction_message(
            interaction,
            content=f"Choose a duelist in **{area_label}**.",
            attachments_factory=lambda: [area_view.make_file()],
            embed=None,
            view=area_view,
        )


class BattleResultView(discord.ui.View):
    def __init__(self, owner_id: int, battle_title: str, log_entries: Sequence[str], *, user_name: str = "", allow_battle_again: bool = False, allow_npc_menu: bool = False, allow_back_to_lobby: bool = False, allow_back_to_arena_roster: bool = False, battle_mode: str = 'normal', lobby_code: str = '', campaign_area_key: str = '', campaign_duelist_key: str = '', campaign_hard_mode: bool = False, arena_roster_opponents: Optional[Sequence[dict]] = None, battle_stats: Optional[Dict[str, List[Dict[str, int | str]]]] = None):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.battle_title = battle_title
        self.log_entries = list(log_entries)
        self.battle_stats = battle_stats or {}
        self.user_name = user_name
        self.battle_mode = str(battle_mode or 'normal').strip().lower()
        self.lobby_code = str(lobby_code or '').strip().upper()
        self.campaign_area_key = str(campaign_area_key or '').strip()
        self.campaign_duelist_key = str(campaign_duelist_key or '').strip()
        self.campaign_hard_mode = bool(campaign_hard_mode)
        self.arena_roster_opponents = list(arena_roster_opponents or [])
        if allow_battle_again:
            self.add_item(BattleAgainButton(label="Again" if self.battle_mode == 'guild_boss' else "Battle Again"))
        if allow_npc_menu:
            self.add_item(DuelingGroundsNpcButton())
        if allow_back_to_lobby:
            self.add_item(BackToLobbyButton())
        if allow_back_to_arena_roster and self.arena_roster_opponents:
            self.add_item(BackToArenaRosterButton())
        if self.battle_mode == 'campaign' and self.campaign_area_key:
            self.add_item(BackToCampaignAreaButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        data = interaction.data if isinstance(interaction.data, dict) else {}
        custom_id = str(data.get("custom_id") or "")
        stats_button_custom_id = getattr(self.battle_stats_button, "custom_id", "")
        if custom_id and custom_id == stats_button_custom_id:
            return True
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This battle result belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Battle Log", style=discord.ButtonStyle.primary)
    async def battle_log_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pager = BattleLogPagerView(self.owner_id, self.battle_title, self.log_entries)
        await interaction.response.send_message(embed=pager.make_embed(), view=pager, ephemeral=True)

    @discord.ui.button(label="Battle Stats", style=discord.ButtonStyle.secondary)
    async def battle_stats_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        stats_view = BattleStatsView(self.owner_id, self.battle_title, self.battle_stats)
        await interaction.response.send_message(embed=stats_view.make_embed(), view=stats_view, ephemeral=True)
