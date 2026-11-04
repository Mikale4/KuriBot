"""Shop offer generation and pricing."""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from kuribot.config import (
    CARD_SHOP_MAX_COST, CARD_SHOP_MIN_COST, CARD_SHOP_SIZE, EQUIP_SHOP_MAX_COST,
    EQUIP_SHOP_MIN_COST, EQUIP_SHOP_SIZE, MONSTER_SHOP_SIZE, PACK_PURCHASE_COST, RNG,
    SHOP_PAID_REFRESH_COST,
)
from kuribot.models import EquipSpellDef, SupportCardDef
from kuribot.timeutil import is_current_reset_window, utc_now
from kuribot.cards.equip_spells import EQUIP_SPELL_DEFS, get_equip_spell_def, init_equip_spells
from kuribot.packs import PACK_DEFS
from kuribot.database import db
from kuribot.cards.support_cards import (
    get_support_card_def, is_support_card_stage_one, support_card_family_key,
    support_card_stage_one_def, support_card_stage_one_defs,
)


def generate_monster_shop_offers(user_id: int, *, force: bool = False) -> List[sqlite3.Row]:
    existing = db.get_monster_shop_offers(user_id)
    now = utc_now()
    if not force and len(existing) >= MONSTER_SHOP_SIZE and all(is_current_reset_window(row["generated_at"], now) for row in existing):
        return existing
    db.clear_monster_shop_offers(user_id)
    generated_at = now.isoformat()
    # The Monster Shop is now a Pack Shop. It offers randomized themed packs while
    # keeping the old shop size, refresh cadence, dropdown flow, and purchase state.
    candidate_keys = [key for key in PACK_DEFS.keys() if key not in {"random_themed", "boss_themed"}]
    RNG.shuffle(candidate_keys)
    picks = candidate_keys[:MONSTER_SHOP_SIZE]
    while len(picks) < MONSTER_SHOP_SIZE:
        picks.append("random_themed")
    for slot_index, pack_key in enumerate(picks, start=1):
        db.save_monster_shop_offer(user_id, slot_index, pack_key, generated_at=generated_at)
    return db.get_monster_shop_offers(user_id)


def generate_card_shop_offers(user_id: int, *, force: bool = False) -> List[sqlite3.Row]:
    existing = db.get_card_shop_offers(user_id)
    now = utc_now()
    existing_is_current = len(existing) >= CARD_SHOP_SIZE and all(is_current_reset_window(row["generated_at"], now) for row in existing)
    existing_is_stage_one_only = all(is_support_card_stage_one(str(row["support_card_id"])) for row in existing)
    if not force and existing_is_current and existing_is_stage_one_only:
        return existing
    db.clear_card_shop_offers(user_id)
    stage_one_cards = [card for card in support_card_stage_one_defs() if is_support_card_stage_one(card.support_card_id)]
    owned_families = {support_card_family_key(str(row["support_card_id"])) for row in db.user_support_cards(user_id)}
    unowned_cards = [card for card in stage_one_cards if support_card_family_key(card.support_card_id) not in owned_families]
    pool = unowned_cards if len(unowned_cards) >= CARD_SHOP_SIZE else stage_one_cards
    shuffled = list(pool)
    RNG.shuffle(shuffled)
    picks: List[SupportCardDef] = []
    seen_families = set()
    for card in shuffled:
        stage_one_card = support_card_stage_one_def(card.support_card_id) or card
        family = support_card_family_key(stage_one_card.support_card_id)
        if family in seen_families:
            continue
        seen_families.add(family)
        picks.append(stage_one_card)
        if len(picks) >= CARD_SHOP_SIZE:
            break
    generated_at = now.isoformat()
    for slot_index, card in enumerate(picks, start=1):
        stage_one_card = support_card_stage_one_def(card.support_card_id) or card
        db.save_card_shop_offer(user_id, slot_index, stage_one_card.support_card_id, generated_at=generated_at)
    return db.get_card_shop_offers(user_id)


def generate_equip_shop_offers(user_id: int, *, force: bool = False) -> List[sqlite3.Row]:
    existing = db.get_equip_shop_offers(user_id)
    now = utc_now()
    if not force and len(existing) >= EQUIP_SHOP_SIZE and all(is_current_reset_window(row["generated_at"], now) for row in existing):
        return existing
    db.clear_equip_shop_offers(user_id)
    init_equip_spells()
    owned_ids = {str(row["equip_spell_id"]) for row in db.user_equip_spells(user_id)}
    unowned_spells = [spell for spell in EQUIP_SPELL_DEFS if spell.equip_spell_id not in owned_ids]
    fallback_spells = list(EQUIP_SPELL_DEFS)
    RNG.shuffle(unowned_spells)
    RNG.shuffle(fallback_spells)
    picks: List[EquipSpellDef] = []
    seen_ids = set()
    for spell in unowned_spells + fallback_spells:
        if spell.equip_spell_id in seen_ids:
            continue
        seen_ids.add(spell.equip_spell_id)
        picks.append(spell)
        if len(picks) >= EQUIP_SHOP_SIZE:
            break
    generated_at = now.isoformat()
    for slot_index, spell in enumerate(picks, start=1):
        db.save_equip_shop_offer(user_id, slot_index, spell.equip_spell_id, generated_at=generated_at)
    return db.get_equip_shop_offers(user_id)


def get_monster_shop_offers(user_id: int) -> List[sqlite3.Row]:
    offers = db.get_monster_shop_offers(user_id)
    now = utc_now()
    if len(offers) < MONSTER_SHOP_SIZE or any(not is_current_reset_window(row["generated_at"], now) for row in offers):
        offers = generate_monster_shop_offers(user_id, force=True)
    return offers


