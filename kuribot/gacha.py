"""Summoning (gacha) pulls and banners."""

from __future__ import annotations

import math
import random
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import discord

from kuribot.config import (
    BOSS_MONSTER_SUMMON_RATE, FEATURED_MONSTER_COUNT, FEATURED_MONSTER_SUMMON_RATE, RNG,
    SIGNATURE_MONSTER_SUMMON_RATE, STANDARD_MONSTER_SUMMON_RATE, SUPPORT_CARD_DROP_CHANCE,
    SUPPORT_CARD_DUPLICATE_DUST,
)
from kuribot.models import SupportCardDef
from kuribot.database import db
from kuribot.render.themes import get_ui_theme_palette
from kuribot.monsters import (
    apply_stat_bonuses, category_title_for_monster, format_monster_types, is_boss_monster_row,
    is_signature_monster_row, monster_category_for_row, random_monster_attribute_for_row,
    roll_monster_types_for_row,
)
from kuribot.cards.support_cards import (
    canonical_support_card_id, random_support_card, support_card_family_key,
    support_card_stage_one_def, support_card_summary,
)


def current_banner_seed(user_id: Optional[int] = None) -> int:
    now = datetime.now(timezone.utc)
    base_seed = int(now.strftime("%Y%m%d"))
    if user_id is None:
        return base_seed
    return ((base_seed * 1000003) ^ int(user_id)) & 0xFFFFFFFF


def get_featured_monsters(pool: Sequence[sqlite3.Row], count: int = FEATURED_MONSTER_COUNT, *, user_id: Optional[int] = None) -> List[sqlite3.Row]:
    if not pool:
        return []
    eligible_pool = [row for row in pool if not is_boss_monster_row(row) and not is_signature_monster_row(row)]
    if not eligible_pool:
        return []
    count = min(count, len(eligible_pool))
    seeded_rng = random.Random(current_banner_seed(user_id))
    return seeded_rng.sample(list(eligible_pool), k=count)


def _split_gacha_pools(pool: Sequence[sqlite3.Row], *, user_id: Optional[int] = None, featured_pool: Optional[Sequence[sqlite3.Row]] = None) -> Tuple[List[sqlite3.Row], List[sqlite3.Row], List[sqlite3.Row], List[sqlite3.Row]]:
    indexed_pool = list(pool)
    boss_pool = [row for row in indexed_pool if is_boss_monster_row(row)]
    signature_pool = [row for row in indexed_pool if is_signature_monster_row(row)]
    remaining_pool = [row for row in indexed_pool if not is_boss_monster_row(row) and not is_signature_monster_row(row)]
    if featured_pool is None:
        resolved_featured_pool = get_featured_monsters(remaining_pool, user_id=user_id)
    else:
        featured_ids_from_arg = {str(row["monster_id"]) for row in featured_pool if not is_boss_monster_row(row) and not is_signature_monster_row(row)}
        resolved_featured_pool = [row for row in remaining_pool if str(row["monster_id"]) in featured_ids_from_arg]
    featured_ids = {str(row["monster_id"]) for row in resolved_featured_pool}
    standard_pool = [row for row in remaining_pool if str(row["monster_id"]) not in featured_ids]
    return boss_pool, signature_pool, resolved_featured_pool, standard_pool


