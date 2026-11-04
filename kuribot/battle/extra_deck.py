"""Extra Deck monsters: summoning and their battle effects."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from kuribot.config import BATTLE_HEARTS, SPEED_GAUGE_MAX
from kuribot.models import Fighter, StatusEffect
from kuribot.database import Database
from kuribot.monsters import (
    extra_deck_effect_description, extra_deck_effect_key, is_extra_deck_monster_row,
)
from kuribot.battle.runtime import (
    _clear_runtime_cards, ACTIVE_ENEMIES_BY_FIGHTER, ACTIVE_SUPPORT_CARDS_BY_FIGHTER,
    ACTIVE_TEAMMATES_BY_FIGHTER, gain_flat_stat_bonus, gain_percent_stat_bonus,
    is_extra_deck_fighter, random_alive, runtime_flag, set_runtime_flag,
)
from kuribot.battle.stats import current_attack_power
from kuribot.battle.mechanics import (
    apply_damage_with_runtime, apply_status_effect, buff_count, clear_status, heal_fighter,
    remove_random_buff, trigger_mound_ra_on_heart_loss,
)


def init_extra_deck_schema(database: Database) -> None:
    database.conn.execute("""
        CREATE TABLE IF NOT EXISTS extra_decks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            monsters_json TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 0
        )
    """)
    database.conn.execute("UPDATE extra_decks SET is_active = 0 WHERE is_active != 0")
    team_columns = {row[1] for row in database.conn.execute("PRAGMA table_info(teams)").fetchall()}
    if team_columns and "extra_deck_id" not in team_columns:
        database.conn.execute("ALTER TABLE teams ADD COLUMN extra_deck_id INTEGER")
    database.conn.commit()
    migrate_extra_deck_monsters_out_of_teams(database)
    unequip_extra_deck_monsters(database)


def migrate_extra_deck_monsters_out_of_teams(database: Database) -> int:
    changed = 0
    try:
        team_rows = database.conn.execute("SELECT id, user_id, slots_csv FROM teams").fetchall()
    except Exception:
        return 0
    for team in team_rows:
        original = [part.strip() for part in str(team["slots_csv"] or "").split(",") if part.strip()]
        kept = []
        for instance_id in original:
            row = database.get_inventory_instance(instance_id, int(team["user_id"]))
            if row is not None and is_extra_deck_monster_row(row):
                changed += 1
                continue
            kept.append(instance_id)
        if kept != original:
            database.conn.execute("UPDATE teams SET slots_csv = ? WHERE id = ?", (",".join(kept), int(team["id"])))
    if changed:
        database.conn.commit()
    return changed


def unequip_extra_deck_monsters(database: Database) -> int:
    try:
        rows = database.conn.execute("""
            SELECT mes.monster_instance_id
            FROM monster_equip_spells mes
            JOIN inventory i ON i.instance_id = mes.monster_instance_id
            JOIN monster_defs m ON m.monster_id = i.monster_id
        """).fetchall()
    except Exception:
        return 0
    removed = 0
    for row in rows:
        inv = database.get_inventory_instance(str(row["monster_instance_id"]))
        if inv is not None and is_extra_deck_monster_row(inv):
            database.conn.execute("DELETE FROM monster_equip_spells WHERE monster_instance_id = ?", (str(row["monster_instance_id"]),))
            removed += 1
    if removed:
        database.conn.commit()
    return removed


def extra_deck_pool_for_team(team: Sequence[Fighter]) -> List[Fighter]:
    for fighter in team:
        pool = runtime_flag(fighter, "extra_deck_pool", None)
        if isinstance(pool, list):
            return pool
    return []


def team_defeated_count(team: Sequence[Fighter]) -> int:
    return sum(1 for fighter in team if fighter.hp <= 0)


def team_total_damage_taken(team: Sequence[Fighter]) -> int:
    return sum(int(runtime_flag(fighter, "battle_damage_taken", 0) or 0) for fighter in team)


def extra_deck_summon_ready(candidate: Fighter, team: Sequence[Fighter], enemies: Sequence[Fighter]) -> bool:
    effect = extra_deck_effect_key(candidate.name)
    defeated = team_defeated_count(team)
    enemy_defeated = team_defeated_count(enemies)
    total_taken = team_total_damage_taken(team)
    if defeated > 0:
        return True
    if effect in {"utopia", "stardust", "firewall"} and any(ally.alive and ally.hp <= ally.max_hp * 0.30 for ally in team):
        return True
    if effect in {"black_rose", "topologic"} and sum(1 for f in [*team, *enemies] if f.alive) >= 5:
        return True
    if effect in {"numeron", "holactie", "armityle"} and defeated + enemy_defeated >= 4:
        return True
    if effect in {"wicked_avatar", "dark_rebellion", "ragnazero"} and any(buff_count(enemy) > 0 or current_attack_power(enemy) > max(current_attack_power(ally) for ally in team if ally.alive) for enemy in enemies if enemy.alive):
        return True
    if total_taken >= max(1, sum(f.max_hp for f in team) // 2):
        return True
    return False


def extra_deck_choose_slot(team: List[Fighter], candidate: Fighter) -> Optional[int]:
    for idx, fighter in enumerate(team):
        if fighter.hp <= 0:
            return idx
    effect = extra_deck_effect_key(candidate.name)
    if effect in {"black_rose", "topologic", "holactie", "ra", "obelisk", "slifer", "wicked_avatar", "wicked_dreadroot"}:
        living = [(idx, f) for idx, f in enumerate(team) if f.alive]
        if living:
            return min(living, key=lambda item: (item[1].hp / max(1, item[1].max_hp), item[1].hp))[0]
    return None


def apply_extra_deck_summon_effect(fighter: Fighter, allies: List[Fighter], enemies: List[Fighter], log_entries: List[str]) -> None:
    effect = extra_deck_effect_key(fighter.name)
    fighter.speed_gauge = min(SPEED_GAUGE_MAX, max(fighter.speed_gauge, 35.0))
    log_entries.append(f"**{fighter.name}** enters from the **Extra Deck**. {extra_deck_effect_description(fighter.name)}")
    if effect == "dark_magician":
        target = random_alive(enemies)
        if target:
            apply_status_effect(target, StatusEffect.SILENCE, source=fighter, log_entries=log_entries)
    elif effect in {"blue_eyes", "armed_dragon", "topologic", "obelisk", "black_rose", "zarc"}:
        pct = 0.20 if effect != "black_rose" else 0.15
        for enemy in enemies:
            if enemy.alive:
                dealt = apply_damage_with_runtime(enemy, max(1, int(round(fighter.atk * pct))), fighter, log_entries, "Extra Deck summon")
                if dealt:
                    log_entries.append(f"**{fighter.name}** hits **{enemy.name}** for **{dealt}** summon damage.")
        if effect == "black_rose":
            for ally in allies:
                if ally is not fighter and ally.alive:
                    apply_damage_with_runtime(ally, max(1, int(round(fighter.atk * 0.10))), fighter, log_entries, "Black Rose Gale")
    elif effect == "red_eyes":
        gain_percent_stat_bonus(fighter, "ATK", 25 * max(1, team_defeated_count(allies)))
    elif effect == "exodia":
        for enemy in enemies:
            if enemy.alive:
                dealt = apply_damage_with_runtime(enemy, max(1, int(round(enemy.max_hp * 0.65))), fighter, log_entries, "Obliterate")
                log_entries.append(f"**Obliterate** deals **{dealt}** damage to **{enemy.name}**.")
    elif effect == "ancient_fairy":
        for ally in allies:
            if ally.alive:
                heal_fighter(ally, max(1, int(round(ally.max_hp * 0.20))), allies, enemies, log_entries, "Sacred Garden", source=fighter)
                set_runtime_flag(ally, "protection_turns", max(int(runtime_flag(ally, "protection_turns", 0)), 1))
    elif effect in {"dark_rebellion", "relinquished", "wicked_avatar"}:
        target = max((enemy for enemy in enemies if enemy.alive), key=lambda e: current_attack_power(e), default=None)
        if target:
            steal = max(1, int(round(current_attack_power(target) * (0.30 if effect != "wicked_avatar" else 0.10))))
            gain_flat_stat_bonus(fighter, "ATK", steal)
            if effect != "wicked_avatar":
                gain_percent_stat_bonus(target, "ATK", -25)
            log_entries.append(f"**{fighter.name}** drains power from **{target.name}** and gains **{steal} ATK**.")
    elif effect in {"borreload", "ragnazero", "plasma"}:
        target = max((enemy for enemy in enemies if enemy.alive), key=lambda e: buff_count(e), default=None)
        if target:
            remove_random_buff(target, log_entries, fighter.name)
            gain_percent_stat_bonus(target, "ATK", -25)
            gain_percent_stat_bonus(target, "SPD", -25)
    elif effect in {"dark_necrofear", "topologic"}:
        target = random_alive(enemies)
        if target:
            apply_status_effect(target, StatusEffect.FEAR if effect == "topologic" else StatusEffect.CHARM, source=fighter, log_entries=log_entries)
    elif effect == "arrival":
        set_runtime_flag(fighter, "immunity_turns", max(int(runtime_flag(fighter, "immunity_turns", 0)), 2))
    elif effect == "dyson":
        set_runtime_flag(fighter, "protection_turns", max(int(runtime_flag(fighter, "protection_turns", 0)), 3))
        set_runtime_flag(fighter, "reflect_turns", max(int(runtime_flag(fighter, "reflect_turns", 0)), 3))
    elif effect == "doom_armageddon":
        fallen = next((ally for ally in allies if ally is not fighter and ally.hp <= 0), None)
        if fallen is not None:
            fallen.hp = 1
            log_entries.append(f"**Contract with Doom** revives **{fallen.name}** at **1 HP**.")
    elif effect in {"ra", "gustav"}:
        target = random_alive(enemies)
        if target:
            cost = max(1, int(round(fighter.hp * 0.25))) if effect == "ra" else 0
            fighter.hp = max(1, fighter.hp - cost)
            dealt = apply_damage_with_runtime(target, max(1, int(round(fighter.atk * (1.5 if effect == "ra" else 1.0)))), fighter, log_entries, "Extra Deck cannon")
            log_entries.append(f"**{fighter.name}** fires for **{dealt}** damage." + (f" It pays **{cost} HP**." if cost else ""))
    elif effect in {"jinzo"}:
        set_runtime_flag(fighter, "disable_enemy_support", True)
        log_entries.append("**Jinzo** shuts down enemy support effects while it remains active.")
    elif effect in {"power_tool", "decode", "rainbow_dragon"}:
        bonus = 20 + (10 * sum(1 for ally in allies if ally.alive and ally is not fighter))
        gain_percent_stat_bonus(fighter, "ATK", bonus)
        gain_percent_stat_bonus(fighter, "DEF", bonus // 2)
        if effect == "rainbow_dragon":
            clear_status(fighter, log_entries, reason="Rainbow Overdrive")
    elif effect in {"utopia", "firewall", "stardust", "galaxy_eyes"}:
        set_runtime_flag(fighter, "extra_deck_guard_used", False)
        set_runtime_flag(fighter, "dodge_next_attack", True)
    elif effect in {"wicked_dreadroot"}:
        for enemy in enemies:
            if enemy.alive:
                gain_percent_stat_bonus(enemy, "ATK", -25)
                gain_percent_stat_bonus(enemy, "SPD", -25)
    elif effect in {"wicked_eraser"}:
        set_runtime_flag(fighter, "erase_on_defeat", True)
    elif effect in {"holactie"}:
        for ally in allies:
            if ally.hp <= 0:
                ally.hp = max(1, int(round(ally.max_hp * 0.50)))
            elif ally.alive:
                heal_fighter(ally, max(1, int(round(ally.max_hp * 0.25))), allies, enemies, log_entries, "Creator's Light", source=fighter)


def maybe_summon_extra_deck_monster(team: Sequence[Fighter], enemies: Sequence[Fighter], side_name: str, log_entries: List[str]) -> Optional[Tuple[Fighter, Fighter, bool]]:
    if not isinstance(team, list):
        return None
    if any(is_extra_deck_fighter(f) and f.alive for f in team):
        return None
    if any(bool(runtime_flag(enemy, "extra_deck_lockout", False)) for enemy in enemies if enemy.alive):
        return None
    pool = extra_deck_pool_for_team(team)
    if not pool:
        return None
    for candidate in list(pool):
        if not extra_deck_summon_ready(candidate, team, enemies):
            continue
        slot = extra_deck_choose_slot(team, candidate)
        if slot is None:
            continue
        pool.remove(candidate)
        old = team[slot]
        replaced_defeated_fighter = old.hp <= 0
        candidate.runtime["extra_deck_pool"] = pool
        candidate.runtime["extra_deck_team_ref"] = team
        candidate.runtime["extra_deck_replaced_name"] = old.name
        candidate.runtime["battle_damage_dealt"] = 0
        candidate.runtime["battle_healing_done"] = 0
        candidate.runtime["battle_damage_taken"] = 0
        candidate.runtime["battle_kills"] = 0
        team[slot] = candidate
        ACTIVE_SUPPORT_CARDS_BY_FIGHTER[id(candidate)] = []
        ACTIVE_TEAMMATES_BY_FIGHTER[id(candidate)] = team
        ACTIVE_ENEMIES_BY_FIGHTER[id(candidate)] = ACTIVE_ENEMIES_BY_FIGHTER.get(id(old)) or list(enemies)
        for teammate in team:
            if teammate is not candidate and id(teammate) in ACTIVE_TEAMMATES_BY_FIGHTER:
                ACTIVE_TEAMMATES_BY_FIGHTER[id(teammate)] = list(team)
        for enemy_fighter in list(ACTIVE_ENEMIES_BY_FIGHTER.get(id(candidate)) or []):
            ACTIVE_ENEMIES_BY_FIGHTER[id(enemy_fighter)] = team
        _clear_runtime_cards([old])
        old.hp = 0
        log_entries.append(f"**{side_name}** opens the **Extra Deck Zone** and summons **{candidate.name}** over **{old.name}**.")
        apply_extra_deck_summon_effect(candidate, team, list(enemies), log_entries)
        return old, candidate, replaced_defeated_fighter
    return None


def _apply_missing_extra_deck_knockout_heart_loss(
    *,
    summon_result: Optional[Tuple[Fighter, Fighter, bool]],
    unresolved_dead_ids_before: set[int],
    hearts_before: int,
    hearts_after: int,
    heart_loss_prevented_before: bool,
    owner_name: str,
    owner_team: Sequence[Fighter],
    opposing_team: Sequence[Fighter],
    log_entries: List[str],
    total_hearts: Optional[int],
) -> int:
    if summon_result is None:
        return hearts_after
    replaced, _summoned, replaced_defeated_fighter = summon_result
    if not replaced_defeated_fighter:
        return hearts_after
    if id(replaced) not in unresolved_dead_ids_before:
        return hearts_after
    if hearts_after != hearts_before:
        return hearts_after
    if heart_loss_prevented_before:
        return hearts_after
    maximum = max(1, int(total_hearts if total_hearts is not None else BATTLE_HEARTS))
    updated_hearts = max(0, hearts_after - 1)
    log_entries.append(
        f"**{replaced.name}** was defeated before the Extra Deck summon resolved. "
        f"{owner_name} loses **1 heart** ({updated_hearts}/{maximum} left)."
    )
    trigger_mound_ra_on_heart_loss(owner_team, owner_name, log_entries)
    trigger_mound_ra_on_heart_loss(opposing_team, owner_name, log_entries)
    return updated_hearts
