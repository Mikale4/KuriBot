"""Guild hall, guild boss, guild shop and membership menus."""

from __future__ import annotations

import asyncio
import math
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

import discord

from kuribot.config import (
    GUILD_ACCEPTANCE_APPLY, GUILD_ACCEPTANCE_FREE_JOIN, GUILD_ACCEPTANCE_LABELS,
    GUILD_BOSS_MAX_PARTICIPATION_CHANCES, GUILD_CREATE_COST, GUILD_DAILY_CLAIM_COINS,
    GUILD_DONATION_OPTIONS, GUILD_JOIN_PAGE_SIZE, GUILD_LIST_PAGE_SIZE, GUILD_MAX_MEMBERS,
    GUILD_MAX_OFFICERS, GUILD_NAME_MAX_LENGTH, GUILD_NAME_MIN_LENGTH, GUILD_ROLE_LEADER,
    GUILD_ROLE_MEMBER, GUILD_ROLE_OFFICER, GUILD_SHOP_ITEMS, RANKED_STARTING_ELO, RNG,
)
from kuribot.timeutil import eastern_midnight_countdown_text
from kuribot.core import bot, is_admin_command_user
from kuribot.database import db
from kuribot.discord_helpers import resolve_guild_member_name_map
from kuribot.guilds import guild_shop_reset_text
from kuribot.arena import (
    arena_rank_label, get_user_arena_rank_position, maybe_run_guild_boss_attempt_reset,
)
from kuribot.render.guild import (
    guild_boss_panel_text, render_guild_boss_panel, render_guild_shop_page,
)
from kuribot.ui.common import render_discord_file_async, safe_edit_interaction_message


def guild_role_display(role: str) -> str:
    normalized = str(role or GUILD_ROLE_MEMBER).strip().lower()
    if normalized == GUILD_ROLE_LEADER:
        return "Leader"
    if normalized == GUILD_ROLE_OFFICER:
        return "Officer"
    return "Member"


def ordered_guild_rows_for_listing(rows: Sequence[sqlite3.Row], guild_order: Optional[Sequence[int]] = None) -> List[sqlite3.Row]:
    rows = list(rows)
    if not rows:
        return []
    by_id = {int(row["id"]): row for row in rows}
    ordered: List[sqlite3.Row] = []
    seen: set[int] = set()
    if guild_order:
        for raw_guild_id in guild_order:
            guild_id = int(raw_guild_id)
            row = by_id.get(guild_id)
            if row is not None and guild_id not in seen:
                ordered.append(row)
                seen.add(guild_id)
    missing = [row for row in rows if int(row["id"]) not in seen]
    RNG.shuffle(missing)
    ordered.extend(missing)
    if not guild_order:
        RNG.shuffle(ordered)
    return ordered


def guild_hub_page_rows(page: int = 0, guild_order: Optional[Sequence[int]] = None) -> Tuple[List[sqlite3.Row], int, int, int]:
    rows = ordered_guild_rows_for_listing(db.list_guilds(), guild_order)
    total_pages = max(1, math.ceil(max(1, len(rows)) / GUILD_LIST_PAGE_SIZE))
    page = max(0, min(int(page or 0), total_pages - 1))
    start = page * GUILD_LIST_PAGE_SIZE
    return rows[start:start + GUILD_LIST_PAGE_SIZE], page, total_pages, len(rows)


def guild_hub_summary_text(
    owner_id: int,
    page: int = 0,
    guild_order: Optional[Sequence[int]] = None,
    leader_name_map: Optional[Dict[int, str]] = None,
) -> str:
    membership = db.get_user_guild_membership(owner_id)
    selected_rows, page, total_pages, guild_count = guild_hub_page_rows(page, guild_order)
    leader_name_map = leader_name_map or {}
    lines = []
    if membership:
        lines.append(f"You are in **{membership['name']}** as **{guild_role_display(membership['role'])}**.")
    else:
        lines.append("You are not in a guild yet.")
    lines.append(f"Create a guild for **{GUILD_CREATE_COST:,} gold**. Max members per guild: **{GUILD_MAX_MEMBERS}**.")
    if guild_count > 0:
        lines.append("")
        lines.append(f"**All Guilds** — Page **{page + 1}/{total_pages}** • Randomized order")
        for row in selected_rows:
            leader_user_id = int(row["leader_user_id"] or 0)
            leader_name = leader_name_map.get(leader_user_id) or f"User {leader_user_id}"
            lines.append(
                f"• **{row['name']}** — Leader: **{leader_name}** • "
                f"{int(row['member_count'] or 0)}/{GUILD_MAX_MEMBERS}"
            )
    else:
        lines.append("")
        lines.append("No guilds have been created yet.")
    return "\n".join(lines)


async def build_guild_hub_summary_text(
    owner_id: int,
    page: int = 0,
    guild_order: Optional[Sequence[int]] = None,
    guild: Optional[discord.Guild] = None,
) -> str:
    selected_rows, normalized_page, _total_pages, _guild_count = guild_hub_page_rows(page, guild_order)
    leader_ids = [int(row["leader_user_id"] or 0) for row in selected_rows if int(row["leader_user_id"] or 0)]
    leader_name_map = await resolve_guild_member_name_map(leader_ids, guild) if leader_ids else {}
    return guild_hub_summary_text(owner_id, normalized_page, guild_order, leader_name_map)


def guild_hall_summary_text(owner_id: int) -> str:
    membership = db.get_user_guild_membership(owner_id)
    if not membership:
        return "You are not in a guild yet."
    guild_id = int(membership["guild_id"])
    guild_row = db.get_guild(guild_id)
    if guild_row is None:
        return "Your guild could not be found."
    members = db.guild_members(guild_id)
    officer_count = sum(1 for row in members if str(row['role']) == GUILD_ROLE_OFFICER)
    lines = [
        f"**{guild_row['name']}**",
        f"Leader: <@{int(guild_row['leader_user_id'])}>",
        f"Members: **{len(members)} / {GUILD_MAX_MEMBERS}**",
        f"Officers: **{officer_count} / {GUILD_MAX_OFFICERS}**",
        "",
        "**Guild Hall**",
        "Use the dropdown below for **Guild Boss**, **Guild Shop**, **Donation**, or **Claim**. Leaders/officers can also review **Guild Mail** and set **Acceptance**. **Guild War** is listed but disabled for now.",
        "",
        "**Roster**",
    ]
    for row in members:
        lines.append(f"• {guild_role_display(str(row['role']))}: <@{int(row['user_id'])}>")
    return "\n".join(lines)


