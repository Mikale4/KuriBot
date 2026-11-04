"""Admin menus (boss monsters, gifts, mail, debug lock, resets)."""

from __future__ import annotations

import asyncio
import math
import sqlite3
from typing import List, Optional

import discord

from kuribot.config import DEBUG_ALLOWED_CHANNEL_ID
from kuribot.core import (
    debug_channel_lock_enabled, debug_channel_lock_status_text, is_bot_creator,
    set_debug_channel_lock,
)
from kuribot.database import db
from kuribot.discord_helpers import resolve_mail_username, send_ping_notification_dm
from kuribot.monsters import (
    encode_monster_types, format_monster_types, is_boss_monster_row,
    random_monster_attribute_for_row, roll_monster_types_for_row,
)
from kuribot.mail import reward_type_label
from kuribot.profile import wipe_profile_tag_assets
from kuribot.ui.common import safe_acknowledge_interaction, safe_edit_interaction_message


class BossMonsterTargetModal(discord.ui.Modal, title="Send Extra Deck Monster"):
    target_user_id = discord.ui.TextInput(
        label="Target Discord ID",
        required=True,
        max_length=25,
        placeholder="Enter the user's Discord ID",
    )

    def __init__(self, parent_view: "BossMonsterAdminView"):
        super().__init__()
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        raw_target = str(self.target_user_id).strip()
        if not raw_target.isdigit():
            await interaction.response.send_message("Enter a valid registered user's Discord ID.", ephemeral=True)
            return
        target_id = int(raw_target)
        if not db.is_registered_user(target_id):
            await interaction.response.send_message("That user is not registered with the bot.", ephemeral=True)
            return
        monster_row = self.parent_view.selected_monster_row()
        if monster_row is None:
            await interaction.response.send_message("Pick an Extra Deck Monster first.", ephemeral=True)
            return
        user_name = await resolve_mail_username(target_id, interaction.guild)
        monster_types = roll_monster_types_for_row(monster_row)
        monster_attribute = random_monster_attribute_for_row(monster_row)
        body = (
            f"Hello {user_name},\n\n"
            f"A Kuribot Admin sent you **{monster_row['name']}** (**{monster_attribute}**).\n\n"
            "The selected Extra Deck Monster is attached to this inbox message. Claim the attachment to add it to your collection."
        )
        reward_bundle = [{
            'type': 'monster',
            'amount': 1,
            'monster_id': str(monster_row['monster_id']),
            'monster_attribute': monster_attribute,
            'monster_types': encode_monster_types(monster_types),
            'label': str(monster_row['name']),
        }]
        db.create_mail(target_id, body, sender_id=self.parent_view.owner_id, sender_name=self.parent_view.sender_name, reward_bundle=reward_bundle)
        reward_text = f"Extra Deck Monster attached: {monster_row['name']} ({monster_attribute})"
        dm_sent = await send_ping_notification_dm(target_id, body, sender_name=self.parent_view.sender_name, reward_text=reward_text)
        self.parent_view.last_status = (
            f"Sent **{monster_row['name']}** ({monster_attribute}) to **{target_id}** with monster types **{format_monster_types(monster_types)}**."
            + (" DM notification sent." if dm_sent else " DM notification could not be sent.")
        )
        self.parent_view.rebuild()
        await safe_edit_interaction_message(interaction, embed=self.parent_view.build_embed(), view=self.parent_view)