def perform_gacha_pulls(pool: Sequence[sqlite3.Row], pull_count: int, *, user_id: Optional[int] = None, featured_pool: Optional[Sequence[sqlite3.Row]] = None) -> Tuple[List[Dict[str, object]], List[sqlite3.Row]]:
    indexed_pool = list(pool)
    boss_pool, signature_pool, featured, standard_pool = _split_gacha_pools(indexed_pool, user_id=user_id, featured_pool=featured_pool)

    pulls: List[Dict[str, object]] = []
    for _ in range(pull_count):
        roll = RNG.random()
        if boss_pool and roll < BOSS_MONSTER_SUMMON_RATE:
            monster = RNG.choice(boss_pool)
        elif signature_pool and roll < BOSS_MONSTER_SUMMON_RATE + SIGNATURE_MONSTER_SUMMON_RATE:
            monster = RNG.choice(signature_pool)
        elif featured and roll < BOSS_MONSTER_SUMMON_RATE + SIGNATURE_MONSTER_SUMMON_RATE + FEATURED_MONSTER_SUMMON_RATE:
            monster = RNG.choice(featured)
        elif standard_pool:
            monster = RNG.choice(standard_pool)
        elif featured:
            monster = RNG.choice(featured)
        elif signature_pool:
            monster = RNG.choice(signature_pool)
        elif boss_pool:
            monster = RNG.choice(boss_pool)
        else:
            raise RuntimeError("No monster pool available for summons.")

        monster_types = roll_monster_types_for_row(monster, featured_monster_ids=[row["monster_id"] for row in featured])
        category = monster_category_for_row(monster)
        hp, atk, defense, spd = apply_stat_bonuses(
            int(monster["hp"]),
            int(monster["atk"]),
            int(monster["def"]),
            int(monster["spd"]),
            monster_types,
            monster["attribute"],
            category,
        )
        pulls.append(
            {
                "monster_id": monster["monster_id"],
                "name": monster["name"],
                "attribute": random_monster_attribute_for_row(monster),
                "monster_types": monster_types,
                "hp": hp,
                "atk": atk,
                "def": defense,
                "spd": spd,
                "image_path": monster["image_path"],
                "monster_category": category,
            }
        )
    return pulls, featured


def grant_support_card_reward(

    user_id: int,
    card: SupportCardDef,
    owned_support_ids: Optional[set[str]] = None,
    obtained_at: Optional[str] = None,
) -> Tuple[bool, int, int]:
    owned_ids = owned_support_ids
    stage_one_card = support_card_stage_one_def(card.support_card_id) or card
    family_key = support_card_family_key(stage_one_card.support_card_id)
    if owned_ids is None:
        owned_ids = {support_card_family_key(str(row["support_card_id"])) for row in db.user_support_cards(user_id)}
    if family_key in owned_ids:
        db.add_card_dust(user_id, SUPPORT_CARD_DUPLICATE_DUST)
        return False, SUPPORT_CARD_DUPLICATE_DUST, 0
    db.add_support_card_to_inventory(user_id, canonical_support_card_id(stage_one_card.support_card_id), obtained_at=obtained_at, exp=0)
    owned_ids.add(family_key)
    return True, 0, 0


def grant_support_cards_for_pulls(user_id: int, pull_count: int, pulled_monsters: Optional[Sequence[Dict[str, object]]] = None) -> Tuple[List[SupportCardDef], int, int]:
    rewards: List[SupportCardDef] = []
    duplicate_exp = 0
    stage_ups = 0
    owned_support_ids = {support_card_family_key(str(row["support_card_id"])) for row in db.user_support_cards(user_id)}
    for idx in range(pull_count):
        rolled_card: Optional[SupportCardDef] = None
        if RNG.random() <= SUPPORT_CARD_DROP_CHANCE:
            rolled_card = random_support_card()
        if rolled_card is None:
            continue
        card = support_card_stage_one_def(rolled_card.support_card_id) or rolled_card
        granted, exp_gained, gained_stage_ups = grant_support_card_reward(user_id, card, owned_support_ids=owned_support_ids)
        duplicate_exp += exp_gained
        stage_ups += gained_stage_ups
        if granted:
            rewards.append(support_card_stage_one_def(card.support_card_id) or card)
    return rewards, duplicate_exp, stage_ups


def format_pull_results(pulls: Sequence[Dict[str, object]]) -> str:
    counts = Counter((str(pull["name"]), str(pull["attribute"]), tuple(pull["monster_types"])) for pull in pulls)
    lines = []
    for (name, attribute, monster_types), qty in counts.most_common():
        prefix = f"x{qty} " if qty > 1 else ""
        category_title = category_title_for_monster(name)
        category_suffix = f" • {category_title}" if category_title else ""
        lines.append(f"• {prefix}**{name}** ({attribute}) [{format_monster_types(monster_types)}]{category_suffix}")
    return "\n".join(lines) if lines else "No monsters opened."


