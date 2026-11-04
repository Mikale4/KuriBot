"""Collection commands: binder, inventory, team, deck, equip, Extra Deck."""

from __future__ import annotations

from typing import List

from discord.ext import commands

from kuribot.config import EXTRA_DECK_MIN_SIZE, EXTRA_DECK_SIZE, PREFIX
from kuribot.core import bot
from kuribot.database import db
from kuribot.discord_helpers import fit_discord_message_content
from kuribot.monsters import is_extra_deck_monster_row
from kuribot.ui.collection import build_inventory_embed, InventoryView, SupportCardBrowserView
from kuribot.ui.equip import EquipCommandHubView
from kuribot.ui.extra_deck import (
    extra_deck_summary_lines, find_owned_extra_deck_monster_instance, send_extra_deck_help,
)
from kuribot.ui.binder import send_binder_section_message


@bot.command(name="inventory", aliases=["inv"])
async def inventory_cmd(ctx: commands.Context):
    user = db.get_user(ctx.author.id)
    monster_count = len(db.user_inventory(ctx.author.id))
    support_card_count = len(db.user_support_cards(ctx.author.id))
    equip_spell_count = len(db.user_equip_spells(ctx.author.id))
    pack_count = sum(int(row["quantity"] or 0) for row in db.user_packs(ctx.author.id))
    embed = build_inventory_embed(
        ctx.author.display_name,
        user,
        monster_count=monster_count,
        support_card_count=support_card_count,
        equip_spell_count=equip_spell_count,
        pack_count=pack_count,
    )
    await ctx.send(embed=embed, view=InventoryView(ctx.author.id, ctx.author.display_name))


@bot.command(name="equip")
async def equip_cmd(ctx: commands.Context):
    view = EquipCommandHubView(ctx.author.id, ctx.author.display_name)
    await ctx.send(view.summary_text(), view=view)


@bot.command(name="binder")
async def binder_cmd(ctx: commands.Context):
    await send_binder_section_message(ctx)


@bot.command(name="team", aliases=["teams"])
async def team_shortcut_cmd(ctx: commands.Context):
    await send_binder_section_message(ctx, "teams")


@bot.command(name="monster", aliases=["monsters"])
async def monster_shortcut_cmd(ctx: commands.Context):
    await send_binder_section_message(ctx, "monsters")


@bot.command(name="cards", aliases=["card"])
async def support_cards_cmd(ctx: commands.Context):
    message = await ctx.send("Opening support cards...")
    view = SupportCardBrowserView(ctx.author.id, ctx.author.display_name)
    await message.edit(content=view.summary_text(), embed=view.selected_embed(), view=view)


@bot.command(name="deck", aliases=["decks"])
async def deck_shortcut_cmd(ctx: commands.Context):
    await send_binder_section_message(ctx, "decks")


@bot.command(name="ed", aliases=["extra", "extradecks"])
async def extra_deck_shortcut_cmd(ctx: commands.Context):
    await send_binder_section_message(ctx, "extra_decks")


@bot.command(name="extradeck")
async def extra_deck_cmd(ctx: commands.Context, action: str = "list", *, payload: str = ""):
    action = str(action or "list").strip().lower()
    user_id = int(ctx.author.id)
    db.ensure_user(user_id)
    if action in {"help", "?"}:
        await send_extra_deck_help(ctx)
        return
    if action in {"list", "show", "view"}:
        decks = db.list_extra_decks(user_id)
        if not decks:
            owned_count = sum(1 for row in db.user_inventory(user_id) if is_extra_deck_monster_row(row))
            await ctx.send(f"You do not have any Extra Decks yet. You own **{owned_count}** Extra Deck Monster(s). Use `{PREFIX}extradeck create Name | Monster 1, Monster 2`.")
            return
        chunks: List[str] = ["**Your Extra Decks**"]
        for deck in decks:
            lines = extra_deck_summary_lines(user_id, deck)
            chunks.append(f"\n**#{deck['id']} — {deck['name']}**\n" + ("\n".join(lines) if lines else "Empty"))
        await ctx.send(fit_discord_message_content("\n".join(chunks)))
        return
    if action in {"create", "new"}:
        if "|" not in payload:
            await ctx.send(f"Use `{PREFIX}extradeck create Deck Name | Monster 1, Monster 2`.")
            return
        name, raw_monsters = payload.split("|", 1)
        picks = []
        missing = []
        for part in [piece.strip() for piece in raw_monsters.split(",") if piece.strip()]:
            instance_id = find_owned_extra_deck_monster_instance(user_id, part)
            if instance_id:
                picks.append(instance_id)
            else:
                missing.append(part)
        if missing:
            await ctx.send("I could not find these owned Extra Deck Monsters: " + ", ".join(f"**{item}**" for item in missing[:8]))
            return
        if not db.create_extra_deck(user_id, name.strip(), picks):
            await ctx.send(f"Could not create that Extra Deck. It must have {EXTRA_DECK_MIN_SIZE}-{EXTRA_DECK_SIZE} owned Extra Deck Monsters and a name of at least 2 characters.")
            return
        await ctx.send(f"Created Extra Deck **{name.strip()}** with **{len(picks)}** monster(s).")
        return
    if action in {"active", "setactive", "default"}:
        await ctx.send(f"Extra Decks do not have an active setting. Equip one to a team with `{PREFIX}extradeck equip <team id> <extra deck id|none>`.")
        return
    if action in {"equip", "attach"}:
        parts = payload.split()
        if len(parts) < 2:
            await ctx.send(f"Use `{PREFIX}extradeck equip <team id> <extra deck id|none>`.")
            return
        try:
            team_id = int(parts[0])
        except Exception:
            await ctx.send("Team id must be a number.")
            return
        raw_deck = parts[1].strip().lower()
        deck_id = None if raw_deck in {"none", "clear", "0"} else int(raw_deck)
        if not db.update_team_extra_deck(user_id, team_id, deck_id):
            await ctx.send("Could not equip that Extra Deck to that team.")
            return
        label = "No Extra Deck" if deck_id is None else f"Extra Deck #{deck_id}"
        await ctx.send(f"Updated Team #{team_id}: **{label}** equipped.")
        return
    if action in {"delete", "remove"}:
        try:
            deck_id = int(payload.strip())
        except Exception:
            await ctx.send(f"Use `{PREFIX}extradeck delete <extra deck id>`.")
            return
        if not db.delete_extra_deck(user_id, deck_id):
            await ctx.send("That Extra Deck was not found.")
            return
        await ctx.send(f"Deleted Extra Deck #{deck_id}.")
        return
    await send_extra_deck_help(ctx)