def joinable_guilds_summary_text(owner_id: int, page: int) -> str:
    rows = db.list_joinable_guilds_for_user(owner_id)
    membership = db.get_user_guild_membership(owner_id)
    if membership:
        return f"You are already in **{membership['name']}**. Leave that guild first before joining another one."
    if not rows:
        return "There are no joinable guilds available right now."
    total_pages = max(1, math.ceil(len(rows) / GUILD_JOIN_PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    start = page * GUILD_JOIN_PAGE_SIZE
    selected = rows[start:start + GUILD_JOIN_PAGE_SIZE]
    lines = [f"**Joinable Guilds** — Page **{page + 1}/{total_pages}**", ""]
    for row in selected:
        mode = db.get_guild_acceptance_mode(int(row['id']))
        lines.append(f"• **{row['name']}** — {int(row['member_count'] or 0)}/{GUILD_MAX_MEMBERS} • {GUILD_ACCEPTANCE_LABELS.get(mode, 'Free Join')}")
    lines.append("")
    lines.append("Use a button below to join one of these guilds.")
    return "\n".join(lines)


class CreateGuildModal(discord.ui.Modal):
    guild_name = discord.ui.TextInput(
        label="Guild Name",
        required=True,
        min_length=GUILD_NAME_MIN_LENGTH,
        max_length=GUILD_NAME_MAX_LENGTH,
        placeholder="Enter your guild name",
    )

    def __init__(self, owner_id: int, user_name: str, source_message: Optional[discord.Message] = None):
        super().__init__(title="Create Guild")
        self.owner_id = owner_id
        self.user_name = user_name
        self.source_message = source_message

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This guild menu belongs to someone else.", ephemeral=True)
            return
        ok, message, _guild_id = db.create_guild(self.owner_id, str(self.guild_name))
        hub_view = GuildHubView(self.owner_id, self.user_name)
        if self.source_message is not None:
            try:
                await self.source_message.edit(content=await build_guild_hub_summary_text(self.owner_id, hub_view.page, hub_view.guild_order, interaction.guild), view=hub_view)
                hub_view.message = self.source_message
            except Exception:
                pass
        await interaction.response.send_message(message, ephemeral=True)


class RenameGuildModal(discord.ui.Modal):
    guild_name = discord.ui.TextInput(
        label="New Guild Name",
        required=True,
        min_length=GUILD_NAME_MIN_LENGTH,
        max_length=GUILD_NAME_MAX_LENGTH,
        placeholder="Enter the new guild name",
    )

    def __init__(self, owner_id: int, user_name: str, guild_id: int, source_message: Optional[discord.Message] = None):
        super().__init__(title="Rename Guild")
        self.owner_id = owner_id
        self.user_name = user_name
        self.guild_id = guild_id
        self.source_message = source_message

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This guild menu belongs to someone else.", ephemeral=True)
            return
        ok, message = db.rename_guild(self.owner_id, self.guild_id, str(self.guild_name))
        hall_view = GuildHallView(self.owner_id, self.user_name)
        message_to_edit = self.source_message or interaction.message
        if message_to_edit is not None:
            try:
                await message_to_edit.edit(content=guild_hall_summary_text(self.owner_id), view=hall_view)
            except Exception:
                pass
        await interaction.response.send_message(message, ephemeral=True)


class GuildLeaveConfirmView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.user_name = user_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This guild menu belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Leave", style=discord.ButtonStyle.danger)
    async def confirm_leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, message = db.leave_guild(self.owner_id)
        if ok:
            hub_view = GuildHubView(self.owner_id, self.user_name)
            await safe_edit_interaction_message(interaction, content=await build_guild_hub_summary_text(self.owner_id, hub_view.page, hub_view.guild_order, interaction.guild), view=hub_view)
            hub_view.message = interaction.message
        else:
            hall_view = GuildHallView(self.owner_id, self.user_name)
            await safe_edit_interaction_message(interaction, content=guild_hall_summary_text(self.owner_id), view=hall_view)
        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        hall_view = GuildHallView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=guild_hall_summary_text(self.owner_id), view=hall_view)


class GuildDisbandConfirmView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.user_name = user_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This guild menu belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Disband", style=discord.ButtonStyle.danger)
    async def confirm_disband_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, message = db.disband_guild(self.owner_id)
        if ok:
            hub_view = GuildHubView(self.owner_id, self.user_name)
            await safe_edit_interaction_message(interaction, content=await build_guild_hub_summary_text(self.owner_id, hub_view.page, hub_view.guild_order, interaction.guild), view=hub_view)
            hub_view.message = interaction.message
        else:
            hall_view = GuildHallView(self.owner_id, self.user_name)
            await safe_edit_interaction_message(interaction, content=guild_hall_summary_text(self.owner_id), view=hall_view)
        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        hall_view = GuildHallView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=guild_hall_summary_text(self.owner_id), view=hall_view)


class GuildKickSelect(discord.ui.Select):
    def __init__(self, owner_id: int, guild_id: int, user_name: str, member_name_map: Optional[Dict[int, str]] = None):
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.user_name = user_name
        member_name_map = member_name_map or {}
        actor_membership = db.get_user_guild_membership(owner_id)
        actor_role = str(actor_membership["role"] or GUILD_ROLE_MEMBER) if actor_membership else GUILD_ROLE_MEMBER
        members = db.guild_members(guild_id)
        candidates = []
        for row in members:
            target_user_id = int(row["user_id"])
            target_role = str(row["role"] or GUILD_ROLE_MEMBER)
            if target_user_id == int(owner_id):
                continue
            if target_role == GUILD_ROLE_LEADER:
                continue
            if actor_role == GUILD_ROLE_OFFICER and target_role != GUILD_ROLE_MEMBER:
                continue
            candidates.append(row)

        options: List[discord.SelectOption] = []
        for row in candidates[:25]:
            target_user_id = int(row["user_id"])
            target_role = guild_role_display(str(row["role"]))
            display_name = member_name_map.get(target_user_id) or f"User {target_user_id}"
            options.append(
                discord.SelectOption(
                    label=f"Kick {display_name}"[:100],
                    description=f"Remove {target_role} from the guild",
                    value=str(target_user_id),
                )
            )
        if not options:
            options = [discord.SelectOption(label="No players available", value="none")]
            super().__init__(placeholder="No players available", min_values=1, max_values=1, options=options)
            self.disabled = True
        else:
            super().__init__(placeholder="Choose a player to kick", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not self.values or self.values[0] == "none":
            await interaction.response.send_message("No players are available for that action.", ephemeral=True)
            return
        target_user_id = int(self.values[0])
        ok, message = db.kick_guild_member(self.owner_id, target_user_id)
        hall_view = GuildHallView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=guild_hall_summary_text(self.owner_id), view=hall_view)
        await interaction.followup.send(message, ephemeral=True)


