"""Battle commands: pve, pvp, ranked, arena, lobby, tournament, campaign."""

from __future__ import annotations

import asyncio
from typing import List

from discord.ext import commands

from kuribot.config import RANKED_STARTING_ELO
from kuribot.core import bot, is_admin_command_user, is_bot_creator
from kuribot.database import db
from kuribot.discord_helpers import resolve_global_discord_user_name
from kuribot.campaign import maybe_build_campaign_hard_mode_unlock_embed
from kuribot.render.hubs import render_battle_hub_page
from kuribot.ui.common import render_discord_file_async
from kuribot.ui.campaign import CampaignHubView
from kuribot.ui.dueling_grounds import battle_hub_summary_text, BattleHubView, make_pvp_section_view
from kuribot.ui.tournament import TournamentHubView
from kuribot.modes.normal import start_dueling_grounds_normal_battle
from kuribot.modes.ranked import start_dueling_grounds_ranked_battle
from kuribot.commands.adapters import _CommandInteractionAdapter


@bot.command(name="list")
async def waiting_list_cmd(ctx: commands.Context):
    if not is_admin_command_user(ctx.author):
        await ctx.send("You are not allowed to use this command.")
        return

    rows = db.waiting_list_entries()
    if not rows:
        await ctx.send("The waiting list is empty.")
        return

    page_size = 10
    for page_start in range(0, len(rows), page_size):
        page_rows = rows[page_start:page_start + page_size]
        lines: List[str] = []
        for row in page_rows:
            user_id = int(row["user_id"])
            display_name = await resolve_global_discord_user_name(user_id)
            ranked_elo = int(row["ranked_elo"] or RANKED_STARTING_ELO)
            lines.append(f"**{display_name}** — **{ranked_elo:,} ELO**")
        await ctx.send("\n".join(lines))


@bot.command(name="pve")
async def pve_cmd(ctx: commands.Context):
    view = BattleHubView(ctx.author.id, ctx.author.display_name)
    message = await ctx.send("Opening Adventure hub...")
    battle_file = await render_discord_file_async(render_battle_hub_page, "battle_hub.png", ctx.author.display_name, ctx.author.id)
    await message.edit(
        content=battle_hub_summary_text(ctx.author.id),
        attachments=[battle_file],
        view=view,
    )
    if hasattr(view, "message"):
        view.message = message


@bot.command(name="pvp")
async def pvp_cmd(ctx: commands.Context):
    content, hub_file, view = make_pvp_section_view(ctx.author.id, ctx.author.display_name)
    message = await ctx.send("Opening Dueling Grounds...")
    try:
        await message.edit(
            content=content,
            attachments=[hub_file] if hub_file is not None else [],
            view=view,
        )
        if hasattr(view, "message"):
            view.message = message
    except Exception:
        await message.edit(content="The Dueling Grounds hub hit an error while opening. Please try again.", attachments=[], view=None)
        raise


@bot.command(name="ranked")
async def ranked_shortcut_cmd(ctx: commands.Context):
    placeholder = await ctx.send("Searching the Ranked ladder for a challenger...")
    interaction = _CommandInteractionAdapter(ctx, placeholder)
    await start_dueling_grounds_ranked_battle(interaction, ctx.author.id, ctx.author.display_name)


@bot.command(name="arena")
async def arena_shortcut_cmd(ctx: commands.Context):
    content, hub_file, view = make_pvp_section_view(ctx.author.id, ctx.author.display_name, "arena")
    send_kwargs = {'content': content, 'view': view}
    if hub_file is not None:
        send_kwargs['file'] = hub_file
    message = await ctx.send(**send_kwargs)
    if hasattr(view, "message"):
        view.message = message


@bot.command(name="lobby")
async def lobby_shortcut_cmd(ctx: commands.Context):
    content, hub_file, view = make_pvp_section_view(ctx.author.id, ctx.author.display_name, "lobby")
    send_kwargs = {'content': content, 'view': view}
    if hub_file is not None:
        send_kwargs['file'] = hub_file
    message = await ctx.send(**send_kwargs)
    if hasattr(view, "message"):
        view.message = message


@bot.command(name="normal")
async def normal_shortcut_cmd(ctx: commands.Context):
    placeholder = await ctx.send("Searching the Dueling Grounds for a challenger...")
    interaction = _CommandInteractionAdapter(ctx, placeholder)
    await start_dueling_grounds_normal_battle(interaction, ctx.author.id, ctx.author.display_name)


@bot.command(name="tournament")
async def tournament_shortcut_cmd(ctx: commands.Context):
    if not is_bot_creator(ctx.author.id):
        await ctx.send("Only the bot creator can use Tournament.")
        return
    view = TournamentHubView(ctx.author.id, ctx.author.display_name)
    message = await ctx.send(content=view.summary_text(), file=await asyncio.to_thread(view.make_file), view=view)
    if hasattr(view, "message"):
        view.message = message


@bot.command(name="campaign")
async def campaign_shortcut_cmd(ctx: commands.Context):
    unlock_embed = maybe_build_campaign_hard_mode_unlock_embed(ctx.author.id)
    view = CampaignHubView(ctx.author.id, ctx.author.display_name)
    message = await ctx.send(content="Choose a campaign area.", file=await asyncio.to_thread(view.make_file), view=view)
    if hasattr(view, "message"):
        view.message = message
    if unlock_embed is not None:
        await ctx.send(embed=unlock_embed)