class BossMonsterSelect(discord.ui.Select):
    def __init__(self, parent_view: "BossMonsterAdminView"):
        self.parent_view = parent_view
        rows = parent_view.page_rows()
        options = []
        for row in rows:
            monster_id = str(row['monster_id'])
            options.append(discord.SelectOption(
                label=str(row['name'])[:100],
                value=monster_id,
                description=f"{row['attribute']} • Extra Deck Monster"[:100],
                default=monster_id == parent_view.selected_monster_id,
            ))
        if not options:
            options = [discord.SelectOption(label="No Extra Deck Monsters", value="0")]
        super().__init__(placeholder="Choose an Extra Deck Monster", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        if self.values == ["0"]:
            await safe_acknowledge_interaction(interaction)
            return
        self.parent_view.selected_monster_id = self.values[0]
        self.parent_view.last_status = f"Selected **{self.parent_view.selected_monster_name()}**."
        self.parent_view.rebuild()
        await safe_edit_interaction_message(interaction, embed=self.parent_view.build_embed(), view=self.parent_view)


class BossMonsterAdminView(discord.ui.View):
    PAGE_SIZE = 25

    def __init__(self, owner_id: int, sender_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.sender_name = sender_name
        self.page = 0
        self.selected_monster_id: Optional[str] = None
        self.last_status = "Use the dropdown to choose an Extra Deck Monster, then press **Send**."
        self.rows = [row for row in db.all_monsters() if is_boss_monster_row(row)]
        self.rebuild()

    def selected_monster_row(self) -> Optional[sqlite3.Row]:
        if not self.selected_monster_id:
            return None
        for row in self.rows:
            if str(row['monster_id']) == self.selected_monster_id:
                return row
        return None

    def selected_monster_name(self) -> str:
        row = self.selected_monster_row()
        return str(row['name']) if row is not None else "None"

    def total_pages(self) -> int:
        return max(1, math.ceil(max(1, len(self.rows)) / self.PAGE_SIZE))

    def page_rows(self) -> List[sqlite3.Row]:
        start = self.page * self.PAGE_SIZE
        end = start + self.PAGE_SIZE
        return self.rows[start:end]

    def build_embed(self) -> discord.Embed:
        total = len(self.rows)
        total_pages = self.total_pages()
        embed = discord.Embed(title="Extra Deck Monster Mail Admin", color=discord.Color.gold())
        if not self.rows:
            embed.description = "No Extra Deck Monsters were found in the bot."
            return embed
        page_rows = self.page_rows()
        start_index = self.page * self.PAGE_SIZE
        lines = [f"**{start_index + idx}.** {row['name']} ({row['attribute']})" for idx, row in enumerate(page_rows, start=1)]
        embed.description = "\n".join(lines)
        selected_row = self.selected_monster_row()
        embed.add_field(name="Selected", value=(f"**{selected_row['name']}** ({selected_row['attribute']})" if selected_row else "None selected"), inline=False)
        embed.add_field(name="Status", value=self.last_status, inline=False)
        embed.set_footer(text=f"Page {self.page + 1}/{total_pages} • {total} Extra Deck Monsters")
        return embed

    def rebuild(self):
        self.clear_items()
        if self.selected_monster_row() is None:
            page_rows = self.page_rows()
            self.selected_monster_id = str(page_rows[0]['monster_id']) if page_rows else None
        if self.rows:
            self.add_item(BossMonsterSelect(self))
        self.prev_button.disabled = self.page <= 0 or not self.rows
        self.next_button.disabled = self.page >= self.total_pages() - 1 or not self.rows
        self.send_button.disabled = self.selected_monster_row() is None
        self.add_item(self.prev_button)
        self.add_item(self.next_button)
        self.add_item(self.send_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This Extra Deck Monster panel belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=2)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=2)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.total_pages() - 1, self.page + 1)
        self.rebuild()
        await safe_edit_interaction_message(interaction, embed=self.build_embed(), view=self)

    @discord.ui.button(label="Send", style=discord.ButtonStyle.success, row=2)
    async def send_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.selected_monster_row() is None:
            await interaction.response.send_message("Pick an Extra Deck Monster first.", ephemeral=True)
            return
        await interaction.response.send_modal(BossMonsterTargetModal(self))


