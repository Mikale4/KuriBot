"""Campaign menus."""

from __future__ import annotations

import asyncio
from typing import List, Optional

import discord

from kuribot.config import (
    ATTRIBUTES, CAMPAIGN_AREAS, CAMPAIGN_HARD_COMPLETION_GOLD,
    CAMPAIGN_HARD_MODE_ENEMY_SUPPORT_BONUSES, CAMPAIGN_REQUIRED_WINS_PER_DUELIST,
)
from kuribot.database import db
from kuribot.monsters import (
    find_monster_by_name_and_attribute, is_signature_monster_row, monster_category_for_row,
    resolve_monster_type_name,
)
from kuribot.portraits import (
    get_campaign_duelist_portrait_path, make_campaign_duelist_portrait_file,
)
from kuribot.campaign import (
    build_campaign_hard_completion_embed, campaign_duelist_record_text, campaign_duelist_wins,
    campaign_hard_completion_choice_claimed, campaign_hard_completion_choice_key,
    campaign_hard_mode_unlocked, claim_campaign_hard_completion_choice, get_campaign_area,
    get_campaign_duelist, get_campaign_duelist_keys, is_campaign_area_unlocked,
    is_campaign_duelist_available,
)
from kuribot.render.hubs import make_campaign_area_file, make_campaign_hub_file
from kuribot.ui.common import disable_view_items, safe_edit_interaction_message


class CampaignDuelistSelect(discord.ui.Select):
    def __init__(self, owner_id: int, user_name: str, area_key: str, hard_mode: bool = False):
        self.owner_id = owner_id
        self.user_name = user_name
        self.area_key = area_key
        self.hard_mode = bool(hard_mode)
        area = get_campaign_area(area_key) or CAMPAIGN_AREAS[0]
        options = []
        for duelist_key in get_campaign_duelist_keys(area["key"]):
            duelist = get_campaign_duelist(area["key"], duelist_key) or {"name": duelist_key}
            wins = campaign_duelist_wins(owner_id, area["key"], duelist_key, hard_mode=self.hard_mode)
            status = f"{campaign_duelist_record_text(owner_id, area['key'], duelist_key, hard_mode=self.hard_mode)} • Progress {wins}/{CAMPAIGN_REQUIRED_WINS_PER_DUELIST}"
            options.append(discord.SelectOption(label=duelist["name"][:100], value=duelist_key, description=status[:100], emoji="⚔️"))
        placeholder = f"Choose a duelist in {area['label']}" + (" - Hard Mode" if self.hard_mode else "")
        super().__init__(placeholder=placeholder[:150], min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        duelist_key = self.values[0]
        if not is_campaign_duelist_available(self.owner_id, self.area_key, duelist_key):
            await interaction.response.send_message("That duelist is not available yet.", ephemeral=True)
            return
        duel_view = CampaignDuelistIntroView(self.owner_id, self.user_name, self.area_key, duelist_key, hard_mode=self.hard_mode)
        await safe_edit_interaction_message(interaction, content=f"{duel_view.duelist_name} steps forward.", attachments=duel_view.make_attachments(), embed=duel_view.make_embed(), view=duel_view)


class CampaignDuelistIntroView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, area_key: str, duelist_key: str, hard_mode: bool = False):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.area_key = area_key
        self.duelist_key = duelist_key
        self.hard_mode = bool(hard_mode)
        self.area = get_campaign_area(area_key) or CAMPAIGN_AREAS[0]
        self.duelist = get_campaign_duelist(area_key, duelist_key) or {"name": duelist_key, "pre": "Let's duel!"}
        self.duelist_name = self.duelist["name"]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This campaign hub belongs to someone else.", ephemeral=True)
            return False
        return True

    def make_file(self) -> discord.File:
        return make_campaign_area_file(self.user_name, self.owner_id, self.area_key, hard_mode=self.hard_mode)

    def make_portrait_file(self) -> Optional[discord.File]:
        return make_campaign_duelist_portrait_file(self.area_key, self.duelist_key, self.duelist_name)

    def make_attachments(self) -> List[discord.File]:
        attachments: List[discord.File] = [self.make_file()]
        portrait_file = self.make_portrait_file()
        if portrait_file is not None:
            attachments.append(portrait_file)
        return attachments

    def make_embed(self) -> discord.Embed:
        wins = campaign_duelist_wins(self.owner_id, self.area_key, self.duelist_key, hard_mode=self.hard_mode)
        embed = discord.Embed(title=f"{self.duelist_name}", description=self.duelist.get("pre", "Let's duel!"), color=discord.Color.blurple())
        embed.add_field(name="Area", value=self.area["label"] + (" - Hard Mode" if self.hard_mode else ""), inline=True)
        embed.add_field(name="Progress", value=f"{wins}/{CAMPAIGN_REQUIRED_WINS_PER_DUELIST} wins", inline=True)
        embed.add_field(name="Record", value=campaign_duelist_record_text(self.owner_id, self.area_key, self.duelist_key, hard_mode=self.hard_mode), inline=True)
        if self.hard_mode:
            bonuses = [int(value) for value in CAMPAIGN_HARD_MODE_ENEMY_SUPPORT_BONUSES.get(self.area_key, [25])]
            bonus_label = f"{bonuses[0]}%" if bonuses and bonuses[0] == bonuses[-1] else f"{bonuses[0]}%/{bonuses[-1]}%"
            embed.add_field(name="Battle Rules", value=f"Enemy uses an aligned generated deck ({bonus_label}) plus generated equip spells.", inline=False)
        if get_campaign_duelist_portrait_path(self.area_key, self.duelist_key, self.duelist_name) is not None:
            embed.set_thumbnail(url="attachment://campaign_duelist.png")
        return embed

    @discord.ui.button(label="Begin Battle", style=discord.ButtonStyle.success)
    async def begin_battle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.modes.campaign import start_campaign_stage_battle
        await interaction.response.defer()
        await start_campaign_stage_battle(interaction, self.owner_id, self.user_name, self.area_key, self.duelist_key, hard_mode=self.hard_mode)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=3)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = CampaignAreaView(self.owner_id, self.user_name, self.area_key, hard_mode=self.hard_mode)
        await safe_edit_interaction_message(interaction, content=f"Choose a duelist in **{self.area['label']}**.", attachments=[], embed=None, view=view)


