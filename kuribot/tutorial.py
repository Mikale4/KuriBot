"""Tutorial content, progress and starter monster pools."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import List, Sequence, Tuple

import discord

from kuribot.config import MONSTER_TYPES, RNG, TEAM_SIZE
from kuribot.models import SupportCardDef
from kuribot.database import db
from kuribot.monsters import (
    display_monster_label_from_id, monster_category_for_row, random_monster_attribute_for_row,
)
from kuribot.cards.support_cards import (
    random_special_support_card, random_support_card, support_card_compact_label,
    support_card_exp_to_next_stage, support_card_max_stage, support_card_stage_number,
    support_card_stage_one_defs,
)
from kuribot.dueling_grounds import choose_random_dueling_grounds_duelist
from kuribot.render.collection import render_support_card_page


def build_tutorial_welcome_embed(user_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="Welcome to the KuriBot Tutorial!",
        description=(
            f"Welcome, **{user_name}**! Kuribot will walk you through the basics one step at a time.\n\n"
            "Start with **Pack Opening** to learn how banners and Pack Openings work. "
            "Open **Monster Generation** to learn how attributes, monster type(s), stat bonuses, and base stats work. "
            "Then open **Team Creation** to learn how the Binder hub and Teams page work before battling. "
            "Open **Support Cards** to learn how battle cards work and why Special Support Cards can swing a duel. "
            "The **Battle** lesson is also available from this menu."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Complete every section here to unlock the rest of KuriBot's commands.")
    return embed


def build_tutorial_summon_monsters_embed(user_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="Tutorial • Pack Opening",
        description=(
            "Packs are where you get new monsters and support cards. Each Random Pack opens into **3 monsters** and **3 support cards**.\n\n"
            "This lesson gives you one free tutorial Random Pack and opens it right away, so you can see how pack rewards work.\n\n"
            "Press **Open Pack** to open your free tutorial pack."
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"{user_name} • Pack Opening Tutorial")
    return embed


def build_tutorial_premium_complete_embed(user_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="Tutorial • Pack Opening Complete!",
        description=(
            f"Great job, **{user_name}**! You learned how pack opening works and finished the **Pack Opening** section of the tutorial.\n\n"
            "Next, open **Team Creation** from the tutorial menu. Kuribot will show you the Binder hub and help you build a team before battle."
        ),
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"{user_name} • Team Creation is available now")
    return embed


def build_tutorial_team_creation_intro_embed(user_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="Tutorial • Team Creation",
        description=(
            f"**{user_name}**, welcome to the **Binder hub**!\n\n"
            "Before you can battle, you must build a **team** first. A team is a lineup of three monsters that Kuribot can send into duels.\n\n"
            "The Binder hub has sections for monsters, support cards, decks, and teams. For this lesson, Kuribot will open a **tutorial example hub**. "
            "It already contains one example team so you can see what a completed lineup looks like.\n\n"
            "At the bottom of this message, press **Teams** to open the tutorial Teams page and create your own practice team."
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text="Tutorial example hub • Your real Binder is unchanged until you save your team")
    return embed


def build_tutorial_team_complete_embed(user_name: str, team_name: str, rewarded: bool) -> discord.Embed:
    reward_text = "Kuribot rewarded you with **10 packs**!" if rewarded else "You already claimed the **10 pack** Team Creation reward for this account."
    embed = discord.Embed(
        title="Tutorial • Team Created!",
        description=(
            f"Congratulations, **{user_name}**! You created **{team_name}** and learned how to build a battle team.\n\n"
            f"{reward_text}\n\n"
            "Your team is saved and set as your active team. The Battle tutorial is available from the tutorial menu."
        ),
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"{user_name} • Team Creation reward is once per account")
    return embed


def build_tutorial_monster_generation_intro_embed(user_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="Tutorial • Monster Generation",
        description=(
            f"Welcome to the **Monster Generation** section, **{user_name}**!\n\n"
            "When you summon a monster, Kuribot does more than add that monster name to your collection. "
            "Each opened copy is also given a random **Attribute** and random **Monster Type(s)**.\n\n"
            "The Attribute and Type(s) help shape what that copy is good at. A monster might become stronger, faster, tougher, or gain different matchups depending on what it rolls.\n\n"
            "Use **Stat Bonuses** to review the same Stat Bonuses glossary section, or press **Continue** to learn about base stats."
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"{user_name} • Monster Generation Tutorial")
    return embed


def build_tutorial_monster_generation_base_stats_embed(user_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="Tutorial • Monster Base Stats",
        description=(
            "Every monster in the game has its own **base stats**: **HP**, **ATK**, **DEF**, and **SPD**.\n\n"
            "These base stats are the starting point for that monster before its generated Attribute and Monster Type(s) apply their bonuses. "
            "That means a monster's natural strengths still matter, even when two copies roll different generation results.\n\n"
            "You can look up any monster's base stats in the **Glossary** by opening `;glossary`, choosing **Monster Base Stats**, and searching for the monster.\n\n"
            "Press **Finish** to complete this tutorial section."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"{user_name} • Monster Base Stats")
    return embed


def build_tutorial_monster_generation_complete_embed(user_name: str, rewarded: bool) -> discord.Embed:
    reward_text = "Kuribot rewarded you with **3 Random Packs**!" if rewarded else "You already claimed the **3 Random Pack** Monster Generation reward for this account."
    embed = discord.Embed(
        title="Tutorial • Monster Generation Complete!",
        description=(
            f"Congratulations, **{user_name}**! You finished the **Monster Generation** section of the tutorial.\n\n"
            "You now know that opened monsters roll random Attributes and Monster Type(s), and that each monster also has its own base stats.\n\n"
            f"{reward_text}"
        ),
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"{user_name} • Monster Generation reward is once per account")
    return embed


def build_tutorial_support_cards_intro_embed(user_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="Tutorial • Support Cards",
        description=(
            f"Welcome to the **Support Cards** section, **{user_name}**!\n\n"
            "Support Cards are battle cards you collect and place into a **Support Deck**. During battle, you draw **3 cards** from your active Support Deck. Those cards last for **2 rounds**, then get discarded before you draw **3 more cards**. They can strengthen your monsters or trigger special effects.\n\n"
            "Most Support Cards boost a stat like **ATK**, **DEF**, or **SPD** for monsters that match the card's Attribute or Monster Type. A good Support Deck can make your team hit harder, survive longer, or take turns faster.\n\n"
            "Press **Show** and Kuribot will reveal 3 random Support Cards. One of them is guaranteed to be a random **Special Support Card**."
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text=f"{user_name} • Support Cards Tutorial")
    return embed


def is_special_support_card_def(card: SupportCardDef) -> bool:
    return str(card.attribute or "").upper() == "SPECIAL" or str(card.stat_type or "").upper() == "SPECIAL"


def make_tutorial_support_card_showcase_cards() -> List[SupportCardDef]:
    special_card = random_special_support_card()
    non_special_cards = [card for card in support_card_stage_one_defs() if not is_special_support_card_def(card)]
    if len(non_special_cards) >= 2:
        cards = [special_card] + RNG.sample(non_special_cards, 2)
    else:
        cards = [special_card, random_support_card(), random_support_card()]
    RNG.shuffle(cards)
    return cards


def tutorial_support_card_rows(cards: Sequence[SupportCardDef]) -> List[dict]:
    rows: List[dict] = []
    now_label = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for idx, card in enumerate(cards):
        rows.append({
            "instance_id": f"tutorial-support-{idx}",
            "support_card_id": card.support_card_id,
            "exp": 0,
            "exp_to_next": support_card_exp_to_next_stage(card.support_card_id),
            "stage": support_card_stage_number(card.support_card_id),
            "max_stage": support_card_max_stage(card.support_card_id),
            "obtained_at": "",
            "obtained_label": now_label,
            "copy_no": 1,
            "name": card.name,
            "attribute": card.attribute,
            "bonus_pct": card.bonus_pct,
            "stat_type": card.stat_type,
            "card": card,
        })
    return rows


def make_tutorial_support_cards_file(user_name: str, owner_id: int, cards: Sequence[SupportCardDef]) -> discord.File:
    rows = tutorial_support_card_rows(cards)
    buffer = render_support_card_page(user_name, rows, 1, per_page=6, filter_label="Tutorial Reveal", sort_label="3 Random Cards", theme_name=db.get_ui_theme(owner_id), show_obtained=False)
    return discord.File(buffer, filename="tutorial_support_cards.png")


def build_tutorial_support_cards_show_embed(user_name: str, cards: Sequence[SupportCardDef]) -> discord.Embed:
    special_cards = [card for card in cards if is_special_support_card_def(card)]
    special_name = special_cards[0].name if special_cards else "a Special Support Card"
    embed = discord.Embed(
        title="Tutorial • Support Cards Revealed!",
        description=(
            f"Kuribot revealed 3 random Support Cards for **{user_name}**.\n\n"
            f"Kuribot notices something important: **{special_name}** is a **Special Support Card**!\n\n"
            "Special Support Cards are different from normal stat-boost cards. Instead of only giving a simple stat bonus, they can create powerful battle-changing effects, disrupt the opponent, protect your team, or open a comeback window.\n\n"
            "These are the key cards that can change the tides of a battle. Building your Support Deck around smart Special Support Card choices can be the difference between a close loss and a dramatic win.\n\n"
            "Press **Finish** to complete this tutorial section and claim your one-time reward."
        ),
        color=discord.Color.gold(),
    )
    for card in cards:
        prefix = "Special" if is_special_support_card_def(card) else support_card_compact_label(card)
        embed.add_field(name=card.name, value=f"{prefix}\n{card.description}", inline=False)
    embed.set_footer(text=f"{user_name} • Support Cards reward is once per account")
    return embed


def build_tutorial_support_cards_complete_embed(user_name: str, rewarded: bool) -> discord.Embed:
    reward_text = "Kuribot rewarded you with **30 random Support Cards**!" if rewarded else "You already claimed the **30 random Support Cards** reward for this account."
    embed = discord.Embed(
        title="Tutorial • Support Cards Complete!",
        description=(
            f"Congratulations, **{user_name}**! You finished the **Support Cards** section of the tutorial.\n\n"
            "You now know that Support Cards power up your team during battle, and that Special Support Cards are key cards capable of changing the flow of a duel.\n\n"
            f"{reward_text}"
        ),
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"{user_name} • Support Cards reward is once per account")
    return embed


def tutorial_support_cards_progress_flags(user_id: int) -> Tuple[bool, bool]:
    progress = db.get_tutorial_progress(user_id)
    return bool(progress["support_cards_done_at"]), bool(progress["support_cards_reward_claimed_at"])


def tutorial_monster_generation_progress_flags(user_id: int) -> Tuple[bool, bool]:
    progress = db.get_tutorial_progress(user_id)
    return bool(progress["monster_generation_done_at"]), bool(progress["monster_generation_reward_claimed_at"])


def tutorial_progress_flags(user_id: int) -> Tuple[bool, bool, bool]:
    progress = db.get_tutorial_progress(user_id)
    return bool(progress["summon_done_at"]), bool(progress["premium_done_at"]), bool(progress["reward_claimed_at"])


TUTORIAL_REQUIRED_SECTIONS: Tuple[Tuple[str, str], ...] = (
    ("summon_done_at", "Pack Opening: open a pack"),
    ("premium_done_at", "Pack Opening: finish lesson"),
    ("monster_generation_done_at", "Monster Generation"),
    ("team_done_at", "Team Creation"),
    ("support_cards_done_at", "Support Cards"),
    ("battle_done_at", "Battle"),
    ("hearts_done_at", "Hearts"),
)


def tutorial_completion_status(user_id: int) -> Tuple[bool, List[str]]:
    progress = db.get_tutorial_progress(user_id)
    missing = [label for column, label in TUTORIAL_REQUIRED_SECTIONS if not bool(progress[column])]
    return len(missing) == 0, missing


def build_tutorial_battle_complete_embed(user_name: str, result_text: str) -> discord.Embed:
    embed = discord.Embed(
        title="Tutorial • Battle Complete!",
        description=(
            f"{result_text}\n\n"
            f"Great watching, **{user_name}**! Battles are turn-based duels between two teams of monsters. "
            "Each monster has **HP**, **ATK**, **DEF**, and **SPD**:\n\n"
            "• **HP** is health. When a monster reaches 0 HP, it is knocked out.\n"
            "• **ATK** controls how hard a monster hits.\n"
            "• **DEF** reduces incoming damage.\n"
            "• **SPD** fills the turn gauge faster, so faster monsters act more often.\n\n"
            "Monsters target enemies, attack, can miss or crit, and type matchups can make attacks stronger or weaker. "
            "The battle ends when one side runs out of living monsters or loses all Hearts.\n\n"
            "Press **Hearts** for one more tutorial battle where both duelists only have **2 Hearts**."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text=f"{user_name} • Battle Tutorial")
    return embed


def build_tutorial_hearts_complete_embed(user_name: str, result_text: str, rewarded: bool) -> discord.Embed:
    reward_text = "Kuribot rewarded you with **10 packs** for finishing the Battle tutorial!" if rewarded else "You already claimed the **10 pack** Battle tutorial reward for this account."
    embed = discord.Embed(
        title="Tutorial • Hearts Complete!",
        description=(
            f"{result_text}\n\n"
            "There is more than one way to win or lose a battle. A side can lose because all of its monsters are knocked out, "
            "or because its **Hearts** are depleted.\n\n"
            "**Hearts** are the team's extra battle lives. When a monster is knocked out, that side loses Hearts. "
            "If Hearts reach 0, that side loses even if some battle effects might otherwise continue.\n\n"
            "Hearts also matter when a player runs out of cards to draw from their Support Deck. At round checkpoints, "
            "an empty deck can pressure that player by costing Hearts, so deck size and card usage can become part of survival.\n\n"
            f"{reward_text}"
        ),
        color=discord.Color.gold(),
    )
    embed.set_footer(text=f"{user_name} • Battle tutorial reward is once per account")
    return embed


def choose_two_tutorial_duelists() -> Tuple[str, dict, str, dict]:
    first_key, first_duelist = choose_random_dueling_grounds_duelist()
    second_key, second_duelist = choose_random_dueling_grounds_duelist()
    attempts = 0
    while second_key == first_key and attempts < 20:
        second_key, second_duelist = choose_random_dueling_grounds_duelist()
        attempts += 1
    return str(first_key), dict(first_duelist), str(second_key), dict(second_duelist)


def _ensure_tutorial_pool_column() -> None:
    try:
        cols = {str(row["name"]) for row in db.conn.execute("PRAGMA table_info(tutorial_progress)").fetchall()}
        if "tutorial_pool_json" not in cols:
            db.conn.execute("ALTER TABLE tutorial_progress ADD COLUMN tutorial_pool_json TEXT NOT NULL DEFAULT ''")
            db.conn.commit()
    except sqlite3.Error:
        pass


def _stored_tutorial_pool_rows(user_id: int) -> List[sqlite3.Row]:
    try:
        row = db.conn.execute("SELECT tutorial_pool_json FROM tutorial_progress WHERE user_id = ?", (user_id,)).fetchone()
        instance_ids = json.loads(str(row["tutorial_pool_json"] or "[]")) if row else []
    except (sqlite3.Error, ValueError, TypeError):
        return []
    rows = []
    for instance_id in instance_ids or []:
        inventory_row = db.get_inventory_instance(str(instance_id), user_id)
        if inventory_row is not None:
            rows.append(inventory_row)
    return rows


def make_tutorial_monster_pool(user_id: int, count: int = 10) -> List[sqlite3.Row]:
    # The tutorial's sample monsters are granted ONCE per account. Previously every click on
    # "Team Creation" added another 10 permanent monsters, so the button could be farmed.
    _ensure_tutorial_pool_column()
    existing_rows = _stored_tutorial_pool_rows(user_id)
    if len(existing_rows) >= TEAM_SIZE:
        return existing_rows
    rows = _generate_tutorial_monster_pool(user_id, count)
    if rows:
        try:
            db.conn.execute("INSERT OR IGNORE INTO tutorial_progress(user_id) VALUES (?)", (user_id,))
            db.conn.execute(
                "UPDATE tutorial_progress SET tutorial_pool_json = ? WHERE user_id = ?",
                (json.dumps([str(row["instance_id"]) for row in rows]), user_id),
            )
            db.conn.commit()
        except sqlite3.Error:
            pass
    return rows


def _generate_tutorial_monster_pool(user_id: int, count: int = 10) -> List[sqlite3.Row]:
    pool = [row for row in db.all_monsters() if monster_category_for_row(row) != "boss"]
    if not pool:
        return []
    rows: List[sqlite3.Row] = []
    seen_instance_ids: set[str] = set()
    attempts = 0
    while len(rows) < count and attempts < count * 20:
        attempts += 1
        monster = RNG.choice(pool)
        first_type = RNG.choice(MONSTER_TYPES)
        second_type = RNG.choice([monster_type for monster_type in MONSTER_TYPES if monster_type != first_type] or [first_type])
        monster_types = tuple(sorted((first_type, second_type), key=MONSTER_TYPES.index))
        attribute_name = random_monster_attribute_for_row(monster)
        result = db.add_monster_to_inventory(user_id, str(monster["monster_id"]), monster_types, monster_attribute=attribute_name)
        instance_id = result.get("instance_id")
        if not instance_id or str(instance_id) in seen_instance_ids:
            continue
        row = db.get_inventory_instance(str(instance_id), user_id)
        if row is None:
            continue
        seen_instance_ids.add(str(instance_id))
        rows.append(row)
    return rows


def tutorial_example_team_rows(sample_rows: Sequence[sqlite3.Row]) -> List[dict]:
    member_ids = [str(row["instance_id"]) for row in sample_rows[:TEAM_SIZE]]
    preview_names = [display_monster_label_from_id(instance_id) for instance_id in member_ids]
    return [{
        "team_id": 0,
        "instance_id": "tutorial-example",
        "name": "Example Team",
        "member_ids": member_ids,
        "member_count": len(member_ids),
        "preview_names": preview_names,
        "is_active": True,
    }]