def create_gacha_banner_embed(user_name: str, tickets: int, premium_tickets: int, boss_monster_tickets: int, pool: Sequence[sqlite3.Row], featured: Sequence[sqlite3.Row], theme_name: str = "gray") -> discord.Embed:
    boss_pool, signature_pool, featured_pool, standard_pool = _split_gacha_pools(pool, featured_pool=featured)
    per_featured = (FEATURED_MONSTER_SUMMON_RATE / len(featured_pool) * 100) if featured_pool else 0.0
    per_standard = (STANDARD_MONSTER_SUMMON_RATE / len(standard_pool) * 100) if standard_pool else 0.0
    per_boss = (BOSS_MONSTER_SUMMON_RATE / len(boss_pool) * 100) if boss_pool else 0.0
    featured_lines = [f"• **{row['name']}** ({row['attribute']}) — {per_featured:.2f}%" for row in featured_pool]
    palette = get_ui_theme_palette(theme_name)
    embed = discord.Embed(
        title="✨ Summon Banner",
        description="Support Cards have a 20% chance to drop per pack opening. Pack Opening lets you choose a monster by name, attribute, and monster type, but Extra Deck Monsters are excluded. Extra Deck Summon spends an Extra Deck Monster Pack to summon a random Extra Deck Monster.",
        color=discord.Color.from_rgb(*palette["outer_outline"]),
    )
    embed.add_field(
        name="Summon Rates",
        value=(
            f"Extra Deck Monsters total: **{BOSS_MONSTER_SUMMON_RATE * 100:.1f}%** ({per_boss:.2f}% each)\n"
            f"Featured Monsters total: **{FEATURED_MONSTER_SUMMON_RATE * 100:.1f}%**\n"
            f"Each featured monster: **{per_featured:.2f}%**\n"
            f"All other monsters total: **{STANDARD_MONSTER_SUMMON_RATE * 100:.1f}%**\n"
            f"Each non-featured standard monster: **{per_standard:.2f}%**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Featured Monsters",
        value="\n".join(featured_lines) if featured_lines else "No featured monsters available.",
        inline=False,
    )
    if featured:
        embed.set_image(url="attachment://banner_featured.png")
    embed.set_footer(text=f"{user_name} • Packs: {tickets} • Random Packs: {premium_tickets} • Extra Deck Monster Packs: {boss_monster_tickets}")
    return embed


BOSS_CUTSCENE_FLAVOR_LINES = (
    "A golden light floods the summoning circle.",
    "The air shakes as an overwhelming presence descends.",
    "A divine aura erupts from the banner.",
    "The summon screen burns with radiant energy.",
)


def has_boss_pull(pulls: Sequence[Dict[str, object]]) -> bool:
    return any(str(pull.get("monster_category") or "").strip().lower() == "boss" for pull in pulls)


def create_boss_cutscene_embed(user_name: str, pulls: Sequence[Dict[str, object]], pull_count: int) -> discord.Embed:
    boss_pulls = [pull for pull in pulls if str(pull.get("monster_category") or "").strip().lower() == "boss"]
    boss_names = [str(pull.get("name") or "Unknown") for pull in boss_pulls]
    unique_names = list(dict.fromkeys(boss_names))
    featured_name = unique_names[0] if unique_names else "An Extra Deck Monster"
    title = "Extra Deck Monster Summon!" if len(unique_names) == 1 else "Extra Deck Monsters Summoned!"
    cycle_index = sum(ord(ch) for ch in "|".join(unique_names)) % len(BOSS_CUTSCENE_FLAVOR_LINES)
    dramatic_line = BOSS_CUTSCENE_FLAVOR_LINES[cycle_index]
    lines = [
        f"The summoning circle trembles as **{featured_name}** makes a grand appearance.",
        dramatic_line,
    ]
    if len(unique_names) > 1:
        extra_names = ", ".join(f"**{name}**" for name in unique_names[1:])
        lines.append(f"The banner erupts again with the arrival of {extra_names}.")
    lines.append("An Extra Deck Monster has answered the call. Press **Continue** to reveal your pack results.")
    embed = discord.Embed(title=title, description="\n".join(lines), color=discord.Color.gold())
    embed.add_field(name="Summon Type", value=f"x{int(pull_count):,}", inline=True)
    embed.add_field(name="Boss Hits", value=str(len(boss_pulls)), inline=True)
    embed.add_field(name="Summoner", value=user_name, inline=True)
    return embed


def _format_summon_all_pull_line(index: int, pulled: Dict[str, object]) -> str:
    category_title = category_title_for_monster(str(pulled.get("name") or ""))
    category_suffix = f" • {category_title}" if category_title else ""
    return (
        f"`{index:03d}.` **{pulled.get('name', 'Unknown')}** "
        f"({pulled.get('attribute', 'Unknown')}) "
        f"[{format_monster_types(tuple(pulled.get('monster_types') or ()))}]{category_suffix}"
    )


def create_summon_all_result_embed(
    user_name: str,
    tickets: int,
    pulls: Sequence[Dict[str, object]],
    featured: Sequence[sqlite3.Row],
    support_rewards: Sequence[SupportCardDef],
    duplicate_exp: int = 0,
    total_stage_ups: int = 0,
    duplicate_ticket_count: int = 0,
    page: int = 0,
    page_size: int = 20,
) -> discord.Embed:
    pulls = list(pulls)
    page_size = max(1, int(page_size or 20))
    total_pages = max(1, math.ceil(len(pulls) / page_size))
    page = max(0, min(int(page or 0), total_pages - 1))
    start = page * page_size
    end = min(len(pulls), start + page_size)
    shown_pulls = pulls[start:end]

    result_lines = [_format_summon_all_pull_line(start + idx + 1, pulled) for idx, pulled in enumerate(shown_pulls)]
    results_text = "\n".join(result_lines) if result_lines else "No monsters opened."
    embed = discord.Embed(
        title=f"✨ Summon All Results ({len(pulls):,} pulls)",
        description=(
            f"Used **{len(pulls):,} Random Pack(s)**. Showing pulls **{start + 1:,}-{end:,}** of **{len(pulls):,}**.\n"
            "This result is embed-only, so no summon image was generated.\n\n"
            f"**Results • Page {page + 1}/{total_pages}**\n{results_text}"
        )[:4096],
        color=discord.Color.gold(),
    )

    featured_ids = {str(f["monster_id"]) for f in featured}
    featured_hits = sum(1 for pull in pulls if str(pull.get("monster_id")) in featured_ids)
    boss_hits = sum(1 for pull in pulls if str(pull.get("monster_category") or "").strip().lower() == "boss")
    dual_type_hits = sum(1 for pull in pulls if len(tuple(pull.get("monster_types") or ())) > 1)

    embed.add_field(name="Featured Hits", value=str(featured_hits), inline=True)
    embed.add_field(name="Boss Hits", value=str(boss_hits), inline=True)
    embed.add_field(name="Dual-Type Rolls", value=str(dual_type_hits), inline=True)
    embed.add_field(name="Support Cards", value=str(len(support_rewards)), inline=True)
    embed.add_field(name="Card Dust", value=str(duplicate_exp), inline=True)
    embed.add_field(name="Random Packs Won", value=str(duplicate_ticket_count), inline=True)
    embed.add_field(name="Packs Left", value=str(tickets), inline=True)
    if support_rewards:
        embed.add_field(name="Support Card Drops", value=support_card_summary(support_rewards)[:1024], inline=False)
    embed.set_footer(text=f"{user_name} • Page {page + 1}/{total_pages} • Press Banner to return")
    return embed


def create_gacha_result_embed(user_name: str, tickets: int, pulls: Sequence[Dict[str, object]], featured: Sequence[sqlite3.Row], pull_count: int, support_rewards: Sequence[SupportCardDef], duplicate_exp: int = 0, total_stage_ups: int = 0, duplicate_ticket_count: int = 0) -> discord.Embed:
    title = "✨ Summon Result" if pull_count == 1 else "✨ Summon x10 Results"
    embed = discord.Embed(title=title, color=discord.Color.gold())
    if pull_count == 1 and pulls:
        pulled = pulls[0]
        lines = [f"You opened **{pulled['name']}**!"]
        if support_rewards:
            lines.append("Hey, look! That monster is carrying a Support Card! Grab it off of them!")
            lines.extend(f"**{user_name}** received Support Card: **{card.name}**" for card in support_rewards)
        if duplicate_exp:
            lines.append(f"Duplicate Support Card converted into **{duplicate_exp} Card Dust**.")
        embed.description = "\n".join(lines)
        embed.add_field(name="Element", value=str(pulled["attribute"]), inline=True)
        embed.add_field(name="Monster Type", value=format_monster_types(pulled["monster_types"]), inline=True)
        embed.add_field(name="HP", value=str(pulled["hp"]), inline=True)
        embed.add_field(name="ATK", value=str(pulled["atk"]), inline=True)
        embed.add_field(name="DEF", value=str(pulled["def"]), inline=True)
        embed.add_field(name="SPD", value=str(pulled["spd"]), inline=True)
        if Path(str(pulled["image_path"])).exists():
            embed.set_image(url="attachment://pull.png")
    else:
        desc_lines = [format_pull_results(pulls)]
        if support_rewards:
            desc_lines.append("")
            desc_lines.append("Hey, look! Some of those monsters were carrying Support Cards! Grab them off of them!")
            desc_lines.extend(f"**{user_name}** received Support Card: **{card.name}**" for card in support_rewards)
        if duplicate_exp:
            desc_lines.append("")
            desc_lines.append(f"Duplicate Support Cards converted into **{duplicate_exp} Card Dust**.")
        embed.description = "\n".join(desc_lines)

    featured_hits = sum(1 for pull in pulls if pull["monster_id"] in {f["monster_id"] for f in featured})
    dual_type_hits = sum(1 for pull in pulls if len(tuple(pull["monster_types"])) > 1)
    embed.add_field(name="Featured Hits", value=str(featured_hits), inline=True)
    embed.add_field(name="Dual-Type Rolls", value=str(dual_type_hits), inline=True)
    embed.add_field(name="Support Cards", value=str(len(support_rewards)), inline=True)
    embed.add_field(name="Card Dust", value=str(duplicate_exp), inline=True)
    embed.add_field(name="Random Packs Won", value=str(duplicate_ticket_count), inline=True)
    if support_rewards:
        embed.add_field(name="Support Card Drops", value=support_card_summary(support_rewards)[:1024], inline=False)
    embed.add_field(name="Packs Left", value=str(tickets), inline=True)
    embed.set_footer(text=f"{user_name} • Press Summon Again to repeat this summon")
    return embed


def create_boss_summon_result_embed(user_name: str, boss_tickets: int, pull: Dict[str, object], duplicate_ticket_count: int = 0) -> discord.Embed:
    embed = discord.Embed(title="✨ Extra Deck Summon Result", color=discord.Color.gold())
    lines = [f"You used **1 Extra Deck Monster Pack** and opened **{pull['name']}**!"]
    if duplicate_ticket_count:
        lines.append(f"Duplicate monster converted into **{duplicate_ticket_count} Random Pack(s)**.")
    embed.description = "\n".join(lines)
    embed.add_field(name="Element", value=str(pull["attribute"]), inline=True)
    embed.add_field(name="Monster Type", value=format_monster_types(pull["monster_types"]), inline=True)
    embed.add_field(name="HP", value=str(pull["hp"]), inline=True)
    embed.add_field(name="ATK", value=str(pull["atk"]), inline=True)
    embed.add_field(name="DEF", value=str(pull["def"]), inline=True)
    embed.add_field(name="SPD", value=str(pull["spd"]), inline=True)
    embed.add_field(name="Extra Deck Monster Packs Left", value=str(boss_tickets), inline=True)
    embed.add_field(name="Random Packs Won", value=str(duplicate_ticket_count), inline=True)
    if Path(str(pull["image_path"])).exists():
        embed.set_image(url="attachment://pull.png")
    embed.set_footer(text=f"{user_name} • Press Banner to return")
    return embed


def perform_boss_monster_ticket_pull(pool: Sequence[sqlite3.Row]) -> Dict[str, object]:
    boss_pool, _, _, _ = _split_gacha_pools(pool)
    if not boss_pool:
        raise RuntimeError("No Extra Deck Monsters were found in the monster pool.")
    monster = RNG.choice(boss_pool)
    monster_types = roll_monster_types_for_row(monster)
    category = monster_category_for_row(monster)
    attribute = random_monster_attribute_for_row(monster)
    hp, atk, defense, spd = apply_stat_bonuses(
        int(monster["hp"]), int(monster["atk"]), int(monster["def"]), int(monster["spd"]),
        monster_types, attribute, category,
    )
    return {
        "monster_id": monster["monster_id"],
        "name": monster["name"],
        "attribute": attribute,
        "monster_types": monster_types,
        "hp": hp,
        "atk": atk,
        "def": defense,
        "spd": spd,
        "image_path": monster["image_path"],
        "monster_category": category,
    }
