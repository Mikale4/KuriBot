"""Admin commands."""

from __future__ import annotations

from collections import Counter
from typing import List, Optional

import discord
from discord.ext import commands

from kuribot.config import RANKED_STARTING_ELO
from kuribot.models import EquipSpellDef, SupportCardDef
from kuribot.core import (
    bot, debug_channel_lock_enabled, debug_channel_lock_status_text, debug_hard_mode_unlock_active,
    grant_debug_hard_mode_unlock, is_admin_command_user, is_bot_creator,
)
from kuribot.cards.equip_spells import random_equip_spell
from kuribot.database import db
from kuribot.cards.support_cards import random_support_card, support_card_family_key
from kuribot.campaign import (
    campaign_area_completed, campaign_completion_claimed, campaign_hard_completion_choice_claimed,
    campaign_hard_completion_choice_key, campaign_hard_mode_campaign_completed,
    reset_all_campaign_progress,
)
from kuribot.gacha import grant_support_card_reward
from kuribot.ui.admin import (
    BossMonsterAdminView, DebugChannelLockView, PingMainView, ResetConfirmView,
)
from kuribot.ui.campaign import CampaignHardCompletionRewardView


@bot.command(name="adminhelp")
async def adminhelp_cmd(ctx: commands.Context):
    if not is_admin_command_user(ctx.author):
        return
    text = (
        "**KuriBot Admin Commands**\n"
        "`;ping` - Open the inbox admin panel for messages and gifts\n"
        "`;givepack` / `;gp` - Give Random Packs to yourself or another user\n"
        "`;givegold` / `;gg` - Give gold to yourself or another user\n"
        "`;getguildcoin` / `;ggc` - Give guild coins to yourself or another user\n"
        "`;givecard` / `;gc` - Grant random support cards to yourself or another user\n"
        "`;getspell` / `;gs` - Grant random equip spells to yourself or another user\n"
        "`;bossmonster` / `;bm` - Send an Extra Deck Monster to a registered user's inbox\n"
        "`;resetboss [guild name]` - Reset a guild's Guild Boss so members can defeat another boss\n"
        "`;attempts <guild name>` - Reset Guild Boss attempts for every member in a guild\n"
        "`;list` - View the 1100+ ELO player waiting list\n"
        "\n**Bot Creator Only**\n"
        "`;reset` - Global wipe of all registered user data\n"
        "`;debug` - Open the bot creator debug channel lock panel\n"
        "`;hardmode` - Temporarily unlock Campaign Hard Mode while debug is ON\n"
        "`;resetcamp` - Reset Campaign normal and hard progress for all users\n"
        "`;resetelo` - Reset Ranked ELO, GXE, and ranked milestone claims for all users\n"
        "`;resetshowcase` - Reset showcase data for all users\n"
    )
    await ctx.send(text)


@bot.command(name="reset")
async def reset_cmd(ctx: commands.Context):
    if not is_bot_creator(ctx.author.id):
        await ctx.send("Only the bot creator can use this command.")
        return
    view = ResetConfirmView(ctx.author.id)
    warning = (
        "⚠️ **Global Reset Warning**\n"
        "This will erase every registered user and all of their saved data from the bot.\n"
        "Everyone will need to use `;start` again after the reset."
    )
    view.message = await ctx.send(warning, view=view)


@bot.command(name="resetcamp")
async def resetcamp_cmd(ctx: commands.Context):
    if not is_bot_creator(ctx.author.id):
        await ctx.send("Only the bot creator can use this command.")
        return
    results = reset_all_campaign_progress()
    await ctx.send(
        "✅ Campaign progress reset for all users. "
        f"Area progress rows cleared: **{results['progress_rows']}** • "
        f"Duelist progress rows cleared: **{results['duelist_rows']}** • "
        f"Reward claim rows cleared: **{results['reward_rows']}** • "
        f"Completion rows cleared: **{results['completion_rows']}**"
    )


@bot.command(name="resetelo")
async def resetelo_cmd(ctx: commands.Context):
    if not is_bot_creator(ctx.author.id):
        await ctx.send("Only the bot creator can use this command.")
        return
    reset_users = db.reset_all_ranked_elo()
    await ctx.send(
        "✅ Ranked ladder reset complete. "
        f"Users reset: **{reset_users}** • ELO: **{RANKED_STARTING_ELO}** • GXE: **50.0%** • Ranked milestone claims cleared."
    )


