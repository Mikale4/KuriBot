"""Battle hub, Dueling Grounds lobby and PvP menus."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import discord

from kuribot.config import CAMPAIGN_AREAS, DUELING_GROUNDS_BATTLE_HEARTS, RANKED_STARTING_ELO, RNG, TEAM_SIZE
from kuribot.core import bot, is_bot_creator
from kuribot.battle.setup import _active_team_fighters_for_user
from kuribot.discord_helpers import resolve_global_discord_user_names
from kuribot.opponents import format_duelist_display_name
from kuribot.ranked import ranked_gxe_from_row
from kuribot.dueling_grounds import get_dueling_grounds_duelist, get_dueling_grounds_duelist_data
from kuribot.campaign import campaign_area_status_text, maybe_build_campaign_hard_mode_unlock_embed
from kuribot.render.hubs import (
    make_battle_hub_file, make_dueling_grounds_hub_file, render_dueling_grounds_hub_page,
)
from kuribot.ui.common import safe_acknowledge_interaction, safe_edit_interaction_message
from kuribot.ui.campaign import CampaignHubView


def battle_hub_summary_text(user_id: int) -> str:
    domino_status = campaign_area_status_text(user_id, CAMPAIGN_AREAS[0]["key"])
    return (
        "Choose a battle mode.\n"
        f"Campaign progress: **{domino_status}** in **{CAMPAIGN_AREAS[0]['label']}**.\n"
        f"Campaign is live. Duel Tower is still under construction. Use `;pvp` for Dueling Grounds."
    )


class BattleHubView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This Adventure hub belongs to someone else.", ephemeral=True)
            return False
        return True

    def make_file(self) -> discord.File:
        return make_battle_hub_file(self.user_name, self.owner_id)

    @discord.ui.button(label="Campaign", style=discord.ButtonStyle.primary)
    async def campaign_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        unlock_embed = maybe_build_campaign_hard_mode_unlock_embed(self.owner_id)
        view = CampaignHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content="Choose a campaign area.", attachments_factory=lambda: [view.make_file()], embed=None, view=view)
        if unlock_embed is not None:
            await interaction.followup.send(embed=unlock_embed, ephemeral=False)

    @discord.ui.button(label="Duel Tower", style=discord.ButtonStyle.secondary)
    async def duel_tower_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Duel Tower free-duel mode is still under construction.", ephemeral=True)


class DuelingGroundsLobbyNpcSelect(discord.ui.Select):
    def __init__(self, owner_id: int, user_name: str, page: int, duelists: Sequence[Tuple[str, dict]]):
        self.owner_id = owner_id
        self.user_name = user_name
        self.page = page
        options = [
            discord.SelectOption(
                label=str(duelist.get("name", duelist_key))[:100],
                value=duelist_key,
                description=f"Battle {str(duelist.get('name', duelist_key))[:85]} in Normal mode.",
            )
            for duelist_key, duelist in duelists
        ]
        if not options:
            options = [discord.SelectOption(label="No duelists found", value="__none__", description="No Duelists folder portraits were available.")]
        super().__init__(placeholder="Choose a Duelist", min_values=1, max_values=1, options=options, disabled=not bool(duelists))

    async def callback(self, interaction: discord.Interaction):
        from kuribot.modes.normal import start_dueling_grounds_normal_battle
        value = self.values[0]
        if value == "__none__":
            await safe_acknowledge_interaction(interaction)
            return
        duelist_name = get_dueling_grounds_duelist(value).get("name", format_duelist_display_name(value)) if get_dueling_grounds_duelist(value) else format_duelist_display_name(value)
        await safe_edit_interaction_message(
            interaction,
            content=f"Challenging **{duelist_name}** in the Dueling Grounds...",
            attachments=[],
            embed=None,
            view=None,
        )
        await start_dueling_grounds_normal_battle(interaction, self.owner_id, self.user_name, forced_duelist_key=value)


class DuelingGroundsLobbyNpcJumpToModal(discord.ui.Modal, title="Go To Duelist Page"):
    page_number = discord.ui.TextInput(
        label="Page number",
        placeholder="Enter a page number",
        required=True,
        max_length=4,
    )

    def __init__(self, npc_view: "DuelingGroundsLobbyNpcView"):
        super().__init__()
        self.npc_view = npc_view
        self.page_number.default = str(npc_view.page + 1)
        self.page_number.placeholder = f"1-{npc_view.max_page + 1}"

    async def on_submit(self, interaction: discord.Interaction):
        raw_value = str(self.page_number).strip()
        try:
            target_page = int(raw_value)
        except Exception:
            await interaction.response.send_message("Enter a valid Duelist page number.", ephemeral=True)
            return
        total_pages = self.npc_view.max_page + 1
        if target_page < 1 or target_page > total_pages:
            await interaction.response.send_message(f"Choose a page between 1 and {total_pages}.", ephemeral=True)
            return
        self.npc_view.page = target_page - 1
        self.npc_view._rebuild()
        await safe_edit_interaction_message(
            interaction,
            content=self.npc_view.summary_text(),
            attachments=[],
            embed=None,
            view=self.npc_view,
        )


class DuelingGroundsLobbyNpcView(discord.ui.View):
    PAGE_SIZE = 10

    def __init__(self, owner_id: int, user_name: str, page: int = 0):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.page = page
        self._rebuild()

    def _all_duelists(self) -> List[Tuple[str, dict]]:
        duelists = get_dueling_grounds_duelist_data()
        return sorted(duelists.items(), key=lambda item: str(item[1].get("name", item[0])).lower())

    def _rebuild(self):
        self.clear_items()
        all_duelists = self._all_duelists()
        self.max_page = max(0, (len(all_duelists) - 1) // self.PAGE_SIZE) if all_duelists else 0
        self.page = max(0, min(self.page, self.max_page))
        start_index = self.page * self.PAGE_SIZE
        end_index = start_index + self.PAGE_SIZE
        page_duelists = all_duelists[start_index:end_index]
        self.add_item(DuelingGroundsLobbyNpcSelect(self.owner_id, self.user_name, self.page, page_duelists))

        prev_button = discord.ui.Button(label="Previous", style=discord.ButtonStyle.secondary, disabled=self.page <= 0, row=1)
        page_button = discord.ui.Button(label="Page", style=discord.ButtonStyle.primary, disabled=self.max_page <= 0, row=1)
        next_button = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary, disabled=self.page >= self.max_page, row=1)
        back_button = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, row=1)

        async def prev_callback(interaction: discord.Interaction):
            self.page = max(0, self.page - 1)
            self._rebuild()
            await safe_edit_interaction_message(
                interaction,
                content=self.summary_text(),
                attachments=[],
                embed=None,
                view=self,
            )

        async def page_callback(interaction: discord.Interaction):
            await interaction.response.send_modal(DuelingGroundsLobbyNpcJumpToModal(self))

        async def next_callback(interaction: discord.Interaction):
            self.page = min(self.max_page, self.page + 1)
            self._rebuild()
            await safe_edit_interaction_message(
                interaction,
                content=self.summary_text(),
                attachments=[],
                embed=None,
                view=self,
            )

        async def back_callback(interaction: discord.Interaction):
            view = DuelingGroundsLobbyView(self.owner_id, self.user_name)
            await safe_edit_interaction_message(
                interaction,
                content=view.summary_text(),
                attachments=[],
                embed=None,
                view=view,
            )

        prev_button.callback = prev_callback
        page_button.callback = page_callback
        next_button.callback = next_callback
        back_button.callback = back_callback
        self.add_item(prev_button)
        self.add_item(page_button)
        self.add_item(next_button)
        self.add_item(back_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This Dueling Grounds menu belongs to someone else.", ephemeral=True)
            return False
        return True

    def summary_text(self) -> str:
        total = len(self._all_duelists())
        if total <= 0:
            return "No Duelists folder portraits were found for Duelist battles."
        return f"Choose a Duelist from the Duelists folder. Page {self.page + 1}/{self.max_page + 1} • {total} total duelists."


DUELING_GROUNDS_CUSTOM_LOBBIES: Dict[str, dict] = {}


def _generate_dueling_lobby_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = ''.join(RNG.choice(alphabet) for _ in range(6))
        if code not in DUELING_GROUNDS_CUSTOM_LOBBIES:
            return code


def _default_custom_lobby_settings() -> dict:
    return {
        'hearts': DUELING_GROUNDS_BATTLE_HEARTS,
        'disable_cards': False,
        'disable_equips': False,
    }


def _find_dueling_lobby_by_creator(user_id: int) -> Optional[dict]:
    for lobby in DUELING_GROUNDS_CUSTOM_LOBBIES.values():
        if int(lobby.get('creator_id', 0)) == int(user_id):
            return lobby
    return None


async def _fetch_lobby_message(lobby: dict) -> Optional[discord.Message]:
    channel_id = int(lobby.get('channel_id') or 0)
    message_id = int(lobby.get('message_id') or 0)
    if channel_id <= 0 or message_id <= 0:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return None
    try:
        return await channel.fetch_message(message_id)
    except Exception:
        return None


async def refresh_dueling_lobby_message(lobby: dict):
    message = await _fetch_lobby_message(lobby)
    if message is None:
        return
    creator_name = str(lobby.get('creator_name') or 'Player 1')
    view = ActiveCustomLobbyView(int(lobby['creator_id']), creator_name, str(lobby['code']))
    try:
        await message.edit(content=view.summary_text(), attachments=[], embed=None, view=view)
    except Exception:
        pass


def format_custom_lobby_settings(settings: dict) -> str:
    hearts = max(1, min(5, int(settings.get('hearts', DUELING_GROUNDS_BATTLE_HEARTS) or DUELING_GROUNDS_BATTLE_HEARTS)))
    cards = 'Disabled' if settings.get('disable_cards') else 'Enabled'
    equips = 'Disabled' if settings.get('disable_equips') else 'Enabled'
    return f"Hearts: **{hearts}** • Support Cards: **{cards}** • Equip Spells: **{equips}**"


class DuelingGroundsCreateLobbySelect(discord.ui.Select):
    def __init__(self, owner_id: int, user_name: str):
        self.owner_id = owner_id
        self.user_name = user_name
        options = [
            discord.SelectOption(label='Duelist vs Duelist', value='duelist_vs_duelist', description='Pick 2 duelists and watch them battle.'),
            discord.SelectOption(label='Custom Match', value='custom_match', description='Create a lobby with custom hearts/cards/equips settings.'),
        ]
        super().__init__(placeholder='Choose a lobby type', min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value == 'duelist_vs_duelist':
            view = DuelistVsDuelistSelectView(self.owner_id, self.user_name)
            await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments=[], embed=None, view=view)
            return
        view = CustomLobbyBuilderView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments=[], embed=None, view=view)


class DuelingGroundsCreateLobbyMenuView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.add_item(DuelingGroundsCreateLobbySelect(owner_id, user_name))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This Dueling Grounds menu belongs to someone else.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Back', style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = DuelingGroundsLobbyView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments=[], embed=None, view=view)


class CustomLobbyHeartsModal(discord.ui.Modal, title='Custom Match Hearts'):
    hearts = discord.ui.TextInput(label='Hearts', placeholder='1-5', required=True, max_length=1)

    def __init__(self, builder_view: 'CustomLobbyBuilderView'):
        super().__init__()
        self.builder_view = builder_view
        self.hearts.default = str(builder_view.settings['hearts'])

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(str(self.hearts).strip())
        except Exception:
            await interaction.response.send_message('Enter a whole number from 1 to 5.', ephemeral=True)
            return
        if value < 1 or value > 5:
            await interaction.response.send_message('Hearts must be between 1 and 5.', ephemeral=True)
            return
        self.builder_view.settings['hearts'] = value
        await safe_edit_interaction_message(interaction, content=self.builder_view.summary_text(), attachments=[], embed=None, view=self.builder_view)


class CustomLobbyToggleModal(discord.ui.Modal):
    toggle_value = discord.ui.TextInput(label='Disable?', placeholder='yes or no', required=True, max_length=3)

    def __init__(self, builder_view: 'CustomLobbyBuilderView', setting_key: str, title: str):
        super().__init__(title=title)
        self.builder_view = builder_view
        self.setting_key = setting_key
        self.toggle_value.default = 'yes' if builder_view.settings.get(setting_key) else 'no'

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.toggle_value).strip().lower()
        if raw not in {'y', 'yes', 'n', 'no'}:
            await interaction.response.send_message("Enter 'yes' or 'no'.", ephemeral=True)
            return
        self.builder_view.settings[self.setting_key] = raw in {'y', 'yes'}
        await safe_edit_interaction_message(interaction, content=self.builder_view.summary_text(), attachments=[], embed=None, view=self.builder_view)


class CustomLobbyBuilderView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.settings = _default_custom_lobby_settings()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This custom match menu belongs to someone else.', ephemeral=True)
            return False
        return True

    def summary_text(self) -> str:
        return f"Set your custom match options. Defaults are standard battle settings.\n{format_custom_lobby_settings(self.settings)}"

    @discord.ui.button(label='Hearts', style=discord.ButtonStyle.primary)
    async def hearts_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CustomLobbyHeartsModal(self))

    @discord.ui.button(label='Cards', style=discord.ButtonStyle.secondary)
    async def cards_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CustomLobbyToggleModal(self, 'disable_cards', 'Support Cards'))

    @discord.ui.button(label='Equips', style=discord.ButtonStyle.secondary)
    async def equips_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CustomLobbyToggleModal(self, 'disable_equips', 'Equip Spells'))

    @discord.ui.button(label='Create Match', style=discord.ButtonStyle.success)
    async def create_match_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        existing = _find_dueling_lobby_by_creator(self.owner_id)
        if existing is not None:
            DUELING_GROUNDS_CUSTOM_LOBBIES.pop(str(existing.get('code')), None)
        code = _generate_dueling_lobby_code()
        lobby = {
            'code': code,
            'creator_id': self.owner_id,
            'creator_name': self.user_name,
            'joiner_id': None,
            'joiner_name': '',
            'settings': dict(self.settings),
            'channel_id': interaction.channel_id,
            'message_id': 0,
        }
        DUELING_GROUNDS_CUSTOM_LOBBIES[code] = lobby
        view = ActiveCustomLobbyView(self.owner_id, self.user_name, code)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments=[], embed=None, view=view)
        try:
            message = await interaction.original_response()
            lobby['message_id'] = int(message.id)
        except Exception:
            pass
        await refresh_dueling_lobby_message(lobby)

    @discord.ui.button(label='Back', style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = DuelingGroundsCreateLobbyMenuView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content='Choose how you want to create your lobby.', attachments=[], embed=None, view=view)


class JoinLobbyModal(discord.ui.Modal, title='Join Lobby'):
    lobby_code = discord.ui.TextInput(label='Lobby code', placeholder='Enter the 6-character code', required=True, max_length=6)

    def __init__(self, owner_id: int, user_name: str):
        super().__init__()
        self.owner_id = owner_id
        self.user_name = user_name

    async def on_submit(self, interaction: discord.Interaction):
        code = str(self.lobby_code).strip().upper()
        lobby = DUELING_GROUNDS_CUSTOM_LOBBIES.get(code)
        if lobby is None:
            await interaction.response.send_message('That lobby code was not found.', ephemeral=True)
            return
        if int(lobby.get('creator_id') or 0) == interaction.user.id:
            await interaction.response.send_message('You cannot join your own lobby.', ephemeral=True)
            return
        if lobby.get('joiner_id'):
            await interaction.response.send_message('That lobby is already full.', ephemeral=True)
            return
        if len(_active_team_fighters_for_user(interaction.user.id, "lobby")) < TEAM_SIZE:
            await interaction.response.send_message(f'You need an active team with {TEAM_SIZE} monsters before joining a lobby.', ephemeral=True)
            return
        lobby['joiner_id'] = interaction.user.id
        lobby['joiner_name'] = interaction.user.display_name
        await interaction.response.send_message(f"You joined **{lobby['creator_name']}**'s lobby. Code: **{code}**.", ephemeral=True)
        await refresh_dueling_lobby_message(lobby)


class ActiveCustomLobbyView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, lobby_code: str):
        super().__init__(timeout=1800)
        self.owner_id = owner_id
        self.user_name = user_name
        self.lobby_code = lobby_code
        lobby = DUELING_GROUNDS_CUSTOM_LOBBIES.get(lobby_code)
        if lobby is not None and lobby.get('joiner_id'):
            self.add_item(StartCustomLobbyMatchButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This lobby belongs to someone else.', ephemeral=True)
            return False
        return True

    def summary_text(self) -> str:
        lobby = DUELING_GROUNDS_CUSTOM_LOBBIES.get(self.lobby_code)
        if lobby is None:
            return 'This lobby is no longer active.'
        joiner_name = str(lobby.get('joiner_name') or 'Waiting for player 2...')
        return f"Lobby Code: **{self.lobby_code}**\nPlayer 1: **{lobby['creator_name']}**\nPlayer 2: **{joiner_name}**\n{format_custom_lobby_settings(lobby['settings'])}"

    @discord.ui.button(label='End', style=discord.ButtonStyle.danger)
    async def end_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        DUELING_GROUNDS_CUSTOM_LOBBIES.pop(self.lobby_code, None)
        view = DuelingGroundsLobbyView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content='Your lobby has been closed.', attachments=[], embed=None, view=view)


class StartCustomLobbyMatchButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label='Start Match', style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        from kuribot.modes.exhibition import start_dueling_grounds_custom_user_battle
        if not isinstance(self.view, ActiveCustomLobbyView):
            await interaction.response.send_message('That lobby view is no longer available.', ephemeral=True)
            return
        lobby = DUELING_GROUNDS_CUSTOM_LOBBIES.get(self.view.lobby_code)
        if lobby is None:
            await interaction.response.send_message('That lobby is no longer active.', ephemeral=True)
            return
        joiner_id = int(lobby.get('joiner_id') or 0)
        if joiner_id <= 0:
            await interaction.response.send_message('A second player has not joined yet.', ephemeral=True)
            return
        if len(_active_team_fighters_for_user(self.view.owner_id, "lobby")) < TEAM_SIZE:
            await interaction.response.send_message(f'You need an active team with {TEAM_SIZE} monsters before starting this match.', ephemeral=True)
            return
        if len(_active_team_fighters_for_user(joiner_id, "lobby")) < TEAM_SIZE:
            await interaction.response.send_message(f"{lobby.get('joiner_name') or 'The joined player'} no longer has a valid active team.", ephemeral=True)
            return
        settings = dict(lobby.get('settings') or _default_custom_lobby_settings())
        await safe_edit_interaction_message(interaction, content=f"Starting custom match vs **{lobby.get('joiner_name') or 'Player 2'}**...", attachments=[], embed=None, view=None)
        await start_dueling_grounds_custom_user_battle(interaction, self.view.owner_id, self.view.user_name, joiner_id, str(lobby.get('joiner_name') or 'Player 2'), settings, lobby_code=self.view.lobby_code)


class DuelistVsDuelistSelect(discord.ui.Select):
    def __init__(self, owner_id: int, user_name: str, page_duelists: List[Tuple[str, dict]], first_key: Optional[str] = None):
        self.owner_id = owner_id
        self.user_name = user_name
        self.first_key = first_key
        options = []
        for key, data in page_duelists:
            label = str(data.get('name', format_duelist_display_name(key)))[:100]
            desc = 'Choose this duelist' if first_key is None else 'Choose as duelist 2'
            options.append(discord.SelectOption(label=label, value=key, description=desc[:100]))
        if not options:
            options = [discord.SelectOption(label='No duelists found', value='__none__')]
        placeholder = 'Choose Duelist 1' if first_key is None else 'Choose Duelist 2'
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        from kuribot.modes.exhibition import start_duelist_vs_duelist_battle
        value = self.values[0]
        if value == '__none__':
            await safe_acknowledge_interaction(interaction)
            return
        if self.first_key is None:
            view = DuelistVsDuelistSelectView(self.owner_id, self.user_name, first_key=value)
            await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments=[], embed=None, view=view)
            return
        if value == self.first_key:
            await interaction.response.send_message('Choose two different duelists.', ephemeral=True)
            return
        first_name = get_dueling_grounds_duelist(self.first_key).get('name', format_duelist_display_name(self.first_key)) if get_dueling_grounds_duelist(self.first_key) else format_duelist_display_name(self.first_key)
        second_name = get_dueling_grounds_duelist(value).get('name', format_duelist_display_name(value)) if get_dueling_grounds_duelist(value) else format_duelist_display_name(value)
        await safe_edit_interaction_message(interaction, content=f'Starting Duelist vs Duelist: **{first_name}** vs **{second_name}**...', attachments=[], embed=None, view=None)
        await start_duelist_vs_duelist_battle(interaction, self.owner_id, self.user_name, self.first_key, value)


class DuelistVsDuelistSelectView(discord.ui.View):
    PAGE_SIZE = 10

    def __init__(self, owner_id: int, user_name: str, page: int = 0, first_key: Optional[str] = None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.page = page
        self.first_key = first_key
        self._rebuild()

    def _all_duelists(self) -> List[Tuple[str, dict]]:
        return sorted(get_dueling_grounds_duelist_data().items(), key=lambda item: str(item[1].get('name', item[0])).lower())

    def _rebuild(self):
        self.clear_items()
        all_duelists = self._all_duelists()
        self.max_page = max(0, (len(all_duelists) - 1) // self.PAGE_SIZE) if all_duelists else 0
        self.page = max(0, min(self.page, self.max_page))
        start = self.page * self.PAGE_SIZE
        page_duelists = all_duelists[start:start + self.PAGE_SIZE]
        self.add_item(DuelistVsDuelistSelect(self.owner_id, self.user_name, page_duelists, first_key=self.first_key))
        prev_button = discord.ui.Button(label='Previous', style=discord.ButtonStyle.secondary, disabled=self.page <= 0, row=1)
        next_button = discord.ui.Button(label='Next', style=discord.ButtonStyle.secondary, disabled=self.page >= self.max_page, row=1)
        back_button = discord.ui.Button(label='Back', style=discord.ButtonStyle.secondary, row=1)

        async def prev_callback(interaction: discord.Interaction):
            self.page = max(0, self.page - 1)
            self._rebuild()
            await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments=[], embed=None, view=self)

        async def next_callback(interaction: discord.Interaction):
            self.page = min(self.max_page, self.page + 1)
            self._rebuild()
            await safe_edit_interaction_message(interaction, content=self.summary_text(), attachments=[], embed=None, view=self)

        async def back_callback(interaction: discord.Interaction):
            if self.first_key is None:
                view = DuelingGroundsCreateLobbyMenuView(self.owner_id, self.user_name)
                await safe_edit_interaction_message(interaction, content='Choose how you want to create your lobby.', attachments=[], embed=None, view=view)
            else:
                view = DuelistVsDuelistSelectView(self.owner_id, self.user_name)
                await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments=[], embed=None, view=view)

        prev_button.callback = prev_callback
        next_button.callback = next_callback
        back_button.callback = back_callback
        self.add_item(prev_button)
        self.add_item(next_button)
        self.add_item(back_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This Dueling Grounds menu belongs to someone else.', ephemeral=True)
            return False
        return True

    def summary_text(self) -> str:
        if self.first_key is None:
            return f"Choose Duelist 1. Page {self.page + 1}/{self.max_page + 1}."
        first_name = get_dueling_grounds_duelist(self.first_key).get('name', format_duelist_display_name(self.first_key)) if get_dueling_grounds_duelist(self.first_key) else format_duelist_display_name(self.first_key)
        return f"Duelist 1: **{first_name}**. Choose Duelist 2. Page {self.page + 1}/{self.max_page + 1}."


class DuelingGroundsLobbyView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This Dueling Grounds lobby belongs to someone else.", ephemeral=True)
            return False
        return True

    def make_file(self) -> Optional[discord.File]:
        return None

    def summary_text(self) -> str:
        return "Custom lobbies against duelists and/or users."

    @discord.ui.button(label="Duelist", style=discord.ButtonStyle.primary)
    async def npc_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = DuelingGroundsLobbyNpcView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(
            interaction,
            content=view.summary_text(),
            attachments=[],
            embed=None,
            view=view,
        )

    @discord.ui.button(label="Create Lobby", style=discord.ButtonStyle.secondary)
    async def create_lobby_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = DuelingGroundsCreateLobbyMenuView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content='Choose how you want to create your lobby.', attachments=[], embed=None, view=view)

    @discord.ui.button(label="Join Lobby", style=discord.ButtonStyle.secondary)
    async def join_lobby_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(JoinLobbyModal(self.owner_id, self.user_name))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = DuelingGroundsHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments=[], embed=None, view=view)


class DuelingGroundsHubView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This Dueling Grounds hub belongs to someone else.", ephemeral=True)
            return False
        return True

    def make_file(self) -> discord.File:
        return make_dueling_grounds_hub_file(self.user_name, self.owner_id)

    def summary_text(self) -> str:
        return "Choose a Dueling Grounds mode. Normal starts a random duelist battle, Ranked uses ELO + GXE and can match duelists or users with active teams, and Lobby creates custom matches against duelists and/or users."

    @discord.ui.button(label="Normal", style=discord.ButtonStyle.primary)
    async def normal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.modes.normal import start_dueling_grounds_normal_battle
        await safe_edit_interaction_message(interaction, content="Searching the Dueling Grounds for a challenger...", attachments=[], embed=None, view=None)
        await start_dueling_grounds_normal_battle(interaction, self.owner_id, self.user_name)

    @discord.ui.button(label="Ranked", style=discord.ButtonStyle.success)
    async def ranked_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.modes.ranked import start_dueling_grounds_ranked_battle
        await safe_edit_interaction_message(interaction, content="Searching the Ranked ladder for a challenger...", attachments=[], embed=None, view=None)
        await start_dueling_grounds_ranked_battle(interaction, self.owner_id, self.user_name)

    @discord.ui.button(label="Arena", style=discord.ButtonStyle.secondary, disabled=True)
    async def arena_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="Tournament", style=discord.ButtonStyle.secondary, disabled=True, row=1)
    async def tournament_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="Lobby", style=discord.ButtonStyle.secondary, row=1)
    async def lobby_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = DuelingGroundsLobbyView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments=[], embed=None, view=view)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = BattleHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=battle_hub_summary_text(self.owner_id), attachments_factory=lambda: [view.make_file()], embed=None, view=view)


class RankedLeaderboardView(discord.ui.View):
    PAGE_SIZE = 20
    MAX_ENTRIES = 200

    def __init__(self, owner_id: int, rows: Sequence, page: int = 0):
        """rows: ranked leaderboard rows from db.ranked_leaderboard(), best first."""
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.entries = list(rows[:self.MAX_ENTRIES])
        self.page = page
        self.max_page = max(0, math.ceil(len(self.entries) / self.PAGE_SIZE) - 1) if self.entries else 0
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This leaderboard belongs to someone else.", ephemeral=True)
            return False
        return True

    def _sync_buttons(self):
        self.page = max(0, min(self.page, self.max_page))
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.max_page

    async def summary_text(self) -> str:
        total_entries = len(self.entries)
        if total_entries <= 0:
            return "No ranked players are on the leaderboard yet."
        start_index = self.page * self.PAGE_SIZE
        end_index = min(start_index + self.PAGE_SIZE, total_entries)
        page_rows = self.entries[start_index:end_index]
        # Only this page's names are looked up, all at once.
        names = await resolve_global_discord_user_names([int(row["user_id"]) for row in page_rows])
        page_entries = [
            f"**{index}.** {names[int(row['user_id'])]} — **{int(row['ranked_elo'] or RANKED_STARTING_ELO)} ELO** • **{ranked_gxe_from_row(row):.1f}% GXE**"
            for index, row in enumerate(page_rows, start=start_index + 1)
        ]
        lines = [
            f"**Ranked Leaderboard** • Page **{self.page + 1}/{self.max_page + 1}** • Showing **{start_index + 1}-{end_index}** of **{total_entries}** players"
        ]
        if total_entries >= self.MAX_ENTRIES:
            lines.append(f"Top **{self.MAX_ENTRIES}** players only.")
        lines.extend(page_entries)
        return "\n".join(lines)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._sync_buttons()
        await safe_acknowledge_interaction(interaction, thinking=False)  # name lookups can take a moment
        await safe_edit_interaction_message(interaction, content=await self.summary_text(), attachments=[], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.max_page, self.page + 1)
        self._sync_buttons()
        await safe_acknowledge_interaction(interaction, thinking=False)  # name lookups can take a moment
        await safe_edit_interaction_message(interaction, content=await self.summary_text(), attachments=[], view=self)


class ArenaEnabledDuelingGroundsHubView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        # Tournament is a bot-creator-only PvP option. Non-creators still see it,
        # but the button is disabled in the Dueling Grounds hub.
        self.tournament_button.disabled = not is_bot_creator(self.owner_id)
        self.remove_item(self.back_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message('This Dueling Grounds hub belongs to someone else.', ephemeral=True)
            return False
        return True

    def make_file(self) -> discord.File:
        return discord.File(render_dueling_grounds_hub_page(self.user_name, self.owner_id), filename='dueling_grounds_hub.png')

    def summary_text(self) -> str:
        return 'Choose a Dueling Grounds mode. Normal starts a random duelist battle, Ranked uses ELO + GXE, Arena opens a ladder, Tournament runs a 16-player best-of-3 bracket, and Lobby creates custom matches.'

    @discord.ui.button(label='Normal', style=discord.ButtonStyle.primary, row=0)
    async def normal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.modes.normal import start_dueling_grounds_normal_battle
        await safe_edit_interaction_message(interaction, content='Searching the Dueling Grounds for a challenger...', attachments=[], embed=None, view=None)
        await start_dueling_grounds_normal_battle(interaction, self.owner_id, self.user_name)

    @discord.ui.button(label='Ranked', style=discord.ButtonStyle.success, row=1)
    async def ranked_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.modes.ranked import start_dueling_grounds_ranked_battle
        await safe_edit_interaction_message(interaction, content='Searching the Ranked ladder for a challenger...', attachments=[], embed=None, view=None)
        await start_dueling_grounds_ranked_battle(interaction, self.owner_id, self.user_name)

    @discord.ui.button(label='Arena', style=discord.ButtonStyle.success, row=1)
    async def arena_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.ui.arena import ArenaHubView
        view = ArenaHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)

    @discord.ui.button(label='Tournament', style=discord.ButtonStyle.danger, row=2)
    async def tournament_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.ui.tournament import TournamentHubView
        if not is_bot_creator(interaction.user.id):
            await interaction.response.send_message('Only the bot creator can use Tournament.', ephemeral=True)
            return
        view = TournamentHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments_factory=lambda: [view.make_file()], embed=None, view=view)

    @discord.ui.button(label='Lobby', style=discord.ButtonStyle.primary, row=0)
    async def lobby_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = DuelingGroundsLobbyView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments=[], embed=None, view=view)

    @discord.ui.button(label='Back', style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = BattleHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=battle_hub_summary_text(self.owner_id), attachments_factory=lambda: [view.make_file()], embed=None, view=view)


def make_pvp_section_view(owner_id: int, user_name: str, section: Optional[str] = None) -> tuple[str, Optional[discord.File], discord.ui.View]:
    from kuribot.ui.arena import ArenaHubView
    from kuribot.ui.tournament import TournamentHubView
    normalized = str(section or "").strip().lower()
    if normalized == 'lobby':
        view = DuelingGroundsLobbyView(owner_id, user_name)
        return view.summary_text(), view.make_file(), view
    if normalized == 'arena':
        view = ArenaHubView(owner_id, user_name)
        return view.summary_text(), view.make_file(), view
    if normalized == 'tournament':
        if not is_bot_creator(owner_id):
            view = ArenaEnabledDuelingGroundsHubView(owner_id, user_name)
            return 'Tournament is currently bot-creator-only.\n' + view.summary_text(), view.make_file(), view
        view = TournamentHubView(owner_id, user_name)
        return view.summary_text(), view.make_file(), view
    view = ArenaEnabledDuelingGroundsHubView(owner_id, user_name)
    return view.summary_text(), view.make_file(), view
