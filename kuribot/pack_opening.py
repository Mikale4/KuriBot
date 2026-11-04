"""Opening packs and pack results."""

from __future__ import annotations

import math
import sqlite3
import time
from typing import Dict, List, Optional, Sequence, Tuple

import discord

from kuribot.config import RNG
from kuribot.models import Fighter, SupportCardDef
from kuribot.packs import normalize_pack_key, pack_display_name, pack_theme_kind_value
from kuribot.database import db
from kuribot.monsters import (
    apply_stat_bonuses, ensure_dual_monster_types, is_boss_monster_row, monster_category_for_row,
    random_monster_attribute_for_row, roll_monster_types_for_row,
)
from kuribot.cards.support_cards import (
    get_support_card_def, special_support_stage_one_cards, support_card_family_key,
    support_card_stage_one_def, support_card_stage_one_defs,
)
from kuribot.battle.special_support import fighter_matches_special_support, SPECIAL_SUPPORT_IDS
from kuribot.gacha import grant_support_card_reward


def _fighter_from_pack_pull(pull: Dict[str, object]) -> Fighter:
    return Fighter(
        monster_id=str(pull.get("monster_id") or ""),
        name=str(pull.get("name") or ""),
        attribute=str(pull.get("attribute") or ""),
        monster_types=tuple(pull.get("monster_types") or ()),
        max_hp=int(pull.get("hp") or 1),
        hp=int(pull.get("hp") or 1),
        atk=int(pull.get("atk") or 1),
        defense=int(pull.get("def") or 1),
        spd=int(pull.get("spd") or 1),
        image_path=str(pull.get("image_path") or ""),
    )


def _pack_special_support_candidates(pulls: Sequence[Dict[str, object]]) -> List[SupportCardDef]:
    candidates: List[SupportCardDef] = []
    seen: set[str] = set()
    for pull in pulls:
        fighter = _fighter_from_pack_pull(pull)
        for support_id in SPECIAL_SUPPORT_IDS:
            if support_id in seen:
                continue
            try:
                if fighter_matches_special_support(support_id, fighter):
                    card = get_support_card_def(support_id)
                    if card is not None:
                        candidates.append(support_card_stage_one_def(card.support_card_id) or card)
                        seen.add(support_id)
            except Exception:
                continue
    return candidates


def _pack_theme_support_candidates(pack_key: str, pulls: Sequence[Dict[str, object]]) -> List[SupportCardDef]:
    kind, value = pack_theme_kind_value(pack_key)
    cards = support_card_stage_one_defs()
    if kind == "attribute" and value:
        themed = [card for card in cards if str(card.attribute or "").upper() == str(value).upper()]
        RNG.shuffle(themed)
        return themed
    if kind == "type" and value:
        needle = str(value).casefold()
        themed = [card for card in cards if needle in f"{card.name} {card.description}".casefold()]
        RNG.shuffle(themed)
        return themed
    if kind == "boss":
        themed = special_support_stage_one_cards()
        RNG.shuffle(themed)
        return themed
    shuffled = list(cards)
    RNG.shuffle(shuffled)
    return shuffled


def _choose_pack_support_cards(pack_key: str, pulls: Sequence[Dict[str, object]], count: int = 3) -> List[SupportCardDef]:
    selected: List[SupportCardDef] = []
    seen_families: set[str] = set()
    pools = [_pack_special_support_candidates(pulls), _pack_theme_support_candidates(pack_key, pulls), support_card_stage_one_defs()]
    for pool in pools:
        pool = list(pool)
        RNG.shuffle(pool)
        for card in pool:
            stage_card = support_card_stage_one_def(card.support_card_id) or card
            family = support_card_family_key(stage_card.support_card_id)
            if family in seen_families:
                continue
            selected.append(stage_card)
            seen_families.add(family)
            if len(selected) >= count:
                return selected
    return selected[:count]


_PACK_POOL_CACHE: Dict[str, object] = {"at": 0.0, "all": (), "boss": ()}


def _pack_monster_pools() -> Tuple[Tuple[sqlite3.Row, ...], Tuple[sqlite3.Row, ...]]:
    # Opening a pack used to load and sort all ~9,500 monster rows for EVERY monster rolled
    # (15 full loads for "Open x5"). The pool only changes at startup, so cache it briefly.
    now = time.monotonic()
    if now - float(_PACK_POOL_CACHE["at"]) > 300 or not _PACK_POOL_CACHE["all"]:
        all_rows = tuple(db.all_monsters())
        _PACK_POOL_CACHE["all"] = all_rows
        _PACK_POOL_CACHE["boss"] = tuple(row for row in all_rows if is_boss_monster_row(row))
        _PACK_POOL_CACHE["at"] = now
    return _PACK_POOL_CACHE["all"], _PACK_POOL_CACHE["boss"]  # type: ignore[return-value]