class AdminMailMessageModal(discord.ui.Modal, title="Send Inbox Message"):
    target_user_id = discord.ui.TextInput(
        label="Target Discord ID",
        required=False,
        max_length=25,
        placeholder="Leave blank for mass message",
    )
    message_body = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1200,
        placeholder="Write the inbox message here.",
    )

    def __init__(self, sender_id: int, sender_name: str, *, mass: bool = False):
        super().__init__()
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.mass = mass
        if self.mass:
            self.target_user_id.required = False
            self.target_user_id.default = "All registered users"

    async def on_submit(self, interaction: discord.Interaction):
        body = str(self.message_body).strip()
        if not body:
            await interaction.response.send_message("Message cannot be empty.", ephemeral=True)
            return
        if self.mass:
            targets = db.get_registered_user_ids()
            if not targets:
                await interaction.response.send_message("No registered users were found.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            dm_sent = 0
            for idx, user_id in enumerate(targets, start=1):
                db.create_mail(user_id, body, sender_id=self.sender_id, sender_name=self.sender_name)
                if await send_ping_notification_dm(user_id, body, sender_name=self.sender_name):
                    dm_sent += 1
                if idx % 25 == 0:
                    await asyncio.sleep(0)
            await interaction.followup.send(
                f"Sent a message to **{len(targets)}** registered user(s). DM notifications delivered to **{dm_sent}** user(s).",
                ephemeral=True,
            )
            return
        raw_target = str(self.target_user_id).strip()
        if not raw_target.isdigit():
            await interaction.response.send_message("Enter a valid registered user's Discord ID.", ephemeral=True)
            return
        target_id = int(raw_target)
        if not db.is_registered_user(target_id):
            await interaction.response.send_message("That user is not registered with the bot.", ephemeral=True)
            return
        db.create_mail(target_id, body, sender_id=self.sender_id, sender_name=self.sender_name)
        dm_sent = await send_ping_notification_dm(target_id, body, sender_name=self.sender_name)
        await interaction.response.send_message(
            f"Inbox message sent to **{target_id}**.{' DM notification sent.' if dm_sent else ' DM notification could not be sent.'}",
            ephemeral=True,
        )


class AdminGiftModal(discord.ui.Modal):
    target_user_id = discord.ui.TextInput(
        label="Target Discord ID",
        required=False,
        max_length=25,
        placeholder="Leave blank for mass gift",
    )
    amount = discord.ui.TextInput(
        label="Amount",
        required=True,
        max_length=10,
        placeholder="Enter a positive whole number",
    )
    message_body = discord.ui.TextInput(
        label="Message",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1200,
        placeholder="Write the gift message here.",
    )

    def __init__(self, sender_id: int, sender_name: str, reward_type: str, *, mass: bool = False):
        title = f"Send {reward_type.title()} Gift" if not mass else f"Mass {reward_type.title()} Gift"
        super().__init__(title=title)
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.reward_type = reward_type
        self.mass = mass
        if self.mass:
            self.target_user_id.required = False
            self.target_user_id.default = "All registered users"

    async def on_submit(self, interaction: discord.Interaction):
        raw_amount = str(self.amount).strip()
        normalized_amount = raw_amount.replace(',', '').replace('_', '').replace(' ', '')
        if not normalized_amount.isdigit() or int(normalized_amount) <= 0:
            await interaction.response.send_message("Amount must be a positive whole number.", ephemeral=True)
            return
        reward_amount = int(normalized_amount)
        body = str(self.message_body).strip()
        if not body:
            await interaction.response.send_message("Message cannot be empty.", ephemeral=True)
            return
        reward_label = reward_type_label(self.reward_type, reward_amount)
        if self.mass:
            targets = db.get_registered_user_ids()
            if not targets:
                await interaction.response.send_message("No registered users were found.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            dm_sent = 0
            for idx, user_id in enumerate(targets, start=1):
                db.create_mail(user_id, body, sender_id=self.sender_id, sender_name=self.sender_name, reward_type=self.reward_type, reward_amount=reward_amount)
                if await send_ping_notification_dm(user_id, body, sender_name=self.sender_name, reward_text=reward_label):
                    dm_sent += 1
                if idx % 25 == 0:
                    await asyncio.sleep(0)
            await interaction.followup.send(
                f"Sent **{reward_label}** to **{len(targets)}** registered user(s). DM notifications delivered to **{dm_sent}** user(s).",
                ephemeral=True,
            )
            return
        raw_target = str(self.target_user_id).strip()
        if not raw_target.isdigit():
            await interaction.response.send_message("Enter a valid registered user's Discord ID.", ephemeral=True)
            return
        target_id = int(raw_target)
        if not db.is_registered_user(target_id):
            await interaction.response.send_message("That user is not registered with the bot.", ephemeral=True)
            return
        db.create_mail(target_id, body, sender_id=self.sender_id, sender_name=self.sender_name, reward_type=self.reward_type, reward_amount=reward_amount)
        dm_sent = await send_ping_notification_dm(target_id, body, sender_name=self.sender_name, reward_text=reward_label)
        await interaction.response.send_message(
            f"Gift mail sent to **{target_id}** with **{reward_label}**.{' DM notification sent.' if dm_sent else ' DM notification could not be sent.'}",
            ephemeral=True,
        )


class PingGiftMenuView(discord.ui.View):
    def __init__(self, owner_id: int, sender_name: str, *, mass: bool = False):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.sender_name = sender_name
        self.mass = mass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This admin panel belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Gold", style=discord.ButtonStyle.success)
    async def gold_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AdminGiftModal(self.owner_id, self.sender_name, "gold", mass=self.mass))

    @discord.ui.button(label="Tickets", style=discord.ButtonStyle.success)
    async def tickets_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AdminGiftModal(self.owner_id, self.sender_name, "tickets", mass=self.mass))

    @discord.ui.button(label="Random Packs", style=discord.ButtonStyle.success)
    async def premium_tickets_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AdminGiftModal(self.owner_id, self.sender_name, "premium_tickets", mass=self.mass))

    @discord.ui.button(label="Cards", style=discord.ButtonStyle.success)
    async def cards_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AdminGiftModal(self.owner_id, self.sender_name, "cards", mass=self.mass))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_edit_interaction_message(interaction, content="Admin inbox tools:", view=PingMainView(self.owner_id, self.sender_name))


