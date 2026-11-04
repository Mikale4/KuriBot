"""Battle speed and notification settings menus."""

from __future__ import annotations

import discord

from kuribot.settings import (
    BATTLE_SPEED_DESCRIPTIONS, battle_speed_label, BATTLE_SPEED_LABELS,
    NOTIFICATION_SETTING_DESCRIPTIONS, NOTIFICATION_SETTING_LABELS, notification_status_label,
)
from kuribot.database import db
from kuribot.ui.common import safe_edit_interaction_message


class BattleSpeedSelect(discord.ui.Select):
    def __init__(self, owner_id: int):
        self.owner_id = owner_id
        current_speed = db.get_battle_speed(owner_id)
        options = [
            discord.SelectOption(
                label=label,
                value=key,
                description=BATTLE_SPEED_DESCRIPTIONS[key],
                default=(key == current_speed),
            )
            for key, label in BATTLE_SPEED_LABELS.items()
        ]
        super().__init__(placeholder="Choose a battle speed", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        chosen = str(self.values[0])
        db.set_battle_speed(self.owner_id, chosen)
        await safe_edit_interaction_message(interaction, 
            content=f"Battle Speed set to **{battle_speed_label(chosen)}** — {BATTLE_SPEED_DESCRIPTIONS[chosen]}.",
            view=BattleSpeedSettingsView(self.owner_id),
        )


class BattleSpeedSettingsView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.add_item(BattleSpeedSelect(owner_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("These settings belong to someone else.", ephemeral=True)
            return False
        return True


class NotificationSelect(discord.ui.Select):
    def __init__(self, owner_id: int):
        self.owner_id = owner_id
        enabled = db.notifications_enabled(owner_id)
        current_value = 'on' if enabled else 'off'
        options = [
            discord.SelectOption(
                label=label,
                value=key,
                description=NOTIFICATION_SETTING_DESCRIPTIONS[key],
                default=(key == current_value),
            )
            for key, label in NOTIFICATION_SETTING_LABELS.items()
        ]
        super().__init__(placeholder="Choose a notification setting", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        chosen = str(self.values[0]).strip().lower()
        enabled = chosen == 'on'
        db.set_notifications_enabled(self.owner_id, enabled)
        await safe_edit_interaction_message(
            interaction,
            content=(
                f"Notifications set to **{notification_status_label(enabled)}**. "
                + ("Kuribot can DM you when `;ping` affects you." if enabled else "Kuribot will no longer DM you when `;ping` affects you.")
            ),
            view=NotificationSettingsView(self.owner_id),
        )


class NotificationSettingsView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.add_item(NotificationSelect(owner_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("These settings belong to someone else.", ephemeral=True)
            return False
        return True


class BotSettingsView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=300)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("These settings belong to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Battle Speed", style=discord.ButtonStyle.primary)
    async def battle_speed_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_speed = db.get_battle_speed(self.owner_id)
        await interaction.response.send_message(
            f"Current Battle Speed: **{battle_speed_label(current_speed)}** — {BATTLE_SPEED_DESCRIPTIONS[current_speed]}.",
            view=BattleSpeedSettingsView(self.owner_id),
            ephemeral=True,
        )

    @discord.ui.button(label="Notifications", style=discord.ButtonStyle.secondary)
    async def notifications_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        enabled = db.notifications_enabled(self.owner_id)
        await interaction.response.send_message(
            f"Current Notifications setting: **{notification_status_label(enabled)}**.",
            view=NotificationSettingsView(self.owner_id),
            ephemeral=True,
        )