def get_card_shop_offers(user_id: int) -> List[sqlite3.Row]:
    offers = db.get_card_shop_offers(user_id)
    now = utc_now()
    if (
        len(offers) < CARD_SHOP_SIZE
        or any(not is_current_reset_window(row["generated_at"], now) for row in offers)
        or any(not is_support_card_stage_one(str(row["support_card_id"])) for row in offers)
    ):
        offers = generate_card_shop_offers(user_id, force=True)
    return offers


def get_equip_shop_offers(user_id: int) -> List[sqlite3.Row]:
    offers = db.get_equip_shop_offers(user_id)
    now = utc_now()
    if len(offers) < EQUIP_SHOP_SIZE or any(not is_current_reset_window(row["generated_at"], now) for row in offers):
        offers = generate_equip_shop_offers(user_id, force=True)
    return offers


def refresh_shop_offers(user_id: int, shop_kind: str):
    normalized = str(shop_kind or '').strip().lower()
    if normalized == 'monster':
        generate_monster_shop_offers(user_id, force=True)
        return
    if normalized == 'card':
        generate_card_shop_offers(user_id, force=True)
        return
    if normalized == 'equip':
        generate_equip_shop_offers(user_id, force=True)
        return
    raise ValueError(f"Unknown shop kind: {shop_kind}")


def shop_refresh_button_label(user_id: int, shop_kind: str) -> str:
    return "Refresh Free" if db.can_use_shop_refresh(user_id, shop_kind) else f"Refresh ({SHOP_PAID_REFRESH_COST:,} coins)"


def card_shop_offer_to_card(offer_row: sqlite3.Row) -> Optional[SupportCardDef]:
    return get_support_card_def(str(offer_row["support_card_id"]))


def _stable_offer_price(seed_key: str, minimum: int, maximum: int) -> int:
    span = max(0, int(maximum) - int(minimum))
    if span <= 0:
        return int(minimum)
    seed_total = 0
    for idx, ch in enumerate(seed_key, 1):
        seed_total += idx * ord(ch)
    return int(minimum) + (seed_total % (span + 1))


def monster_shop_offer_price(offer_row: sqlite3.Row) -> int:
    return PACK_PURCHASE_COST


def card_shop_offer_price(offer_row: sqlite3.Row) -> int:
    seed_key = f"card|{offer_row['user_id']}|{offer_row['slot_index']}|{offer_row['generated_at']}|{offer_row['support_card_id']}"
    return _stable_offer_price(seed_key, CARD_SHOP_MIN_COST, CARD_SHOP_MAX_COST)


def equip_shop_offer_to_spell(offer_row: sqlite3.Row) -> Optional[EquipSpellDef]:
    return get_equip_spell_def(str(offer_row["equip_spell_id"]))


def equip_spell_power_score(spell: EquipSpellDef) -> int:
    text = f"{spell.name} {spell.description}".lower()
    score = 8

    def has(*phrases: str) -> bool:
        return any(phrase in text for phrase in phrases)

    if has("once per battle", "first time"):
        score += 8
    if has("revive", "revived"):
        score += 26
    if has("all allies", "all enemies"):
        score += 16
    elif has("ally", "enemy"):
        score += 6
    if has("immunity", "immune to all debuffs"):
        score += 20
    elif has("immune to", "immunity."):
        score += 12
    if has("all removable buffs from all enemies", "remove 1 random removable buff from all enemies"):
        score += 18
    elif has("remove 1 removable buff", "remove 1 random removable buff", "remove all removable buffs"):
        score += 10
    if has("double damage", "deals double damage", "attacks hit twice"):
        score += 22
    elif has("50% more damage", "60% more damage"):
        score += 18
    elif has("30% more damage", "25% more damage", "10% more damage"):
        score += 10
    if has("reflect", "returns 50% of damage", "counterattack for 100% damage"):
        score += 14
    elif has("counterattack", "precog"):
        score += 10
    if has("stun on all enemies", "freeze on all enemies", "sleep on all enemies", "fear on all enemies", "taunt on all enemies"):
        score += 18
    elif has("stun", "freeze", "petrify", "sleep", "fear", "charm", "silence"):
        score += 8
    if has("bomb on all enemies"):
        score += 18
    elif has("bomb"):
        score += 10
    if has("shield equal to 25% of the equipped monster's max hp", "grant all allies a shield"):
        score += 14
    elif has("shield equal to 25% of its max hp", "gains protection"):
        score += 10
    if has("heal all allies by 30% max hp", "grant all allies recovery"):
        score += 18
    elif has("heal the ally with the lowest hp", "heal it to 100% hp", "heals for 50%", "heal the equipped monster by"):
        score += 10
    if has("takes 40% less damage", "takes 25% less damage", "allies take 15% less damage"):
        score += 14
    if has("cannot be knocked out", "survives at 1 hp"):
        score += 16
    if has("apply burn, poison, fear, or silence at random", "heal block"):
        score += 10
    if has("attack again"):
        score += 12
    if has("while the equipped monster is alive"):
        score += 6

    return max(0, min(100, score))


def equip_spell_price(spell: EquipSpellDef) -> int:
    score = equip_spell_power_score(spell)
    raw_price = EQUIP_SHOP_MIN_COST + round((score / 100.0) * (EQUIP_SHOP_MAX_COST - EQUIP_SHOP_MIN_COST))
    rounded_price = int(round(raw_price / 100.0) * 100)
    return max(EQUIP_SHOP_MIN_COST, min(EQUIP_SHOP_MAX_COST, rounded_price))


def equip_shop_offer_price(offer_row: sqlite3.Row) -> int:
    spell = equip_shop_offer_to_spell(offer_row)
    if spell is None:
        return EQUIP_SHOP_MIN_COST
    return equip_spell_price(spell)