class GuildKickManageView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, guild_id: int, member_name_map: Optional[Dict[int, str]] = None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.guild_id = guild_id
        self.add_item(GuildKickSelect(owner_id, guild_id, user_name, member_name_map))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This guild menu belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=3)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        hall_view = GuildHallView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=guild_hall_summary_text(self.owner_id), view=hall_view)


class GuildRoleSelect(discord.ui.Select):
    def __init__(self, owner_id: int, guild_id: int, mode: str, user_name: str, member_name_map: Optional[Dict[int, str]] = None):
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.mode = mode
        self.user_name = user_name
        member_name_map = member_name_map or {}
        members = db.guild_members(guild_id)
        options: List[discord.SelectOption] = []
        if mode == "promote":
            candidates = [row for row in members if str(row['role']) == GUILD_ROLE_MEMBER]
            for row in candidates[:25]:
                target_user_id = int(row['user_id'])
                display_name = member_name_map.get(target_user_id) or f"User {target_user_id}"
                options.append(discord.SelectOption(label=f"Promote {display_name}"[:100], description="Promote to Officer", value=str(target_user_id)))
            placeholder = "Choose a member to promote"
        else:
            candidates = [row for row in members if str(row['role']) == GUILD_ROLE_OFFICER]
            for row in candidates[:25]:
                target_user_id = int(row['user_id'])
                display_name = member_name_map.get(target_user_id) or f"User {target_user_id}"
                options.append(discord.SelectOption(label=f"Demote {display_name}"[:100], description="Demote to Member", value=str(target_user_id)))
            placeholder = "Choose an officer to demote"
        if not options:
            options = [discord.SelectOption(label="No players available", value="none")]
            super().__init__(placeholder="No players available", min_values=1, max_values=1, options=options)
            self.disabled = True
        else:
            super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if not self.values or self.values[0] == "none":
            await interaction.response.send_message("No players are available for that action.", ephemeral=True)
            return
        target_user_id = int(self.values[0])
        new_role = GUILD_ROLE_OFFICER if self.mode == "promote" else GUILD_ROLE_MEMBER
        ok, message = db.set_guild_member_role(self.guild_id, target_user_id, new_role)
        hall_view = GuildHallView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=guild_hall_summary_text(self.owner_id), view=hall_view)
        await interaction.followup.send(message, ephemeral=True)


class GuildRoleManageView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, guild_id: int, mode: str, member_name_map: Optional[Dict[int, str]] = None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.guild_id = guild_id
        self.mode = mode
        self.add_item(GuildRoleSelect(owner_id, guild_id, mode, user_name, member_name_map))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This guild menu belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=3)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        hall_view = GuildHallView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=guild_hall_summary_text(self.owner_id), view=hall_view)