class CampaignHubView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, hard_mode: bool = False):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.hard_mode = bool(hard_mode)
        self.hard_mode_button.disabled = not campaign_hard_mode_unlocked(owner_id)
        self.hard_mode_button.style = discord.ButtonStyle.danger
        self.hard_mode_button.label = "Hard Mode On" if self.hard_mode else "Hard Mode"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This campaign hub belongs to someone else.", ephemeral=True)
            return False
        return True

    def make_file(self) -> discord.File:
        return make_campaign_hub_file(self.user_name, self.owner_id, hard_mode=self.hard_mode)

    async def _open_area(self, interaction: discord.Interaction, area_key: str):
        if self.hard_mode and not campaign_hard_mode_unlocked(self.owner_id):
            await interaction.response.send_message("Hard Mode unlocks after you clear Battle City.", ephemeral=True)
            return
        if not is_campaign_area_unlocked(self.owner_id, area_key):
            await interaction.response.send_message("That area is still locked.", ephemeral=True)
            return
        view = CampaignAreaView(self.owner_id, self.user_name, area_key, hard_mode=self.hard_mode)
        area = get_campaign_area(area_key) or CAMPAIGN_AREAS[0]
        area_label = area['label'] + (" - Hard Mode" if self.hard_mode else "")
        await safe_edit_interaction_message(interaction, content=f"Choose a duelist in **{area_label}**.", attachments=[await asyncio.to_thread(view.make_file)], embed=None, view=view)

    @discord.ui.button(label="Domino City", style=discord.ButtonStyle.primary)
    async def domino_city_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open_area(interaction, 'domino_city')

    @discord.ui.button(label="Duelist Kingdom", style=discord.ButtonStyle.primary)
    async def duelist_kingdom_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open_area(interaction, 'duelist_kingdom')

    @discord.ui.button(label="Battle City", style=discord.ButtonStyle.primary)
    async def battle_city_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open_area(interaction, 'battle_city')

    @discord.ui.button(label="Duel Tower", style=discord.ButtonStyle.primary)
    async def duel_tower_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._open_area(interaction, 'kaiba_corp')

    @discord.ui.button(label="Hard Mode", style=discord.ButtonStyle.danger, row=1)
    async def hard_mode_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not campaign_hard_mode_unlocked(self.owner_id):
            await interaction.response.send_message("Hard Mode unlocks after you clear Battle City.", ephemeral=True)
            return
        next_mode = not self.hard_mode
        view = CampaignHubView(self.owner_id, self.user_name, hard_mode=next_mode)
        content = "Choose a campaign area." if not next_mode else "Choose a campaign area. Hard Mode enabled."
        await safe_edit_interaction_message(interaction, content=content, attachments_factory=lambda: [view.make_file()], embed=None, view=view)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.ui.dueling_grounds import battle_hub_summary_text, BattleHubView
        view = BattleHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=battle_hub_summary_text(self.owner_id), attachments_factory=lambda: [view.make_file()], embed=None, view=view)


