"""Inbox mail rewards."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from kuribot.models import SupportCardDef
from kuribot.timeutil import EASTERN_TZ
from kuribot.cards.equip_spells import random_equip_spell
from kuribot.database import db
from kuribot.monsters import decode_monster_types, format_monster_types, roll_monster_types_for_row
from kuribot.cards.support_cards import random_support_card, support_card_family_key
from kuribot.gacha import grant_support_card_reward


def format_mail_timestamp(timestamp: Optional[str]) -> str:
    if not timestamp:
        return "Unknown time"
    try:
        dt = datetime.fromisoformat(str(timestamp))
    except Exception:
        return "Unknown time"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone(EASTERN_TZ)
    return local_dt.strftime("%b %d, %Y • %I:%M %p ET")


def reward_type_label(reward_type: Optional[str], reward_amount: int) -> str:
    if not reward_type or reward_amount <= 0:
        return "No reward"
    normalized = str(reward_type).lower()
    if normalized == "bundle":
        return "Reward Bundle"
    label = {"gold": "Gold", "tickets": "Tickets", "premium_tickets": "Random Packs", "boss_monster_tickets": "Extra Deck Themed Packs", "guild_coins": "Guild Coins", "cards": "Support Cards", "equip_spells": "Equip Spells", "packs": "Packs", "boss_themed_pack": "Extra Deck Themed Pack", "monster": "Monster"}.get(normalized, str(reward_type).title())
    return f"{reward_amount} {label}"


def mail_has_unclaimed_reward(mail_row) -> bool:
    try:
        keys = set(mail_row.keys())
    except AttributeError:
        keys = set()
    reward_type = str(mail_row["reward_type"] or "").strip() if "reward_type" in keys else ""
    reward_amount = int(mail_row["reward_amount"] or 0) if "reward_amount" in keys else 0
    bundle = str(mail_row["reward_bundle_json"] or "").strip() if "reward_bundle_json" in keys else ""
    claimed = bool(int(mail_row["reward_claimed"] or 0)) if "reward_claimed" in keys else False
    return bool(((reward_type and reward_amount > 0) or bundle) and not claimed)


def claim_mail_reward(mail_row: sqlite3.Row) -> Tuple[bool, str]:
    reward_type = str(mail_row["reward_type"] or "").strip().lower()
    reward_amount = int(mail_row["reward_amount"] or 0)
    user_id = int(mail_row["user_id"])
    if int(mail_row["reward_claimed"] or 0):
        return False, "This reward has already been claimed."

    def _claim_single(single_type: str, single_amount: int, reward_meta: Optional[Dict[str, object]] = None) -> str:
        reward_meta = dict(reward_meta or {})
        if single_type == "gold":
            db.add_gold(user_id, single_amount)
            return f"{single_amount} gold"
        if single_type == "tickets":
            db.add_pack(user_id, "random_themed", single_amount)
            return f"{single_amount} Random Pack(s)"
        if single_type == "premium_tickets":
            db.add_pack(user_id, "random_themed", single_amount)
            return f"{single_amount} Random Pack(s)"
        if single_type == "boss_monster_tickets":
            db.add_pack(user_id, "boss_themed", single_amount)
            return f"{single_amount} Extra Deck Themed Pack(s)"
        if single_type in {"packs", "pack", "random_themed_pack"}:
            db.add_pack(user_id, "random_themed", single_amount)
            return f"{single_amount} Random Pack(s)"
        if single_type == "boss_themed_pack":
            db.add_pack(user_id, "boss_themed", single_amount)
            return f"{single_amount} Extra Deck Themed Pack(s)"
        if single_type == "guild_coins":
            db.add_guild_coins(user_id, single_amount)
            return f"{single_amount} Guild Coin(s)"
        if single_type == "equip_spells":
            for _ in range(single_amount):
                db.add_equip_spell_to_inventory(user_id, random_equip_spell().equip_spell_id)
            return f"{single_amount} Equip Spell(s)"
        if single_type == "cards":
            rewards: List[SupportCardDef] = []
            duplicate_exp = 0
            owned_support_ids = {support_card_family_key(str(row["support_card_id"])) for row in db.user_support_cards(user_id)}
            for _ in range(single_amount):
                card = random_support_card()
                granted, exp_gained, stage_ups = grant_support_card_reward(user_id, card, owned_support_ids=owned_support_ids)
                duplicate_exp += exp_gained
                if granted:
                    rewards.append(card)
            if rewards:
                counts = Counter(card.name for card in rewards)
                reward_text = ", ".join(f"x{qty} {name}" for name, qty in counts.most_common(3))
                if len(counts) > 3:
                    reward_text += f", +{len(counts) - 3} more"
            else:
                reward_text = "no new support cards"
            dust_text = f" (+{duplicate_exp} Card Dust from duplicates)" if duplicate_exp else ""
            return f"{single_amount} support card roll(s): {reward_text}{dust_text}"
        if single_type == "monster":
            monster_id = str(reward_meta.get('monster_id') or '').strip()
            if not monster_id:
                raise ValueError(single_type)
            monster_row = db.get_monster_by_id(monster_id)
            if monster_row is None:
                return "1 monster reward could not be delivered"
            raw_types = reward_meta.get('monster_types')
            monster_types = decode_monster_types(raw_types) if isinstance(raw_types, str) else tuple()
            if not monster_types:
                monster_types = roll_monster_types_for_row(monster_row)
            result = db.add_monster_to_inventory(user_id, monster_id, monster_types, monster_attribute=str(reward_meta.get('monster_attribute') or monster_row['attribute']))
            type_text = format_monster_types(result.get('monster_types') or monster_types)
            if bool(result.get('is_duplicate')):
                premium_amount = int(result.get('premium_tickets_awarded') or 0)
                premium_text = f" and converted into {premium_amount} Random Pack(s)" if premium_amount > 0 else ""
                resolved_attribute = str(result.get('monster_attribute') or reward_meta.get('monster_attribute') or monster_row['attribute'])
                return f"{monster_row['name']} ({resolved_attribute}) [{type_text}] duplicate{premium_text}"
            resolved_attribute = str(result.get('monster_attribute') or reward_meta.get('monster_attribute') or monster_row['attribute'])
            return f"{monster_row['name']} ({resolved_attribute}) [{type_text}]"
        raise ValueError(single_type)

    try:
        bundle_raw = str(mail_row["reward_bundle_json"] or "").strip()
    except (KeyError, IndexError):
        bundle_raw = ""
    claim_parts: List[str] = []
    if bundle_raw:
        try:
            rewards_bundle = json.loads(bundle_raw)
        except Exception:
            rewards_bundle = []
        for reward in list(rewards_bundle or []):
            single_type = str(reward.get('type') or '').strip().lower()
            single_amount = int(reward.get('amount') or 0)
            if not single_type or single_amount <= 0:
                continue
            claim_parts.append(_claim_single(single_type, single_amount, reward))
        if not claim_parts:
            return False, "This mail does not include a reward."
        db.mark_mail_reward_claimed(int(mail_row["id"]), user_id)
        return True, "Claimed " + "; ".join(f"**{part}**" for part in claim_parts) + "."

    if not reward_type or reward_amount <= 0:
        return False, "This mail does not include a reward."
    try:
        claim_parts.append(_claim_single(reward_type, reward_amount))
    except ValueError:
        return False, "That reward type is not supported."
    db.mark_mail_reward_claimed(int(mail_row["id"]), user_id)
    return True, f"Claimed **{claim_parts[0]}**."