class GuildJoinView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, page: int = 0):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.page = page
        self.message: Optional[discord.Message] = None
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        rows = db.list_joinable_guilds_for_user(self.owner_id)
        total_pages = max(1, math.ceil(max(1, len(rows)) / GUILD_JOIN_PAGE_SIZE))
        self.page = max(0, min(self.page, total_pages - 1))
        start = self.page * GUILD_JOIN_PAGE_SIZE
        selected = rows[start:start + GUILD_JOIN_PAGE_SIZE]
        for row in selected:
            self.add_item(JoinGuildButton(int(row['id']), str(row['name'])))
        prev_btn = discord.ui.Button(label="Prev", style=discord.ButtonStyle.secondary, disabled=self.page <= 0, row=4)
        next_btn = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary, disabled=(self.page + 1) >= total_pages, row=4)
        back_btn = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary, row=4)

        async def prev_callback(interaction: discord.Interaction):
            self.page -= 1
            self.rebuild()
            await safe_edit_interaction_message(interaction, content=joinable_guilds_summary_text(self.owner_id, self.page), view=self)

        async def next_callback(interaction: discord.Interaction):
            self.page += 1
            self.rebuild()
            await safe_edit_interaction_message(interaction, content=joinable_guilds_summary_text(self.owner_id, self.page), view=self)

        async def back_callback(interaction: discord.Interaction):
            hub_view = GuildHubView(self.owner_id, self.user_name)
            await safe_edit_interaction_message(interaction, content=await build_guild_hub_summary_text(self.owner_id, hub_view.page, hub_view.guild_order, interaction.guild), view=hub_view)
            hub_view.message = interaction.message

        prev_btn.callback = prev_callback
        next_btn.callback = next_callback
        back_btn.callback = back_callback
        self.add_item(prev_btn)
        self.add_item(next_btn)
        self.add_item(back_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This guild menu belongs to someone else.", ephemeral=True)
            return False
        return True


class JoinGuildButton(discord.ui.Button):
    def __init__(self, guild_id: int, guild_name: str):
        super().__init__(label=f"Join {guild_name}"[:80], style=discord.ButtonStyle.success, row=0)
        self.guild_id = guild_id
        self.guild_name = guild_name

    async def callback(self, interaction: discord.Interaction):
        parent = self.view
        if parent is None:
            return
        ok, message = db.join_guild(int(interaction.user.id), self.guild_id)
        membership = db.get_user_guild_membership(int(interaction.user.id))
        if ok and membership:
            hub_view = GuildHubView(parent.owner_id, parent.user_name)
            await safe_edit_interaction_message(interaction, content=await build_guild_hub_summary_text(parent.owner_id, hub_view.page, hub_view.guild_order, interaction.guild), view=hub_view)
            hub_view.message = interaction.message
            await interaction.followup.send(message, ephemeral=True)
        else:
            parent.rebuild()
            await safe_edit_interaction_message(interaction, content=joinable_guilds_summary_text(parent.owner_id, parent.page), view=parent)
            await interaction.followup.send(message, ephemeral=True)


class GuildShopItemSelect(discord.ui.Select):
    def __init__(self, parent_view: "GuildShopView"):
        self.parent_view = parent_view
        options: List[discord.SelectOption] = []
        for item in GUILD_SHOP_ITEMS:
            sold = db.is_guild_shop_item_sold_out(parent_view.owner_id, str(item['key']))
            options.append(discord.SelectOption(
                label=str(item['name'])[:100],
                description=("SOLD OUT • " if sold else "") + f"{int(item['cost'])} guild coins",
                value=str(item['key']),
            ))
        super().__init__(placeholder="Choose a guild shop item to buy", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        item_key = self.values[0] if self.values else ""
        ok, message = db.buy_guild_shop_item(self.parent_view.owner_id, item_key)
        refreshed = GuildShopView(self.parent_view.owner_id, self.parent_view.user_name)
        await safe_edit_interaction_message(interaction, content=refreshed.summary_text(extra=message), attachments=[await asyncio.to_thread(refreshed.make_file)], embed=None, view=refreshed)


class GuildShopView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.user_name = str(user_name)
        self.add_item(GuildShopItemSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This guild shop belongs to someone else.", ephemeral=True)
            return False
        return True

    def make_file(self) -> discord.File:
        return discord.File(render_guild_shop_page(self.user_name, self.owner_id), filename="guild_shop.png")

    def summary_text(self, extra: str = "") -> str:
        user_row = db.get_user(self.owner_id)
        lines = [
            "**Guild Shop**",
            f"Guild Coins: **{int(user_row['guild_coins'] or 0):,}** • {guild_shop_reset_text()}",
            "Bought items are sold out until the next 12 AM / 12 PM ET shop refresh.",
        ]
        if extra:
            lines.insert(0, extra)
        return "\n".join(lines)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        hall_view = GuildHallView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=guild_hall_summary_text(self.owner_id), attachments=[], embed=None, view=hall_view)


class GuildDonationSelect(discord.ui.Select):
    def __init__(self, parent_view: "GuildDonationView"):
        self.parent_view = parent_view
        donated = db.has_used_guild_hall_daily_action(parent_view.owner_id, "donate")
        options = []
        for option in GUILD_DONATION_OPTIONS:
            options.append(discord.SelectOption(
                label=str(option["label"]),
                description=("USED TODAY • " if donated else "") + f"Get {int(option['guild_coins'])} guild coins",
                value=str(option["key"]),
            ))
        super().__init__(placeholder="Choose a donation amount", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        option_key = self.values[0] if self.values else ""
        ok, message = db.donate_gold_for_guild_coins(self.parent_view.owner_id, option_key)
        refreshed = GuildDonationView(self.parent_view.owner_id, self.parent_view.user_name)
        await safe_edit_interaction_message(interaction, content=refreshed.summary_text(extra=message), attachments=[], embed=None, view=refreshed)


class GuildDonationView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.user_name = str(user_name)
        self.add_item(GuildDonationSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This donation menu belongs to someone else.", ephemeral=True)
            return False
        return True

    def summary_text(self, extra: str = "") -> str:
        user_row = db.get_user(self.owner_id)
        donated = db.has_used_guild_hall_daily_action(self.owner_id, "donate")
        lines = [
            "**Guild Donation**",
            f"Gold: **{int(user_row['gold'] or 0):,}** • Guild Coins: **{int(user_row['guild_coins'] or 0):,}**",
            "Donate gold once per day for guild coins. Resets at **12:00 AM ET**.",
        ]
        if donated:
            lines.append(f"Next donation available in **{eastern_midnight_countdown_text()}**.")
        if extra:
            lines.insert(0, extra)
        return "\n".join(lines)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        hall_view = GuildHallView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=guild_hall_summary_text(self.owner_id), attachments=[], embed=None, view=hall_view)


def guild_acceptance_summary_text(owner_id: int) -> str:
    membership = db.get_user_guild_membership(owner_id)
    if not membership:
        return "You are not in a guild."
    mode = db.get_guild_acceptance_mode(int(membership["guild_id"]))
    return (
        f"**Guild Acceptance**\n"
        f"Current setting: **{GUILD_ACCEPTANCE_LABELS.get(mode, 'Free Join')}**\n\n"
        "**Free Join** lets players join instantly.\n"
        "**Apply** requires a leader or officer to approve applicants from Guild Mail."
    )


class GuildAcceptanceSelect(discord.ui.Select):
    def __init__(self, owner_id: int):
        membership = db.get_user_guild_membership(owner_id)
        mode = db.get_guild_acceptance_mode(int(membership["guild_id"])) if membership else GUILD_ACCEPTANCE_FREE_JOIN
        options = [
            discord.SelectOption(label="Free Join", value=GUILD_ACCEPTANCE_FREE_JOIN, description="Players can join instantly", default=mode == GUILD_ACCEPTANCE_FREE_JOIN),
            discord.SelectOption(label="Apply", value=GUILD_ACCEPTANCE_APPLY, description="Players must submit an application", default=mode == GUILD_ACCEPTANCE_APPLY),
        ]
        super().__init__(placeholder="Choose guild acceptance mode", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        parent = self.view
        if parent is None:
            return
        owner_id = int(getattr(parent, "owner_id", interaction.user.id))
        membership = db.get_user_guild_membership(owner_id)
        if not membership:
            await interaction.response.send_message("You are not in a guild.", ephemeral=True)
            return
        ok, message = db.set_guild_acceptance_mode(owner_id, int(membership["guild_id"]), self.values[0] if self.values else GUILD_ACCEPTANCE_FREE_JOIN)
        view = GuildAcceptanceView(owner_id, str(getattr(parent, "user_name", interaction.user.display_name)))
        await safe_edit_interaction_message(interaction, content=guild_acceptance_summary_text(owner_id) + "\n\n" + message, attachments=[], embed=None, view=view)


class GuildAcceptanceView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.user_name = str(user_name or "Player")
        self.add_item(GuildAcceptanceSelect(self.owner_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This guild acceptance menu belongs to someone else.", ephemeral=True)
            return False
        membership = db.get_user_guild_membership(self.owner_id)
        if not membership or str(membership["role"] or GUILD_ROLE_MEMBER) not in {GUILD_ROLE_LEADER, GUILD_ROLE_OFFICER}:
            await interaction.response.send_message("Only the guild leader and officers can change guild acceptance settings.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_edit_interaction_message(interaction, content=guild_hall_summary_text(self.owner_id), attachments=[], embed=None, view=GuildHallView(self.owner_id, self.user_name))


async def notify_guild_application_result(user_id: int, guild_name: str, accepted: bool):
    outcome = "accepted" if accepted else "denied"
    body = (
        f"Your application to **{guild_name}** was **{outcome}**.\n\n"
        + (f"Welcome to **{guild_name}**! Open Guild Hall to see your new guild." if accepted else "You can apply to another guild from the guild menu.")
    )
    db.create_mail(int(user_id), body, sender_name="KuriBot")
    user = bot.get_user(int(user_id))
    if user is None:
        try:
            user = await bot.fetch_user(int(user_id))
        except Exception:
            user = None
    if user is not None:
        try:
            dm = user.dm_channel or await user.create_dm()
            await dm.send(body)
        except Exception:
            pass


def guild_application_empty_embed(guild_name: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"{guild_name} Guild Mail",
        description="There are no pending guild applications.",
        color=discord.Color.gold(),
    )
    embed.set_footer(text="Guild Mail • Applications")
    return embed


class GuildApplicationReviewView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, guild_id: int, page: int = 0):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.user_name = str(user_name or "Player")
        self.guild_id = int(guild_id)
        self.page = int(page or 0)
        self.rows: List[sqlite3.Row] = []
        self.refresh_rows()
        self.sync_buttons()

    def refresh_rows(self):
        self.rows = db.pending_guild_applications(self.guild_id)
        total_pages = max(1, len(self.rows))
        self.page = max(0, min(self.page, total_pages - 1))

    def current_application(self) -> Optional[sqlite3.Row]:
        if not self.rows:
            return None
        self.page = max(0, min(self.page, len(self.rows) - 1))
        return self.rows[self.page]

    def sync_buttons(self):
        has_rows = bool(self.rows)
        self.prev_btn.disabled = not has_rows or self.page <= 0
        self.next_btn.disabled = not has_rows or self.page >= len(self.rows) - 1
        self.accept_btn.disabled = not has_rows
        self.deny_btn.disabled = not has_rows

    async def make_embed(self, guild: Optional[discord.Guild] = None) -> discord.Embed:
        guild_row = db.get_guild(self.guild_id)
        guild_name = str(guild_row["name"] if guild_row is not None else "Guild")
        application = self.current_application()
        if application is None:
            return guild_application_empty_embed(guild_name)
        applicant_id = int(application["user_id"])
        user_row = db.get_user(applicant_id)
        monster_count = len(db.user_inventory(applicant_id))
        arena_rank = get_user_arena_rank_position(applicant_id)
        arena_text = arena_rank_label(arena_rank)
        display_name = f"User {applicant_id}"
        avatar_url = ""
        member = guild.get_member(applicant_id) if guild is not None else None
        if member is not None:
            display_name = member.display_name
            avatar_url = str(member.display_avatar.url)
        else:
            cached = bot.get_user(applicant_id)
            if cached is None:
                try:
                    cached = await bot.fetch_user(applicant_id)
                except Exception:
                    cached = None
            if cached is not None:
                display_name = cached.display_name
                avatar_url = str(cached.display_avatar.url)
        embed = discord.Embed(
            title=f"{guild_name} Guild Application",
            description=f"Applicant: **{display_name}** (<@{applicant_id}>)",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Monsters Owned", value=f"{monster_count:,}", inline=True)
        embed.add_field(name="Gold", value=f"{int(user_row['gold'] or 0):,}", inline=True)
        embed.add_field(name="Account Level", value=f"{int(user_row['level'] or 1):,}", inline=True)
        embed.add_field(name="ELO Rating", value=f"{int(user_row['ranked_elo'] or RANKED_STARTING_ELO):,}", inline=True)
        embed.add_field(name="Arena Rank", value=arena_text, inline=True)
        embed.add_field(name="Applied", value=str(application["applied_at"] or "Unknown"), inline=False)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        embed.set_footer(text=f"Application {self.page + 1}/{len(self.rows)}")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This Guild Mail belongs to someone else.", ephemeral=True)
            return False
        membership = db.get_user_guild_membership(self.owner_id)
        if not membership or int(membership["guild_id"]) != self.guild_id or str(membership["role"] or GUILD_ROLE_MEMBER) not in {GUILD_ROLE_LEADER, GUILD_ROLE_OFFICER}:
            await interaction.response.send_message("Only this guild's leader and officers can review Guild Mail.", ephemeral=True)
            return False
        return True

    async def refresh_message(self, interaction: discord.Interaction):
        self.refresh_rows()
        self.sync_buttons()
        await safe_edit_interaction_message(interaction, content="**Guild Mail**", attachments=[], embed=await self.make_embed(interaction.guild), view=self)

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await self.refresh_message(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(max(0, len(self.rows) - 1), self.page + 1)
        await self.refresh_message(interaction)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, row=1)
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        application = self.current_application()
        if application is None:
            await interaction.response.send_message("There are no pending applications.", ephemeral=True)
            return
        ok, message, applicant_id, guild_name = db.resolve_guild_application(self.owner_id, int(application["id"]), True)
        if applicant_id is not None and guild_name is not None:
            await notify_guild_application_result(applicant_id, guild_name, ok)
        await self.refresh_message(interaction)
        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, row=1)
    async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        application = self.current_application()
        if application is None:
            await interaction.response.send_message("There are no pending applications.", ephemeral=True)
            return
        ok, message, applicant_id, guild_name = db.resolve_guild_application(self.owner_id, int(application["id"]), False)
        if applicant_id is not None and guild_name is not None:
            await notify_guild_application_result(applicant_id, guild_name, False)
        await self.refresh_message(interaction)
        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=2)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await safe_edit_interaction_message(interaction, content=guild_hall_summary_text(self.owner_id), attachments=[], embed=None, view=GuildHallView(self.owner_id, self.user_name))


class GuildHallActionSelect(discord.ui.Select):
    def __init__(self, owner_id: int):
        membership = db.get_user_guild_membership(int(owner_id))
        role = str(membership["role"] if membership else GUILD_ROLE_MEMBER).strip().lower()
        options = [
            discord.SelectOption(label="Guild Boss", description="Challenge the active shared guild boss", value="boss"),
            discord.SelectOption(label="Guild Shop", description="Spend guild coins on guild items", value="shop"),
            discord.SelectOption(label="Donation", description="Donate gold once daily for guild coins", value="donation"),
            discord.SelectOption(label="Claim", description=f"Claim {GUILD_DAILY_CLAIM_COINS} free guild coins once daily", value="claim"),
        ]
        if role in {GUILD_ROLE_LEADER, GUILD_ROLE_OFFICER}:
            options.append(discord.SelectOption(label="Guild Mail", description="Review pending guild applications", value="guild_mail"))
        options.append(discord.SelectOption(label="Guild War", description="Coming soon", value="war"))
        super().__init__(placeholder="Choose a Guild Hall activity", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        parent = self.view
        if parent is None:
            return
        owner_id = int(getattr(parent, "owner_id", interaction.user.id))
        user_name = str(getattr(parent, "user_name", interaction.user.display_name))
        choice = self.values[0] if self.values else ""
        if choice == "war":
            await interaction.response.send_message("That Guild Hall feature is disabled for now.", ephemeral=True)
            return
        membership = db.get_user_guild_membership(owner_id)
        if not membership:
            await interaction.response.send_message("You are not in a guild.", ephemeral=True)
            return
        if choice == "guild_mail":
            if str(membership["role"] or GUILD_ROLE_MEMBER) not in {GUILD_ROLE_LEADER, GUILD_ROLE_OFFICER}:
                await interaction.response.send_message("Only the guild leader and officers can review Guild Mail.", ephemeral=True)
                return
            view = GuildApplicationReviewView(owner_id, user_name, int(membership["guild_id"]))
            await safe_edit_interaction_message(interaction, content="**Guild Mail**", attachments=[], embed=await view.make_embed(interaction.guild), view=view)
            return
        if choice == "shop":
            view = GuildShopView(owner_id, user_name)
            await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments=[await asyncio.to_thread(view.make_file)], embed=None, view=view)
            return
        if choice == "donation":
            view = GuildDonationView(owner_id, user_name)
            await safe_edit_interaction_message(interaction, content=view.summary_text(), attachments=[], embed=None, view=view)
            return
        if choice == "claim":
            ok, message = db.claim_daily_guild_coins(owner_id)
            await safe_edit_interaction_message(interaction, content=guild_hall_summary_text(owner_id) + "\n\n" + message, attachments=[], embed=None, view=GuildHallView(owner_id, user_name))
            return
        guild_id = int(membership["guild_id"])
        maybe_run_guild_boss_attempt_reset()
        boss = db.get_or_create_guild_boss(guild_id)
        if boss is None:
            await interaction.response.send_message("No monsters are available to create a Guild Boss.", ephemeral=True)
            return
        participants = len(db.conn.execute("SELECT user_id FROM guild_boss_participants WHERE guild_boss_id = ?", (int(boss["id"]),)).fetchall())
        chances_used = db.get_guild_boss_participation_chances(int(boss["id"]), owner_id)
        attempts_left = max(0, GUILD_BOSS_MAX_PARTICIPATION_CHANCES - int(chances_used))
        boss_file = await render_discord_file_async(render_guild_boss_panel, "guild_boss.png", str(membership["name"]), boss, participants, attempts_left=attempts_left)
        view = GuildBossView(owner_id, user_name)
        await safe_edit_interaction_message(interaction, content=guild_boss_panel_text(owner_id), attachments=[boss_file], embed=None, view=view)


class GuildBossLeaderboardView(discord.ui.View):
    def __init__(self, owner_id: int, guild_name: str, boss_name: str, rows: Sequence[sqlite3.Row], page_size: int = 10):
        super().__init__(timeout=300)
        self.owner_id = int(owner_id)
        self.guild_name = str(guild_name or "Guild")
        self.boss_name = str(boss_name or "Guild Boss")
        self.rows = list(rows)
        self.page_size = max(1, int(page_size))
        self.page = 0
        self.total_pages = max(1, math.ceil(max(1, len(self.rows)) / self.page_size))
        self.sync_buttons()

    def sync_buttons(self):
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= self.total_pages - 1

    def make_embed(self) -> discord.Embed:
        start = self.page * self.page_size
        page_rows = self.rows[start:start + self.page_size]
        if page_rows:
            lines = []
            for idx, row in enumerate(page_rows, start=start + 1):
                lines.append(f"**{idx}.** <@{int(row['user_id'])}> — **{int(row['total_damage']):,}** damage")
            description = "\n".join(lines)
        else:
            description = "No one has damaged this Guild Boss yet."
        embed = discord.Embed(
            title=f"{self.guild_name} Guild Boss Leaderboard",
            description=description,
            color=discord.Color.gold(),
        )
        embed.add_field(name="Boss", value=self.boss_name, inline=False)
        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages} • Top damage done to this boss")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This leaderboard belongs to someone else.", ephemeral=True)
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


class GuildBossView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This guild boss panel belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Challenge Guild Boss", style=discord.ButtonStyle.danger, row=0)
    async def challenge_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.modes.guild_boss import start_guild_boss_battle
        await start_guild_boss_battle(interaction, self.owner_id, self.user_name)

    @discord.ui.button(label="Refresh Boss", style=discord.ButtonStyle.secondary, row=0)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        membership = db.get_user_guild_membership(self.owner_id)
        if not membership:
            await interaction.response.send_message("You are not in a guild.", ephemeral=True)
            return
        if not is_admin_command_user(interaction.user):
            # Rerolling deletes the boss and everyone's damage/attempts - admin only, like ;resetboss.
            await interaction.response.send_message("Only bot admins can reroll the Guild Boss.", ephemeral=True)
            return
        maybe_run_guild_boss_attempt_reset()
        boss = db.refresh_guild_boss(int(membership["guild_id"]))
        if boss is None:
            await interaction.response.send_message("No monsters are available to create a Guild Boss.", ephemeral=True)
            return
        participants = len(db.conn.execute("SELECT user_id FROM guild_boss_participants WHERE guild_boss_id = ?", (int(boss["id"]),)).fetchall())
        chances_used = db.get_guild_boss_participation_chances(int(boss["id"]), self.owner_id)
        attempts_left = max(0, GUILD_BOSS_MAX_PARTICIPATION_CHANCES - int(chances_used))
        boss_file = await render_discord_file_async(render_guild_boss_panel, "guild_boss.png", str(membership["name"]), boss, participants, attempts_left=attempts_left)
        await safe_edit_interaction_message(interaction, content=guild_boss_panel_text(self.owner_id), attachments=[boss_file], embed=None, view=GuildBossView(self.owner_id, self.user_name))

    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.primary, row=0)
    async def leaderboard_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        membership = db.get_user_guild_membership(self.owner_id)
        if not membership:
            await interaction.response.send_message("You are not in a guild.", ephemeral=True)
            return
        boss = db.get_or_create_guild_boss(int(membership["guild_id"]))
        if boss is None:
            await interaction.response.send_message("No monsters are available to create a Guild Boss.", ephemeral=True)
            return
        rows = db.get_guild_boss_leaderboard(int(boss["id"]))
        view = GuildBossLeaderboardView(self.owner_id, str(membership["name"]), str(boss["monster_name"]), rows)
        await interaction.response.send_message(embed=view.make_embed(), view=view, ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        hall_view = GuildHallView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=guild_hall_summary_text(self.owner_id), attachments=[], embed=None, view=hall_view)


class GuildHallView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.add_item(GuildHallActionSelect(self.owner_id))
        self.apply_role_button_visibility()

    def apply_role_button_visibility(self):
        """Hide guild-management buttons the current member's role should not see."""
        membership = db.get_user_guild_membership(self.owner_id)
        role = str(membership["role"] if membership else GUILD_ROLE_MEMBER).strip().lower()

        leader_only_labels = {"Promote Member", "Demote Officer", "Rename Guild", "Disband Guild"}
        officer_or_leader_labels = {"Kick Member", "Acceptance"}
        labels_to_hide: set[str] = set()

        if role != GUILD_ROLE_LEADER:
            labels_to_hide.update(leader_only_labels)
        if role not in {GUILD_ROLE_LEADER, GUILD_ROLE_OFFICER}:
            labels_to_hide.update(officer_or_leader_labels)

        for child in list(self.children):
            if isinstance(child, discord.ui.Button) and str(getattr(child, "label", "")) in labels_to_hide:
                self.remove_item(child)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This guild hall belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Acceptance", style=discord.ButtonStyle.primary, row=1)
    async def acceptance_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        membership = db.get_user_guild_membership(self.owner_id)
        if not membership:
            await interaction.response.send_message("You are not in a guild.", ephemeral=True)
            return
        if str(membership["role"] or GUILD_ROLE_MEMBER) not in {GUILD_ROLE_LEADER, GUILD_ROLE_OFFICER}:
            await interaction.response.send_message("Only the guild leader and officers can change guild acceptance settings.", ephemeral=True)
            return
        await safe_edit_interaction_message(interaction, content=guild_acceptance_summary_text(self.owner_id), attachments=[], embed=None, view=GuildAcceptanceView(self.owner_id, self.user_name))

    @discord.ui.button(label="Promote Member", style=discord.ButtonStyle.primary, row=1)
    async def promote_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        membership = db.get_user_guild_membership(self.owner_id)
        if not membership:
            await interaction.response.send_message("You are not in a guild.", ephemeral=True)
            return
        if str(membership['role']) != GUILD_ROLE_LEADER:
            await interaction.response.send_message("Only the guild leader can promote officers.", ephemeral=True)
            return
        guild_id = int(membership['guild_id'])
        candidates = [int(row['user_id']) for row in db.guild_members(guild_id) if str(row['role']) == GUILD_ROLE_MEMBER][:25]
        member_name_map = await resolve_guild_member_name_map(candidates, interaction.guild)
        view = GuildRoleManageView(self.owner_id, self.user_name, guild_id, "promote", member_name_map)
        await safe_edit_interaction_message(interaction, content="Choose a member to promote to officer.", view=view)

    @discord.ui.button(label="Demote Officer", style=discord.ButtonStyle.secondary, row=1)
    async def demote_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        membership = db.get_user_guild_membership(self.owner_id)
        if not membership:
            await interaction.response.send_message("You are not in a guild.", ephemeral=True)
            return
        if str(membership['role']) != GUILD_ROLE_LEADER:
            await interaction.response.send_message("Only the guild leader can demote officers.", ephemeral=True)
            return
        guild_id = int(membership['guild_id'])
        candidates = [int(row['user_id']) for row in db.guild_members(guild_id) if str(row['role']) == GUILD_ROLE_OFFICER][:25]
        member_name_map = await resolve_guild_member_name_map(candidates, interaction.guild)
        view = GuildRoleManageView(self.owner_id, self.user_name, guild_id, "demote", member_name_map)
        await safe_edit_interaction_message(interaction, content="Choose an officer to demote back to member.", view=view)

    @discord.ui.button(label="Kick Member", style=discord.ButtonStyle.danger, row=2)
    async def kick_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        membership = db.get_user_guild_membership(self.owner_id)
        if not membership:
            await interaction.response.send_message("You are not in a guild.", ephemeral=True)
            return
        if str(membership['role']) not in {GUILD_ROLE_LEADER, GUILD_ROLE_OFFICER}:
            await interaction.response.send_message("Only the guild leader and officers can kick members.", ephemeral=True)
            return
        guild_id = int(membership['guild_id'])
        candidates = []
        actor_role = str(membership['role'] or GUILD_ROLE_MEMBER)
        for row in db.guild_members(guild_id):
            target_user_id = int(row['user_id'])
            target_role = str(row['role'] or GUILD_ROLE_MEMBER)
            if target_user_id == self.owner_id or target_role == GUILD_ROLE_LEADER:
                continue
            if actor_role == GUILD_ROLE_OFFICER and target_role != GUILD_ROLE_MEMBER:
                continue
            candidates.append(target_user_id)
        member_name_map = await resolve_guild_member_name_map(candidates, interaction.guild)
        view = GuildKickManageView(self.owner_id, self.user_name, guild_id, member_name_map)
        await safe_edit_interaction_message(interaction, content="Choose a guild member to kick.", view=view)

    @discord.ui.button(label="Rename Guild", style=discord.ButtonStyle.secondary, row=2)
    async def rename_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        membership = db.get_user_guild_membership(self.owner_id)
        if not membership:
            await interaction.response.send_message("You are not in a guild.", ephemeral=True)
            return
        if str(membership['role']) != GUILD_ROLE_LEADER:
            await interaction.response.send_message("Only the guild leader can rename the guild.", ephemeral=True)
            return
        await interaction.response.send_modal(RenameGuildModal(self.owner_id, self.user_name, int(membership['guild_id']), interaction.message))

    @discord.ui.button(label="Leave Guild", style=discord.ButtonStyle.danger, row=3)
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        membership = db.get_user_guild_membership(self.owner_id)
        if not membership:
            await interaction.response.send_message("You are not in a guild.", ephemeral=True)
            return
        guild_id = int(membership["guild_id"])
        guild_row = db.get_guild(guild_id)
        guild_name = str(guild_row["name"] if guild_row is not None else "your guild")
        role = str(membership["role"] or GUILD_ROLE_MEMBER)
        member_count = db.guild_member_count(guild_id)
        if role == GUILD_ROLE_LEADER and member_count > 1:
            await interaction.response.send_message(
                "Guild leaders cannot leave while other members are still in the guild. Kick members first, then leave to disband it.",
                ephemeral=True,
            )
            return
        if role == GUILD_ROLE_LEADER:
            content = f"Are you sure you want to leave and disband **{guild_name}**?"
        else:
            content = f"Are you sure you want to leave **{guild_name}**?"
        await safe_edit_interaction_message(interaction, content=content, view=GuildLeaveConfirmView(self.owner_id, self.user_name))

    @discord.ui.button(label="Disband Guild", style=discord.ButtonStyle.danger, row=3)
    async def disband_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        membership = db.get_user_guild_membership(self.owner_id)
        if not membership:
            await interaction.response.send_message("You are not in a guild.", ephemeral=True)
            return
        if str(membership["role"] or GUILD_ROLE_MEMBER) != GUILD_ROLE_LEADER:
            await interaction.response.send_message("Only the guild leader can disband the guild.", ephemeral=True)
            return
        guild_name = str(membership["name"] or "your guild")
        member_count = db.guild_member_count(int(membership["guild_id"]))
        await safe_edit_interaction_message(
            interaction,
            content=f"Are you sure you want to permanently disband **{guild_name}** and remove all **{member_count}** member(s)?",
            view=GuildDisbandConfirmView(self.owner_id, self.user_name),
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=3)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        hub_view = GuildHubView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=await build_guild_hub_summary_text(self.owner_id, hub_view.page, hub_view.guild_order, interaction.guild), view=hub_view)
        hub_view.message = interaction.message


class GuildHubView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, page: int = 0, guild_order: Optional[Sequence[int]] = None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.page = int(page or 0)
        rows = db.list_guilds()
        if guild_order is None:
            self.guild_order = [int(row["id"]) for row in rows]
            RNG.shuffle(self.guild_order)
        else:
            self.guild_order = [int(guild_id) for guild_id in guild_order]
        self.message: Optional[discord.Message] = None
        self.rebuild_pagination_buttons()

    def rebuild_pagination_buttons(self):
        _rows, self.page, total_pages, guild_count = guild_hub_page_rows(self.page, self.guild_order)
        prev_btn = discord.ui.Button(label="Prev Guilds", style=discord.ButtonStyle.secondary, disabled=self.page <= 0 or guild_count <= GUILD_LIST_PAGE_SIZE, row=1)
        next_btn = discord.ui.Button(label="Next Guilds", style=discord.ButtonStyle.secondary, disabled=(self.page + 1) >= total_pages or guild_count <= GUILD_LIST_PAGE_SIZE, row=1)
        shuffle_btn = discord.ui.Button(label="Refresh", style=discord.ButtonStyle.secondary, disabled=guild_count <= 1, row=1)

        async def prev_callback(interaction: discord.Interaction):
            self.page -= 1
            self.rebuild_pagination_buttons()
            content = await build_guild_hub_summary_text(self.owner_id, self.page, self.guild_order, interaction.guild)
            await safe_edit_interaction_message(interaction, content=content, view=self)

        async def next_callback(interaction: discord.Interaction):
            self.page += 1
            self.rebuild_pagination_buttons()
            content = await build_guild_hub_summary_text(self.owner_id, self.page, self.guild_order, interaction.guild)
            await safe_edit_interaction_message(interaction, content=content, view=self)

        async def shuffle_callback(interaction: discord.Interaction):
            rows = db.list_guilds()
            self.guild_order = [int(row["id"]) for row in rows]
            RNG.shuffle(self.guild_order)
            self.page = 0
            self.rebuild_pagination_buttons()
            content = await build_guild_hub_summary_text(self.owner_id, self.page, self.guild_order, interaction.guild)
            await safe_edit_interaction_message(interaction, content=content, view=self)

        prev_btn.callback = prev_callback
        next_btn.callback = next_callback
        shuffle_btn.callback = shuffle_callback

        # Keep the four main hub buttons, then replace any old pagination controls.
        for child in list(self.children):
            if isinstance(child, discord.ui.Button) and str(getattr(child, "label", "") or "") in {"Prev Guilds", "Next Guilds", "Shuffle Guilds", "Refresh"}:
                self.remove_item(child)
        self.add_item(prev_btn)
        self.add_item(next_btn)
        self.add_item(shuffle_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This guild hub belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Create Guild", style=discord.ButtonStyle.success, row=0)
    async def create_guild_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CreateGuildModal(self.owner_id, self.user_name, interaction.message))

    @discord.ui.button(label="Join Guild", style=discord.ButtonStyle.primary, row=0)
    async def join_guild_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = GuildJoinView(self.owner_id, self.user_name, page=0)
        view.message = interaction.message
        await safe_edit_interaction_message(interaction, content=joinable_guilds_summary_text(self.owner_id, 0), view=view)

    @discord.ui.button(label="Guild Hall", style=discord.ButtonStyle.primary, row=0)
    async def guild_hall_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not db.get_user_guild_membership(self.owner_id):
            await interaction.response.send_message("You are not in a guild yet.", ephemeral=True)
            return
        view = GuildHallView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(interaction, content=guild_hall_summary_text(self.owner_id), view=view)