class CampaignAreaView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, area_key: str, hard_mode: bool = False):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.area_key = area_key
        self.hard_mode = bool(hard_mode)
        self.area = get_campaign_area(area_key) or CAMPAIGN_AREAS[0]
        self.duelist_select = CampaignDuelistSelect(owner_id, user_name, self.area['key'], hard_mode=self.hard_mode)
        self.add_item(self.duelist_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This campaign hub belongs to someone else.", ephemeral=True)
            return False
        return True

    def make_file(self) -> discord.File:
        return make_campaign_area_file(self.user_name, self.owner_id, self.area['key'], hard_mode=self.hard_mode)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = CampaignHubView(self.owner_id, self.user_name, hard_mode=self.hard_mode)
        content = "Choose a campaign area." if not self.hard_mode else "Choose a campaign area. Hard Mode enabled."
        await safe_edit_interaction_message(interaction, content=content, attachments_factory=lambda: [view.make_file()], embed=None, view=view)


def make_campaign_result_embed(area_key: str, area_label: str, duelist_key: str, duelist_name: str, speech: str, player_won: bool, progress_text: str = "") -> discord.Embed:
    outcome_label = "Victory" if player_won else ("Defeat" if speech and not speech.startswith("Both") else "Draw")
    embed = discord.Embed(
        title=f"{duelist_name}",
        description=speech or ("Excellent duel." if player_won else "Victory is mine."),
        color=discord.Color.green() if player_won else (discord.Color.orange() if outcome_label == "Draw" else discord.Color.red()),
    )
    embed.add_field(name="Area", value=area_label, inline=True)
    embed.add_field(name="Outcome", value=outcome_label, inline=True)
    if progress_text:
        embed.add_field(name="Progress", value=progress_text, inline=False)
    if get_campaign_duelist_portrait_path(area_key, duelist_key, duelist_name) is not None:
        embed.set_thumbnail(url="attachment://campaign_duelist.png")
    return embed


class CampaignHardRewardBossModal(discord.ui.Modal, title="Hard Mode Reward • Extra Deck Monster"):
    monster_name = discord.ui.TextInput(label="Extra Deck monster name", required=True, max_length=100, placeholder="Exact monster name")
    attribute = discord.ui.TextInput(label="Attribute", required=True, max_length=20, placeholder="FIRE, WATER, EARTH, WIND, LIGHT, DARK, or DIVINE")
    monster_type = discord.ui.TextInput(label="Type", required=True, max_length=40, placeholder="Example: Dragon")

    def __init__(self, reward_view: "CampaignHardCompletionRewardView"):
        super().__init__()
        self.reward_view = reward_view

    async def on_submit(self, interaction: discord.Interaction):
        monster_name = str(self.monster_name.value or '').strip()
        attribute_name = str(self.attribute.value or '').strip().upper()
        resolved_type = resolve_monster_type_name(str(self.monster_type.value or ''))
        if attribute_name not in ATTRIBUTES:
            await interaction.response.send_message(f"Invalid attribute. Choose one of: {', '.join(ATTRIBUTES)}.", ephemeral=True)
            return
        if resolved_type is None:
            await interaction.response.send_message("That monster type is invalid.", ephemeral=True)
            return
        monster_row = find_monster_by_name_and_attribute(monster_name, attribute_name)
        if monster_row is None or monster_category_for_row(monster_row) != 'boss':
            await interaction.response.send_message("That exact Extra Deck Monster could not be found.", ephemeral=True)
            return
        if not claim_campaign_hard_completion_choice(self.reward_view.owner_id, 'boss_monster'):
            await interaction.response.send_message("Your Hard Mode reward was already claimed.", ephemeral=True)
            return
        db.add_monster_to_inventory(self.reward_view.owner_id, str(monster_row['monster_id']), [resolved_type], monster_attribute=attribute_name)
        disable_view_items(self.reward_view)
        await interaction.response.edit_message(embed=self.reward_view.build_embed("Reward claimed: Extra Deck Monster."), view=self.reward_view)
        await interaction.followup.send(f"You received **{monster_row['name']}** as an Extra Deck Monster reward.", ephemeral=True)


class CampaignHardRewardSignatureModal(discord.ui.Modal, title="Hard Mode Reward • Extra Deck Monster"):
    monster_name = discord.ui.TextInput(label="Signature monster name", required=True, max_length=100, placeholder="Exact monster name")
    attribute = discord.ui.TextInput(label="Attribute", required=True, max_length=20, placeholder="FIRE, WATER, EARTH, WIND, LIGHT, DARK, or DIVINE")
    monster_type_one = discord.ui.TextInput(label="Type One", required=True, max_length=40, placeholder="Example: Warrior")
    monster_type_two = discord.ui.TextInput(label="Type Two", required=True, max_length=40, placeholder="Example: Dragon")

    def __init__(self, reward_view: "CampaignHardCompletionRewardView"):
        super().__init__()
        self.reward_view = reward_view

    async def on_submit(self, interaction: discord.Interaction):
        monster_name = str(self.monster_name.value or '').strip()
        attribute_name = str(self.attribute.value or '').strip().upper()
        type_one = resolve_monster_type_name(str(self.monster_type_one.value or ''))
        type_two = resolve_monster_type_name(str(self.monster_type_two.value or ''))
        if attribute_name not in ATTRIBUTES:
            await interaction.response.send_message(f"Invalid attribute. Choose one of: {', '.join(ATTRIBUTES)}.", ephemeral=True)
            return
        if type_one is None or type_two is None:
            await interaction.response.send_message("One or both monster types are invalid.", ephemeral=True)
            return
        if type_one == type_two:
            await interaction.response.send_message("Signature reward monsters must use two different types.", ephemeral=True)
            return
        monster_row = find_monster_by_name_and_attribute(monster_name, attribute_name)
        if monster_row is None or not is_signature_monster_row(monster_row):
            await interaction.response.send_message("That exact Extra Deck Monster could not be found.", ephemeral=True)
            return
        if not claim_campaign_hard_completion_choice(self.reward_view.owner_id, 'signature_monster'):
            await interaction.response.send_message("Your Hard Mode reward was already claimed.", ephemeral=True)
            return
        db.add_monster_to_inventory(self.reward_view.owner_id, str(monster_row['monster_id']), [type_one, type_two], monster_attribute=attribute_name)
        disable_view_items(self.reward_view)
        await interaction.response.edit_message(embed=self.reward_view.build_embed("Reward claimed: Extra Deck Monster."), view=self.reward_view)
        await interaction.followup.send(f"You received **{monster_row['name']}** as an Extra Deck Monster reward.", ephemeral=True)


class CampaignHardCompletionRewardView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.user_name = user_name
        for child in list(self.children):
            if isinstance(child, discord.ui.Button) and child.label in {"Extra Deck Monster", "Extra Deck Monster", "1 Million Gold"}:
                self.remove_item(child)
        if campaign_hard_completion_choice_claimed(owner_id):
            disable_view_items(self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("These reward buttons belong to someone else.", ephemeral=True)
            return False
        return True

    def build_embed(self, status_text: str = "Claim your Extra Deck Themed Pack. This reward can only be claimed once.") -> discord.Embed:
        embed = build_campaign_hard_completion_embed(self.user_name)
        claimed_key = campaign_hard_completion_choice_key(self.owner_id)
        if claimed_key:
            status_text = f"Reward already claimed: **{claimed_key.replace('_', ' ').title()}**."
        embed.add_field(name="Status", value=status_text, inline=False)
        return embed

    @discord.ui.button(label="Claim Extra Deck Themed Pack", style=discord.ButtonStyle.primary)
    async def all_special_support_cards_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not claim_campaign_hard_completion_choice(self.owner_id, 'boss_themed_pack'):
            await interaction.response.send_message("Your Hard Mode reward was already claimed.", ephemeral=True)
            return
        db.add_pack(self.owner_id, "boss_themed", 1)
        disable_view_items(self)
        await interaction.response.edit_message(embed=self.build_embed("Reward claimed: Extra Deck Themed Pack."), view=self)

    @discord.ui.button(label="Extra Deck Monster", style=discord.ButtonStyle.secondary)
    async def boss_monster_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CampaignHardRewardBossModal(self))

    @discord.ui.button(label="Extra Deck Monster", style=discord.ButtonStyle.secondary)
    async def signature_monster_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CampaignHardRewardSignatureModal(self))

    @discord.ui.button(label="1 Million Gold", style=discord.ButtonStyle.success)
    async def gold_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not claim_campaign_hard_completion_choice(self.owner_id, 'one_million_gold'):
            await interaction.response.send_message("Your Hard Mode reward was already claimed.", ephemeral=True)
            return
        db.add_gold(self.owner_id, CAMPAIGN_HARD_COMPLETION_GOLD)
        disable_view_items(self)
        await interaction.response.edit_message(embed=self.build_embed("Reward claimed: 1 Million Gold."), view=self)
