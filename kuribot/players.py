"""New-player rewards and level experience."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Tuple

from kuribot.config import RNG
from kuribot.timeutil import utc_now
from kuribot.cards.equip_spells import random_equip_spell
from kuribot.database import db
from kuribot.monsters import (
    is_boss_monster_row, random_monster_attribute_for_row, roll_monster_types_for_row,
)


def xp_required_for_level(level: int) -> int:
    normalized = max(1, int(level))
    return 150 + ((normalized - 1) * 30)


def build_start_welcome_mail_body(user_name: str) -> str:
    return (
        f"Welcome to KuriBot, {user_name}!\n\n"
        "Thank you for being part of the DMO community. Attached to this message is 1 Random Pack to get you started.\n\n"
        "Packs contain 3 monsters and 3 support cards. Open them with `;packs`.\n\n"
        "Always keep an eye on your inbox. A notification will appear under your avatar in your profile whenever a new message arrives. Have fun!"
    )


def claim_one_time_account_reward(user_id: int, reward_key: str) -> bool:
    """Atomically mark a once-per-account reward as granted. True only the first time.

    The starter rewards used to be guarded only by "is the welcome mail still in the inbox?",
    so deleting that mail and confirming an avatar again granted them again.
    """
    cur = db.conn.execute(
        "INSERT OR IGNORE INTO user_notice_flags(user_id, notice_key, shown_at) VALUES (?, ?, ?)",
        (int(user_id), str(reward_key), utc_now().isoformat()),
    )
    db.conn.commit()
    return int(cur.rowcount or 0) == 1


def ensure_start_welcome_mail(user_id: int, user_name: str):
    body = build_start_welcome_mail_body(user_name)
    if db.has_mail_reward(user_id, "packs", 1, sender_name="KuriBot", body_substring="Welcome to KuriBot"):
        return
    if not claim_one_time_account_reward(user_id, "starter_welcome_pack_v1"):
        return
    db.create_mail(
        user_id,
        body,
        sender_name="KuriBot",
        reward_type="packs",
        reward_amount=1,
    )


def grant_start_registration_rewards(user_id: int, user_name: str) -> Tuple[List[str], List[str]]:
    """Grant once-per-account starter monsters and equip spells after registration."""
    marker = "Starter account rewards granted"
    if db.has_mail_reward(user_id, "starter_rewards", 0, sender_name="KuriBot", body_substring=marker):
        return [], []
    if not claim_one_time_account_reward(user_id, "starter_rewards_v1"):
        return [], []

    monster_pool = [row for row in db.all_monsters() if not is_boss_monster_row(row)]
    if not monster_pool:
        monster_pool = list(db.all_monsters())
    picked_monsters = RNG.sample(monster_pool, k=min(3, len(monster_pool))) if monster_pool else []
    monster_names: List[str] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for monster_row in picked_monsters:
        monster_types = roll_monster_types_for_row(monster_row)
        result = db.add_monster_to_inventory(
            user_id,
            str(monster_row["monster_id"]),
            monster_types,
            obtained_at=now_iso,
            monster_attribute=random_monster_attribute_for_row(monster_row),
        )
        attr = str(result.get("monster_attribute") or monster_row["attribute"] or "")
        monster_names.append(f"{monster_row['name']} ({attr})")

    equip_names: List[str] = []
    for _ in range(3):
        spell = random_equip_spell()
        db.add_equip_spell_to_inventory(user_id, spell.equip_spell_id, obtained_at=now_iso)
        equip_names.append(spell.name)

    details = "\n".join([*(f"• Monster: {name}" for name in monster_names), *(f"• Equip Spell: {name}" for name in equip_names)])
    db.create_mail(
        user_id,
        f"{marker} for {user_name}.\n\nYour new account received 3 random non-Extra Deck monsters and 3 random equip spells.\n\n{details}",
        sender_name="KuriBot",
        reward_type="starter_rewards",
        reward_amount=0,
    )
    return monster_names, equip_names