class PingMainView(discord.ui.View):
    def __init__(self, owner_id: int, sender_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.sender_name = sender_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This admin panel belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Message", style=discord.ButtonStyle.primary)
    async def message_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AdminMailMessageModal(self.owner_id, self.sender_name, mass=False))

    @discord.ui.button(label="Mass Message", style=discord.ButtonStyle.primary)
    async def mass_message_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AdminMailMessageModal(self.owner_id, self.sender_name, mass=True))

    @discord.ui.button(label="Gift", style=discord.ButtonStyle.success)
    async def gift_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_edit_interaction_message(interaction, content="Choose the gift type:", view=PingGiftMenuView(self.owner_id, self.sender_name, mass=False))

    @discord.ui.button(label="Mass Gift", style=discord.ButtonStyle.success)
    async def mass_gift_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_edit_interaction_message(interaction, content="Choose the mass gift type:", view=PingGiftMenuView(self.owner_id, self.sender_name, mass=True))


class DebugChannelLockView(discord.ui.View):
    def __init__(self, requester_id: int):
        super().__init__(timeout=300)
        self.requester_id = requester_id
        self.message: Optional[discord.Message] = None
        self._sync_buttons()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id or not is_bot_creator(interaction.user.id):
            await interaction.response.send_message("Only the bot creator can use this panel.", ephemeral=True)
            return False
        return True

    def _sync_buttons(self):
        self.on_button.disabled = debug_channel_lock_enabled()
        self.off_button.disabled = not debug_channel_lock_enabled()

    @discord.ui.button(label="ON", style=discord.ButtonStyle.success, row=0)
    async def on_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        set_debug_channel_lock(True)
        self._sync_buttons()
        await safe_edit_interaction_message(
            interaction,
            content=f"✅ {debug_channel_lock_status_text()}\nThe bot will only send messages in <#{DEBUG_ALLOWED_CHANNEL_ID}> until you turn it off.",
            view=self,
        )

    @discord.ui.button(label="OFF", style=discord.ButtonStyle.danger, row=0)
    async def off_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        set_debug_channel_lock(False)
        self._sync_buttons()
        await safe_edit_interaction_message(
            interaction,
            content=f"✅ {debug_channel_lock_status_text()}\nThe bot can send messages normally again.",
            view=self,
        )

    async def on_timeout(self):
        self._sync_buttons()
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(content=f"{debug_channel_lock_status_text()}\nThis debug panel timed out.", view=self)
            except Exception:
                pass


class ResetConfirmView(discord.ui.View):
    def __init__(self, requester_id: int):
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Only the bot creator can use this confirmation.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        registered_count = len(db.get_registered_user_ids())
        db.reset_all_player_data()
        wipe_profile_tag_assets()
        for child in self.children:
            child.disabled = True
        await safe_edit_interaction_message(interaction, 
            content=f"⚠️ Global reset complete. Cleared **{registered_count}** registered user(s). Everyone must use `;start` again.",
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await safe_edit_interaction_message(interaction, content="Reset cancelled. No user data was deleted.", view=self)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(content="Reset timed out. No user data was deleted.", view=self)
            except Exception:
                pass
