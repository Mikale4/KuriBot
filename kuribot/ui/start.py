"""Start, tutorial prompt and idea submission menus."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import discord
from discord.ext import commands

from kuribot.config import IDEA_SUBMISSION_CHANNEL_ID
from kuribot.core import bot
from kuribot.database import db
from kuribot.ui.common import safe_edit_interaction_message, safe_send_interaction_message
from kuribot.tutorial import build_tutorial_welcome_embed


def create_start_embed(user_name: str, *, already_registered: bool = False) -> discord.Embed:
    title = "Welcome back to KuriBot!" if already_registered else "Welcome to KuriBot!"
    description = (
        "I’m your DMO RPG companion. Summon monsters, build teams, collect support cards, and battle your way through the game."
    )
    embed = discord.Embed(title=title, description=description, color=discord.Color.blurple())
    embed.add_field(
        name="What you can do",
        value=(
            "• `;daily` to claim Random Packs\n"
            "• `;packs` to open packs and summon monsters\n"
            "• `;binder` to manage your collection\n"
            "• `;shop` to browse offers\n"
            "• `;pve` for Adventure or `;pvp` for Dueling Grounds"
        ),
        inline=False,
    )
    embed.add_field(
        name="Get started",
        value=(
            "Use `;help` anytime to see the main commands and jump back in. "
            "The tutorial is optional, but finishing it gives free rewards."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Good luck, {user_name}!")
    return embed


async def send_unregistered_prompt(ctx: commands.Context):
    has_started = db.user_exists(ctx.author.id)
    description = (
        f"Hey {ctx.author.mention}! You’re not registered yet. Use `;start` to begin your adventure and unlock the bot’s commands."
        if not has_started else
        f"Hey {ctx.author.mention}! You haven’t finished registration yet. Use `;start` again, press **Continue**, and choose your avatar to unlock the bot’s commands."
    )
    embed = discord.Embed(
        title="Welcome to KuriBot!",
        description=description,
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Start here",
        value=(
            "Type `;start` and I’ll get you set up right away."
            if not has_started else
            "Type `;start` again to reopen setup, then press **Continue** and choose your avatar."
        ),
        inline=False,
    )
    fallback_text = (
        f"Hey {ctx.author.mention}! You’re not registered yet. Use `;start` to begin your adventure and unlock KuriBot’s commands."
        if not has_started else
        f"Hey {ctx.author.mention}! You haven’t finished registration yet. Use `;start` again, press **Continue**, and choose your avatar to unlock KuriBot’s commands."
    )
    dm_text = (
        "Welcome to KuriBot! You’re not registered yet. Use `;start` in a server where KuriBot can reply to begin your adventure."
        if not has_started else
        "Welcome back to KuriBot! You haven’t finished registration yet. Use `;start` again, press **Continue**, and choose your avatar to finish setup."
    )
    try:
        await ctx.send(embed=embed)
        return True
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass

    try:
        await ctx.send(fallback_text)
        return True
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass

    try:
        await ctx.author.send(dm_text)
        return True
    except discord.Forbidden:
        return False
    except discord.HTTPException:
        return False


class IdeaSubmissionModal(discord.ui.Modal, title="Submit Idea"):
    idea_kind = discord.ui.TextInput(
        label="Type",
        style=discord.TextStyle.short,
        required=True,
        max_length=32,
        placeholder="Special Support Card or Equip Spell",
    )
    idea_name = discord.ui.TextInput(
        label="Name",
        style=discord.TextStyle.short,
        required=True,
        max_length=100,
        placeholder="Use a real Yu-Gi-Oh! non-monster card name",
    )
    description = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
        placeholder="Describe what the card or equip should do.",
    )

    def __init__(self, owner_id: int, owner_name: str):
        super().__init__()
        self.owner_id = owner_id
        self.owner_name = owner_name

    async def on_submit(self, interaction: discord.Interaction):
        review_channel = bot.get_channel(IDEA_SUBMISSION_CHANNEL_ID)
        if review_channel is None:
            try:
                fetched_channel = await bot.fetch_channel(IDEA_SUBMISSION_CHANNEL_ID)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                fetched_channel = None
            review_channel = fetched_channel
        if review_channel is None or not hasattr(review_channel, 'send'):
            await interaction.response.send_message(
                "Your idea could not be submitted right now. Please try again later.",
                ephemeral=True,
            )
            return

        kind_text = str(self.idea_kind).strip()
        name_text = str(self.idea_name).strip()
        description_text = str(self.description).strip()
        embed = discord.Embed(
            title="New Card / Equip Idea",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Submitted By", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
        embed.add_field(name="Type", value=kind_text or "Unknown", inline=False)
        embed.add_field(name="Name", value=name_text or "Unnamed", inline=False)
        embed.add_field(name="Description", value=description_text or "No description provided.", inline=False)
        if interaction.guild is not None:
            embed.add_field(name="Server", value=str(interaction.guild.name), inline=True)
        if interaction.channel is not None:
            embed.add_field(name="Channel", value=getattr(interaction.channel, 'mention', f"`{getattr(interaction.channel, 'id', 'Unknown')}`"), inline=True)
        embed.set_footer(text=f"Submitted from ;idea by {self.owner_name}")

        try:
            await review_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "Your idea could not be submitted right now. Please try again later.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Your idea has been submitted and is now up for review.",
            ephemeral=True,
        )


class IdeaCommandView(discord.ui.View):
    def __init__(self, owner_id: int, owner_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.owner_name = owner_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This idea prompt belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Build", style=discord.ButtonStyle.success)
    async def build_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(IdeaSubmissionModal(self.owner_id, self.owner_name))


class StartTutorialView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This tutorial button belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Tutorial", style=discord.ButtonStyle.success)
    async def tutorial_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.ui.tutorial import TutorialView
        view = TutorialView(self.owner_id, self.user_name)
        await safe_edit_interaction_message(
            interaction,
            content=None,
            embed=build_tutorial_welcome_embed(self.user_name),
            attachments=[],
            view=view,
        )


class StartContinueView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This start menu belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.success)
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.ui.profile import AvatarSelectionView
        picker = AvatarSelectionView(self.owner_id, self.user_name, mode="start")
        picker.message = await safe_send_interaction_message(
            interaction,
            content=f"{picker.intro_text()}\n\nFriendly tip: after you finish picking your avatar, you can play right away. The optional tutorial gives free rewards if you finish it.",
            file=await asyncio.to_thread(picker.make_file),
            view=picker,
        )