@bot.command(name="resetshowcase")
async def resetshowcase_cmd(ctx: commands.Context):
    if not is_bot_creator(ctx.author.id):
        await ctx.send("Only the bot creator can use this command.")
        return
    results = db.reset_showcase_data()
    await ctx.send(
        "✅ Showcase data reset. "
        f"Profiles cleared: **{results['reset_profiles']}** • "
        f"Pending requests cleared: **{results['cleared_requests']}** • "
        f"Pending files deleted: **{results['pending_files_deleted']}** • "
        f"Approved files deleted: **{results['approved_files_deleted']}**"
    )


@bot.command(name="resetboss")
async def resetboss_cmd(ctx: commands.Context, *, guild_name: str = ""):
    if not is_admin_command_user(ctx.author):
        await ctx.send("You are not allowed to use this command.")
        return

    guild_name = str(guild_name or "").strip()
    if guild_name:
        guild_row = db.get_guild_by_name(guild_name)
        if guild_row is None:
            await ctx.send(f"Could not find a guild named **{guild_name}**.")
            return
        target_guild_id = int(guild_row["id"])
        target_guild_name = str(guild_row["name"] or guild_name)
    else:
        membership = db.get_user_guild_membership(ctx.author.id)
        if not membership:
            await ctx.send("Usage: `;resetboss [guild name]` or join a guild before using `;resetboss` without a guild name.")
            return
        target_guild_id = int(membership["guild_id"])
        target_guild_name = str(membership["name"] or "Guild")

    boss = db.refresh_guild_boss(target_guild_id)
    if boss is None:
        await ctx.send("No monsters are available to create a Guild Boss.")
        return

    await ctx.send(
        f"✅ Guild Boss reset for **{target_guild_name}**. "
        f"New boss: **{boss['monster_name']}** with **{int(boss['current_hp']):,}/{int(boss['max_hp']):,} HP**. "
        "Guild members can challenge and defeat another boss now."
    )


@bot.command(name="attempts")
async def attempts_cmd(ctx: commands.Context, *, guild_name: str = ""):
    if not is_admin_command_user(ctx.author):
        await ctx.send("You are not allowed to use this command.")
        return

    guild_name = str(guild_name or "").strip()
    if not guild_name:
        await ctx.send("Usage: `;attempts <guild name>`")
        return

    guild_row = db.get_guild_by_name(guild_name)
    if guild_row is None:
        await ctx.send(f"Could not find a guild named **{guild_name}**.")
        return

    target_guild_id = int(guild_row["id"])
    target_guild_name = str(guild_row["name"] or guild_name)
    results = db.reset_guild_boss_attempts_for_guild(target_guild_id)
    active_boss = db.active_guild_boss(target_guild_id)
    boss_text = f" Active boss: **{active_boss['monster_name']}**." if active_boss is not None else " No active Guild Boss is currently spawned."
    await ctx.send(
        f"✅ Guild Boss attempts reset for **{target_guild_name}**. "
        f"Members covered: **{results['member_count']}** • "
        f"Participant rows found: **{results['participant_rows']}** • "
        f"Rows changed: **{results['reset_rows']}**."
        f"{boss_text}"
    )


@bot.command(name="debug")
async def debug_cmd(ctx: commands.Context):
    if not is_bot_creator(ctx.author.id):
        await ctx.send("Only the bot creator can use this command.")
        return
    view = DebugChannelLockView(ctx.author.id)
    content = (
        "**Debug Controls**\n"
        f"{debug_channel_lock_status_text()}\n"
        "Use **ON** to force the bot to only send messages in the allowed channel."
    )
    view.message = await ctx.send(content, view=view)