def _pack_roll_single_monster(pack_key: str, user_id: Optional[int] = None) -> Dict[str, object]:
    pool, boss_pool = _pack_monster_pools()
    if not pool:
        raise RuntimeError("No monster pool was loaded. Check your CARDS_DIR path in .env.")

    # Pack openings use equal monster odds across the loaded monster pool.
    # The Extra Deck Themed Pack draws only Extra Deck Monsters - previously its theme was
    # ignored for monsters, so it rolled the normal pool like any other pack.
    kind, value = pack_theme_kind_value(pack_key)
    monster = RNG.choice(boss_pool if (kind == "boss" and boss_pool) else pool)
    category = monster_category_for_row(monster)
    monster_types = roll_monster_types_for_row(monster)

    pull: Dict[str, object] = {
        "monster_id": monster["monster_id"],
        "name": monster["name"],
        "attribute": random_monster_attribute_for_row(monster),
        "monster_types": monster_types,
        "hp": int(monster["hp"]),
        "atk": int(monster["atk"]),
        "def": int(monster["def"]),
        "spd": int(monster["spd"]),
        "image_path": monster["image_path"],
        "monster_category": category,
    }

    if kind == "attribute" and value:
        pull["attribute"] = str(value).upper()
    elif kind == "type" and value:
        existing_types = [str(t) for t in tuple(pull.get("monster_types") or ()) if str(t) != value]
        pull["monster_types"] = tuple([value] + existing_types)

    pull["monster_types"] = ensure_dual_monster_types(tuple(pull.get("monster_types") or ()))
    hp, atk, defense, spd = apply_stat_bonuses(
        int(monster["hp"]),
        int(monster["atk"]),
        int(monster["def"]),
        int(monster["spd"]),
        tuple(pull.get("monster_types") or ()),
        str(pull.get("attribute") or ""),
        category,
    )
    pull.update({"hp": hp, "atk": atk, "def": defense, "spd": spd})
    return pull


def open_pack_for_user(user_id: int, pack_key: str) -> Dict[str, object]:
    pack_key = normalize_pack_key(pack_key) or "random_themed"
    if not db.spend_pack(user_id, pack_key, 1):
        raise ValueError(f"You do not own a {pack_display_name(pack_key)}.")
    pulls = [_pack_roll_single_monster(pack_key, user_id=user_id) for _ in range(3)]
    for pull in pulls:
        db.add_monster_to_inventory(user_id, str(pull["monster_id"]), tuple(pull.get("monster_types") or ()), monster_attribute=str(pull.get("attribute") or ""))
    support_cards = _choose_pack_support_cards(pack_key, pulls, 3)
    owned_support_ids = {support_card_family_key(str(row["support_card_id"])) for row in db.user_support_cards(user_id)}
    dust = 0
    granted_count = 0
    for card in support_cards:
        granted, exp_gained, _stage_ups = grant_support_card_reward(user_id, card, owned_support_ids=owned_support_ids)
        dust += exp_gained
        if granted:
            granted_count += 1
    return {"pack_key": pack_key, "pack_name": pack_display_name(pack_key), "monsters": pulls, "support_cards": support_cards, "card_dust": dust, "new_support_cards": granted_count}


def format_pack_open_result(result: Dict[str, object], index: Optional[int] = None) -> str:
    prefix = f"`{index:03d}.` " if index is not None else ""
    monster_text = ", ".join(str(p.get("name")) for p in result.get("monsters", []))
    support_text = ", ".join(card.name for card in result.get("support_cards", []))
    dust = int(result.get("card_dust") or 0)
    dust_text = f" (+{dust} Card Dust)" if dust else ""
    return f"{prefix}**{result.get('pack_name')}** → Monsters: {monster_text} • Supports: {support_text}{dust_text}"


def build_pack_results_embed(user_name: str, results: Sequence[Dict[str, object]], page: int = 0, page_size: int = 5) -> discord.Embed:
    results = list(results)
    total_pages = max(1, math.ceil(max(1, len(results)) / page_size))
    page = max(0, min(int(page or 0), total_pages - 1))
    start = page * page_size
    end = min(len(results), start + page_size)
    lines = [format_pack_open_result(result, start + idx + 1) for idx, result in enumerate(results[start:end])]
    embed = discord.Embed(
        title=f"🎁 Pack Opening Results ({len(results):,} pack{'s' if len(results) != 1 else ''})",
        description=("\n".join(lines) if lines else "No packs opened.")[:4096],
        color=discord.Color.gold(),
    )
    monster_count = sum(len(result.get("monsters", [])) for result in results)
    support_count = sum(len(result.get("support_cards", [])) for result in results)
    dust = sum(int(result.get("card_dust") or 0) for result in results)
    boss_count = sum(1 for result in results for pull in result.get("monsters", []) if str(pull.get("monster_category") or "") == "boss")
    embed.add_field(name="Monsters", value=str(monster_count), inline=True)
    embed.add_field(name="Support Cards", value=str(support_count), inline=True)
    embed.add_field(name="Extra Deck Monsters", value=str(boss_count), inline=True)
    if dust:
        embed.add_field(name="Card Dust", value=f"+{dust}", inline=True)
    embed.set_footer(text=f"{user_name} • Page {page + 1}/{total_pages}")
    return embed


PACK_MIGRATION_NOTICE_KEY = "ticket_to_pack_banner_notice_v1"


def ticket_to_pack_banner_notice_text() -> str:
    return (
        "📦 **Tickets have changed into packs.** Ticket rewards now pay packs 1:1. "
        "Use `;packs` to view, open, bulk-open, gift, sell, and buy packs."
    )
