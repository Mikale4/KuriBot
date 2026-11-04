"""General commands: start, help, idea, tutorial, glossary."""

from __future__ import annotations

import discord
from discord.ext import commands

from kuribot.config import PREFIX
from kuribot.core import bot
from kuribot.database import db
from kuribot.render.themes import get_ui_theme_palette
from kuribot.discord_helpers import fit_discord_message_content
from kuribot.ui.glossary import GlossaryView
from kuribot.tutorial import build_tutorial_welcome_embed, tutorial_completion_status
from kuribot.ui.start import (
    create_start_embed, IdeaCommandView, send_unregistered_prompt, StartContinueView,
)
from kuribot.ui.tutorial import TutorialView


@bot.command(name="start")
async def start_cmd(ctx: commands.Context):
    if db.is_registered_user(ctx.author.id):
        tutorial_complete, missing = tutorial_completion_status(ctx.author.id)
        tutorial_note = (
            "Your tutorial is complete."
            if tutorial_complete else
            "The tutorial is optional and still has free rewards available. Use `;tutorial` whenever you want to continue."
        )
        already_registered_text = (
            f"{ctx.author.mention} you're already registered. "
            f"{tutorial_note} "
            "Use `;help` to see the main commands or `;profile` to view your account."
        )
        try:
            await ctx.send(already_registered_text)
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass
        return

    has_started = db.user_exists(ctx.author.id)
    db.ensure_user(ctx.author.id)
    embed = create_start_embed(ctx.author.display_name, already_registered=has_started)
    fallback_text = (
        f"Welcome back to KuriBot, {ctx.author.display_name}! Press **Continue** below to finish setup and choose your KuriBot avatar."
        if has_started else
        f"Welcome to KuriBot, {ctx.author.display_name}! Press **Continue** below to choose your KuriBot avatar and finish setup."
    )
    dm_text = (
        f"Welcome back to KuriBot, {ctx.author.display_name}! Press **Continue** below to finish setup and choose your KuriBot avatar."
        if has_started else
        f"Welcome to KuriBot, {ctx.author.display_name}! Press **Continue** below to choose your KuriBot avatar and finish setup."
    )
    view = StartContinueView(ctx.author.id, ctx.author.display_name)
    try:
        await ctx.send(embed=embed, view=view)
        return
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass

    view = StartContinueView(ctx.author.id, ctx.author.display_name)
    try:
        await ctx.send(fallback_text, view=view)
        return
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass

    view = StartContinueView(ctx.author.id, ctx.author.display_name)
    try:
        await ctx.author.send(dm_text, view=view)
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        pass