@bot.command(name="hardmode")
async def hardmode_cmd(ctx: commands.Context):
    if not is_bot_creator(ctx.author.id):
        await ctx.send("Only the bot creator can use this command.")
        return
    if not debug_channel_lock_enabled():
        await ctx.send("`;hardmode` can only be used while `;debug` mode is ON.")
        return
    already_unlocked = campaign_area_completed(ctx.author.id, "battle_city")
    already_debug_unlocked = debug_hard_mode_unlock_active(ctx.author.id)
    if not already_unlocked and not already_debug_unlocked:
        grant_debug_hard_mode_unlock(ctx.author.id)
    if already_unlocked:
        await ctx.send("Campaign Hard Mode is already unlocked naturally on this account.")
        return
    if already_debug_unlocked:
        await ctx.send("Campaign Hard Mode is already temporarily unlocked for you while debug mode stays ON.")
        return
    await ctx.send("✅ Campaign Hard Mode temporarily unlocked for you. This bypass turns off when debug mode is turned OFF or when the bot restarts.")


@bot.command(name="hardclaim")
async def hardclaim_cmd(ctx: commands.Context):
    owner_id = ctx.author.id
    user_name = ctx.author.display_name

    if not campaign_hard_mode_campaign_completed(owner_id):
        await ctx.send("You have not completed Campaign Hard Mode yet.")
        return

    if not campaign_completion_claimed(owner_id, hard_mode=True):
        await ctx.send("Your Campaign Hard Mode completion reward is not available yet.")
        return

    if campaign_hard_completion_choice_claimed(owner_id):
        claimed_key = campaign_hard_completion_choice_key(owner_id)
        claimed_text = claimed_key.replace('_', ' ').title() if claimed_key else 'Unknown Reward'
        await ctx.send(f"Your Campaign Hard Mode final reward was already claimed: **{claimed_text}**.")
        return

    reward_view = CampaignHardCompletionRewardView(owner_id, user_name)
    await ctx.send(embed=reward_view.build_embed("Choose one reward. This choice is permanent."), view=reward_view)


@bot.command(name="ping")
async def ping_cmd(ctx: commands.Context):
    if not is_admin_command_user(ctx.author):
        await ctx.send("You are not allowed to use this command.")
        return
    await ctx.send("Admin inbox tools:", view=PingMainView(ctx.author.id, ctx.author.display_name))


@bot.command(name="bossmonster", aliases=["bm"])
async def bossmonster_cmd(ctx: commands.Context):
    if not is_admin_command_user(ctx.author):
        await ctx.send("You are not allowed to use this command.")
        return
    view = BossMonsterAdminView(ctx.author.id, ctx.author.display_name)
    await ctx.send(embed=view.build_embed(), view=view)


@bot.command(name="givepack", aliases=["gp"])
async def givepack_cmd(ctx: commands.Context, amount: int, member: Optional[discord.Member] = None):
    if not is_admin_command_user(ctx.author):
        await ctx.send("You are not allowed to use this command.")
        return
    if amount <= 0:
        await ctx.send("Amount must be greater than 0.")
        return

    target = member or ctx.author
    db.add_pack(target.id, "random_themed", amount)
    current_packs = db.pack_quantity(target.id, "random_themed")
    if target.id == ctx.author.id:
        await ctx.send(f"📦 Gave **{amount} Random Pack(s)** to yourself. You now have **{current_packs}** Random Pack(s).")
    else:
        await ctx.send(f"📦 Gave **{amount} Random Pack(s)** to {target.mention}. They now have **{current_packs}** Random Pack(s).")


@givepack_cmd.error
async def givepack_cmd_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Usage: `;givepack <amount> [@user]` or `;gp <amount> [@user]`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Usage: `;givepack <amount> [@user]` or `;gp <amount> [@user]`")
    else:
        raise error


@bot.command(name="givegold", aliases=["gg"])
async def givegold_cmd(ctx: commands.Context, amount: int, member: Optional[discord.Member] = None):
    if not is_admin_command_user(ctx.author):
        await ctx.send("You are not allowed to use this command.")
        return
    if amount <= 0:
        await ctx.send("Amount must be greater than 0.")
        return

    target = member or ctx.author
    db.add_gold(target.id, amount)
    user = db.get_user(target.id)
    if target.id == ctx.author.id:
        await ctx.send(f"💰 Gave yourself **{amount}** gold. You now have **{user['gold']}** gold.")
    else:
        await ctx.send(f"💰 Gave **{amount}** gold to {target.mention}. They now have **{user['gold']}** gold.")


@givegold_cmd.error
async def givegold_cmd_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Usage: `;givegold <amount> [@user]` or `;gg <amount> [@user]`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Usage: `;givegold <amount> [@user]` or `;gg <amount> [@user]`")
    else:
        raise error


@bot.command(name="getguildcoin", aliases=["ggc"])
async def getguildcoin_cmd(ctx: commands.Context, amount: int, member: Optional[discord.Member] = None):
    if not is_admin_command_user(ctx.author):
        await ctx.send("You are not allowed to use this command.")
        return
    if amount <= 0:
        await ctx.send("Amount must be greater than 0.")
        return

    target = member or ctx.author
    db.add_guild_coins(target.id, amount)
    user = db.get_user(target.id)
    if target.id == ctx.author.id:
        await ctx.send(f"🪙 Gave yourself **{amount}** guild coin(s). You now have **{user['guild_coins']}** guild coins.")
    else:
        await ctx.send(f"🪙 Gave **{amount}** guild coin(s) to {target.mention}. They now have **{user['guild_coins']}** guild coins.")


@getguildcoin_cmd.error
async def getguildcoin_cmd_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Usage: `;getguildcoin <amount> [@user]` or `;ggc <amount> [@user]`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Usage: `;getguildcoin <amount> [@user]` or `;ggc <amount> [@user]`")
    else:
        raise error


@bot.command(name="givecard", aliases=["gc"])
async def givecard_cmd(ctx: commands.Context, *args: str):
    if not is_admin_command_user(ctx.author):
        await ctx.send("You are not allowed to use this command.")
        return
    amount = 1
    member: Optional[discord.Member] = None
    for arg in args:
        if member is None and ctx.message.mentions:
            member = ctx.message.mentions[0]
        elif arg.isdigit():
            amount = int(arg)
    target = member or ctx.author
    if amount <= 0:
        await ctx.send("Amount must be greater than 0.")
        return
    rewards: List[SupportCardDef] = []
    duplicate_exp = 0
    owned_support_ids = {support_card_family_key(str(row["support_card_id"])) for row in db.user_support_cards(target.id)}
    for _ in range(amount):
        card = random_support_card()
        granted, exp_gained, stage_ups = grant_support_card_reward(target.id, card, owned_support_ids=owned_support_ids)
        duplicate_exp += exp_gained
        if granted:
            rewards.append(card)
    lines = [f"🃏 Processed **{amount}** random support card roll(s) for {target.mention}."]
    if rewards:
        counts = Counter(card.name for card in rewards)
        summary = "\n".join(f"• x{qty} **{name}**" for name, qty in counts.most_common(10))
        if len(counts) > 10:
            summary += f"\n• ...and {len(counts) - 10} more card types"
        lines.append(f"New support cards added: **{len(rewards)}**")
        lines.append(summary)
    if duplicate_exp:
        lines.append(f"Duplicate support cards converted into **{duplicate_exp} Card Dust**.")
    if not rewards and not duplicate_exp:
        lines.append("No support cards were granted.")
    await ctx.send("\n".join(lines))


@givecard_cmd.error
async def givecard_cmd_error(ctx: commands.Context, error: commands.CommandError):
    raise error


@bot.command(name="getspell", aliases=["gs"])
async def getspell_cmd(ctx: commands.Context, *args: str):
    if not is_admin_command_user(ctx.author):
        await ctx.send("You are not allowed to use this command.")
        return
    amount = 1
    member: Optional[discord.Member] = None
    if ctx.message.mentions:
        member = ctx.message.mentions[0]
    for arg in args:
        if arg.isdigit():
            amount = int(arg)
    target = member or ctx.author
    if amount <= 0:
        await ctx.send("Amount must be greater than 0.")
        return
    rewards: List[EquipSpellDef] = []
    for _ in range(amount):
        spell = random_equip_spell()
        db.add_equip_spell_to_inventory(target.id, spell.equip_spell_id)
        rewards.append(spell)
    counts = Counter(spell.name for spell in rewards)
    lines = [f"✨ Granted **{amount}** random equip spell(s) to {target.mention}."]
    lines.extend(f"• x{qty} **{name}**" for name, qty in counts.most_common(15))
    await ctx.send("\n".join(lines))


@getspell_cmd.error
async def getspell_cmd_error(ctx: commands.Context, error: commands.CommandError):
    raise error


# Keep the old admin command name working, but add the new name too.
try:
    bot.command(name="extramonster", aliases=["edadmin"])(bossmonster_cmd)
except Exception:
    pass