@bot.command(name="help")
async def help_cmd(ctx: commands.Context):
    prefix = PREFIX
    palette = get_ui_theme_palette(db.get_ui_theme(ctx.author.id))
    embed = discord.Embed(
        title="✨ KuriBot Command Center",
        description=f"Use `{prefix}<command>` to run a command.",
        color=discord.Color.from_rgb(*palette["outer_outline"]),
    )
    embed.add_field(
        name="🚀 Getting Started",
        value=(
            f"`{prefix}start` — Register and begin your adventure\n"
            f"`{prefix}tutorial` — Learn the core game systems\n"
            f"`{prefix}daily` — Claim your daily rewards\n"
            f"`{prefix}quest` — View and progress your active quest\n"
            f"`{prefix}hardclaim` — Claim your Campaign Hard Mode final reward"
        ),
        inline=False,
    )
    embed.add_field(
        name="👤 Player",
        value=(
            f"`{prefix}profile` — Profile, inbox alerts, avatar, and showcase\n"
            f"`{prefix}rank` — Level and XP progress\n"
            f"`{prefix}leaderboard` / `{prefix}lb` — Ranked leaderboard\n"
            f"`{prefix}settings` — Battle speed and notifications (themes: Profile → Customize)\n"
            f"`{prefix}inbox` — Open your mailbox"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎒 Collection",
        value=(
            f"`{prefix}inventory` / `{prefix}inv` — Inventory hub\n"
            f"`{prefix}binder` — Monsters, cards, decks, and teams\n"
            f"`{prefix}monster` / `{prefix}monsters` — Monster binder shortcut\n"
            f"`{prefix}cards` / `{prefix}card` — Support card binder shortcut\n"
            f"`{prefix}deck` / `{prefix}decks` — Support deck shortcut\n"
            f"`{prefix}ed` / `{prefix}extradecks` — Extra Deck shortcut\n"
            f"`{prefix}team` / `{prefix}teams` — Team builder shortcut\n"
            f"`{prefix}equip` — Manage equip spells"
        ),
        inline=False,
    )
    embed.add_field(
        name="📦 Packs & Economy",
        value=(
            f"`{prefix}packs` / `{prefix}pack` — View, open, gift, or sell packs\n"
            f"`{prefix}shop` — Pack, card, equip, and partner shops\n"
            f"`{prefix}market` — Player market"
        ),
        inline=False,
    )
    embed.add_field(
        name="⚔️ Battles",
        value=(
            f"`{prefix}pve` — Adventure hub (Campaign)\n"
            f"`{prefix}campaign` — Campaign shortcut\n"
            f"`{prefix}pvp` — Dueling Grounds hub\n"
            f"`{prefix}ranked` — Ranked duel shortcut\n"
            f"`{prefix}arena` — Arena shortcut\n"
            f"`{prefix}lobby` — Lobby shortcut\n"
            f"`{prefix}normal` — Start a Normal Dueling Grounds battle"
        ),
        inline=False,
    )
    embed.add_field(
        name="🏰 Guilds & Community",
        value=(
            f"`{prefix}guild` — Guild hub, boss, shop, and applications\n"
            f"`{prefix}idea` — Submit a support card or equip spell idea\n"
        ),
        inline=False,
    )
    embed.add_field(
        name="📚 Reference",
        value=f"`{prefix}glossary` — Mechanics, statuses, stats, monster generation, and equip spells",
        inline=False,
    )
    embed.set_footer(text=f"{ctx.author.display_name} • KuriBot commands")
    await ctx.send(embed=embed)


@bot.command(name="idea")
async def idea_cmd(ctx: commands.Context):
    embed = discord.Embed(
        title="Suggest a New Equip Spell or Special Support Card",
        description=(
            "All ideas are welcomed.\n\n"
            "If you submit one, the card or equip name must be a real Yu-Gi-Oh! non-monster card name.\n\n"
            "Press **Build** below to open the submission form."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="What to include",
        value=(
            "• Whether it is a **Special Support Card** or **Equip Spell**\n"
            "• The **name**\n"
            "• The **description/effect idea**"
        ),
        inline=False,
    )
    await ctx.send(embed=embed, view=IdeaCommandView(ctx.author.id, ctx.author.display_name))


@bot.command(name="tutorial")
async def tutorial_cmd(ctx: commands.Context):
    if not db.is_registered_user(ctx.author.id):
        await send_unregistered_prompt(ctx)
        return
    db.ensure_user(ctx.author.id)
    view = TutorialView(ctx.author.id, ctx.author.display_name)
    complete, _ = tutorial_completion_status(ctx.author.id)
    if complete:
        embed = build_tutorial_welcome_embed(ctx.author.display_name)
        embed.description = (
            f"Great work, **{ctx.author.display_name}**! You have completed every tutorial section.\n\n"
            "You can still reopen any tutorial lesson from this menu whenever you want a refresher."
        )
    else:
        embed = build_tutorial_welcome_embed(ctx.author.display_name)
    await ctx.send(embed=embed, view=view)


@bot.command(name="glossary")
async def glossary_cmd(ctx: commands.Context):
    view = GlossaryView(ctx.author.id)
    message = await ctx.send(fit_discord_message_content(view.content()), view=view)
    view.message = message
