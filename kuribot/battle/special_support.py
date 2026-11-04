"""Special support card matching and passive effects."""

from __future__ import annotations

from typing import List, Optional, Sequence

from kuribot.config import RNG
from kuribot.models import Fighter, SupportCardDef, SupportDeckRuntime
from kuribot.cards.support_cards import get_support_card_def, SUPPORT_CARD_DEFS
from kuribot.battle.archetypes import (
    fighter_is_albaz, fighter_is_ancient_gear, fighter_is_archfiend, fighter_is_blackwing,
    fighter_is_blue_eyes, fighter_is_charmer_familiar_channeler, fighter_is_clear_wing,
    fighter_is_clown_clan, fighter_is_crystal_beast, fighter_is_crystron, fighter_is_cyber_dragon,
    fighter_is_dark_magician, fighter_is_dark_world, fighter_is_destiny_hero, fighter_is_diabell,
    fighter_is_dinowrestler, fighter_is_dracotail, fighter_is_elemental_hero, fighter_is_fire_king,
    fighter_is_forbidden_one, fighter_is_fossil, fighter_is_frightfur_or_fluffal,
    fighter_is_galaxy_or_photon, fighter_is_gem_knight, fighter_is_gimmick_puppet,
    fighter_is_gravekeeper, fighter_is_hamon, fighter_is_infernoid, fighter_is_invoked,
    fighter_is_kashtira, fighter_is_kewl_tune, fighter_is_labrynth, fighter_is_lunalight,
    fighter_is_magician_girl, fighter_is_magnet_warrior, fighter_is_masked_hero,
    fighter_is_metalfoes, fighter_is_mimighoul, fighter_is_mitsurugi, fighter_is_myutant,
    fighter_is_neos, fighter_is_obelisk_the_tormentor, fighter_is_ojama, fighter_is_orcust,
    fighter_is_penguin, fighter_is_predaplant, fighter_is_primite, fighter_is_raviel,
    fighter_is_red_eyes, fighter_is_relinquished, fighter_is_ryzeal, fighter_is_shaddoll,
    fighter_is_silent_swordsman, fighter_is_skull_servant_or_wight,
    fighter_is_slifer_the_sky_dragon, fighter_is_speedroid, fighter_is_tearlaments,
    fighter_is_the_winged_dragon_of_ra, fighter_is_thunder_dragon, fighter_is_toon,
    fighter_is_trickstar, fighter_is_true_king_or_true_draco,
    fighter_is_uria_lord_of_searing_flames, fighter_is_utopia_or_utopic, fighter_is_vanquish_soul,
    fighter_is_visas_starfrost, fighter_is_vision_hero, fighter_is_volcanic,
    fighter_is_white_forest, fighter_is_yummy,
)
from kuribot.battle.runtime import (
    ACTIVE_SUPPORT_CARDS_BY_FIGHTER, ACTIVE_TEAMMATES_BY_FIGHTER, gain_flat_stat_bonus,
    is_extra_deck_fighter,
)


SPECIAL_SUPPORT_IDS = [
    "CYBER_REVSYSTEM", "DARK_BURNING_ATTACK", "BLUE_EYES_SPIRIT_CALL", "RED_EYES_INFERNO_CHAIN",
    "CYBER_EMERGENCY", "HERO_SIGNAL", "ANCIENT_GEAR_FORTRESS", "BLACKWING_WHIRLWIND",
    "GALAXY_EXPEDITION", "DARK_MAGIC_ATTACK", "NEUTRON_BLAST", "INFERNO_FIRE_BLAST", "WRATH_OF_NEOS",
    "TEARLAMENTS_SULLIEK", "CLOCK_TOWER_PRISON", "CRYSTRON_INCLUSION", "DARK_WORLD_DEALINGS",
    "MAGNET_CONVERSION", "LABRYNTH_BARRAGE", "ORCUSTRATED_BABEL", "PUPPET_PARADE",
    "KASHTIRA_BIG_BANG", "BLACK_SONIC", "OJAMA_COUNTRY", "STAKE_YOUR_SOUL", "SILENT_SWORD_SLASH",
    "CLOWN_CREW_REHEARSAL", "MIMIGHOUL_CHARM", "YUMMY_SURPRISE", "MASK_CHANGE", "DRAGONIC_DIAGRAM",
    "BRANDED_FUSION", "OVERLOAD_FUSION", "FUTURE_FUSION", "RED_EYES_FUSION", "GEM_KNIGHT_FUSION",
    "FRIGHTFUR_FUSION", "NEOS_FUSION", "POWER_BOND", "MYUTANT_FUSION", "SHADDOLL_FUSION",
    "FUSION_DESTINY", "VISION_FUSION", "FOSSIL_FUSION", "ANCIENT_GEAR_FUSION", "METALFOES_FUSION",
    "INVOCATION", "LUNALIGHT_FUSION", "TRICKSTAR_FUSION", "THUNDER_DRAGON_FUSION", "PRIMITE_FUSION",
    "PREDAPRIME_FUSION", "TYRANT_DINO_FUSION", "RYZEAL_CROSS", "RYZEAL_PLUGIN", "PRIMEVAL_PLANET_PERLEREINO",
    "PRESSURED_PLANET_WRAITSOTH", "VANQUISH_SOUL_CALAMITY_CAESAR", "FILIA_DIABELL", "NECROVALLEY", "MILLENNIUM_ANKH",
    "WIGHT_REANIMATOR", "PANDEMONIUM", "ROYAL_PENGUINS_GARDEN", "BLAZE_ACCELERATOR", "GRAND_SPIRITUAL_ART_ICHIRIN",
    "TRICKSTAR_REINCARNATION", "LIGHT_WING_SHIELD", "CERULEAN_SKYFIRE", "FALLEN_PARADISE", "HYPER_BLAZE",
    "BEWARE_THE_WHITE_FOREST", "TALES_OF_THE_WHITE_FOREST", "YUM_YUM_YUMMYS", "ULTIMATE_CRYSTAL_MAGIC", "TOON_BRIEFCASE",
    "MOUND_OF_THE_BOUND_CREATOR", "JJ_KEWL_TUNE", "MITSURUGI_GREAT_PURIFICATION", "DRACOTAIL_FLAME",
    "SOUL_SERVANT", "BURST_STREAM_OF_DESTRUCTION", "ETERNAL_SOUL", "RELINQUISHED_FUSION",
    "SPEEDROID_CLEAR_WING_WONDER", "FIRE_KING_ISLAND", "VOID_PURIFICATION",
]


def _fighter_matches_special_support_base(support_card_id: str, fighter: Fighter) -> bool:
    support_card_id = str(support_card_id or "").upper()
    if support_card_id == "CYBER_REVSYSTEM":
        return fighter_is_cyber_dragon(fighter)
    if support_card_id == "DARK_BURNING_ATTACK":
        return fighter_is_magician_girl(fighter)
    if support_card_id == "BLUE_EYES_SPIRIT_CALL":
        return fighter_is_blue_eyes(fighter)
    if support_card_id == "RED_EYES_INFERNO_CHAIN":
        return fighter_is_red_eyes(fighter)
    if support_card_id == "CYBER_EMERGENCY":
        return fighter_is_cyber_dragon(fighter)
    if support_card_id == "HERO_SIGNAL":
        return fighter_is_elemental_hero(fighter)
    if support_card_id == "ANCIENT_GEAR_FORTRESS":
        return fighter_is_ancient_gear(fighter)
    if support_card_id == "BLACKWING_WHIRLWIND":
        return fighter_is_blackwing(fighter)
    if support_card_id == "GALAXY_EXPEDITION":
        return fighter_is_galaxy_or_photon(fighter)
    if support_card_id == "DARK_MAGIC_ATTACK":
        return fighter_is_dark_magician(fighter)
    if support_card_id == "NEUTRON_BLAST":
        return fighter_is_blue_eyes(fighter)
    if support_card_id == "INFERNO_FIRE_BLAST":
        return fighter_is_red_eyes(fighter)
    if support_card_id == "WRATH_OF_NEOS":
        return fighter_is_elemental_hero(fighter)
    if support_card_id == "TEARLAMENTS_SULLIEK":
        return fighter_is_tearlaments(fighter)
    if support_card_id == "CLOCK_TOWER_PRISON":
        return fighter_is_destiny_hero(fighter)
    if support_card_id == "CRYSTRON_INCLUSION":
        return fighter_is_crystron(fighter)
    if support_card_id == "DARK_WORLD_DEALINGS":
        return fighter_is_dark_world(fighter)
    if support_card_id == "MAGNET_CONVERSION":
        return fighter_is_magnet_warrior(fighter)
    if support_card_id == "LABRYNTH_BARRAGE":
        return fighter_is_labrynth(fighter)
    if support_card_id == "ORCUSTRATED_BABEL":
        return fighter_is_orcust(fighter)
    if support_card_id == "PUPPET_PARADE":
        return fighter_is_gimmick_puppet(fighter)
    if support_card_id == "KASHTIRA_BIG_BANG":
        return fighter_is_kashtira(fighter)
    if support_card_id == "BLACK_SONIC":
        return fighter_is_blackwing(fighter)
    if support_card_id == "OJAMA_COUNTRY":
        return fighter_is_ojama(fighter)
    if support_card_id == "STAKE_YOUR_SOUL":
        return fighter_is_vanquish_soul(fighter)
    if support_card_id == "SILENT_SWORD_SLASH":
        return fighter_is_silent_swordsman(fighter)
    if support_card_id == "CLOWN_CREW_REHEARSAL":
        return fighter_is_clown_clan(fighter)
    if support_card_id == "MIMIGHOUL_CHARM":
        return fighter_is_mimighoul(fighter)
    if support_card_id == "YUMMY_SURPRISE":
        return fighter_is_yummy(fighter)
    if support_card_id == "MASK_CHANGE":
        return fighter_is_masked_hero(fighter)
    if support_card_id == "DRAGONIC_DIAGRAM":
        return fighter_is_true_king_or_true_draco(fighter)
    if support_card_id == "BRANDED_FUSION":
        return fighter_is_albaz(fighter)
    if support_card_id in {"OVERLOAD_FUSION", "FUTURE_FUSION", "POWER_BOND"}:
        return fighter_is_cyber_dragon(fighter)
    if support_card_id == "RED_EYES_FUSION":
        return fighter_is_red_eyes(fighter)
    if support_card_id == "GEM_KNIGHT_FUSION":
        return fighter_is_gem_knight(fighter)
    if support_card_id == "FRIGHTFUR_FUSION":
        return fighter_is_frightfur_or_fluffal(fighter)
    if support_card_id == "NEOS_FUSION":
        return fighter_is_neos(fighter)
    if support_card_id == "MYUTANT_FUSION":
        return fighter_is_myutant(fighter)
    if support_card_id == "SHADDOLL_FUSION":
        return fighter_is_shaddoll(fighter)
    if support_card_id == "FUSION_DESTINY":
        return fighter_is_destiny_hero(fighter)
    if support_card_id == "VISION_FUSION":
        return fighter_is_vision_hero(fighter)
    if support_card_id == "FOSSIL_FUSION":
        return fighter_is_fossil(fighter)
    if support_card_id == "ANCIENT_GEAR_FUSION":
        return fighter_is_ancient_gear(fighter)
    if support_card_id == "METALFOES_FUSION":
        return fighter_is_metalfoes(fighter)
    if support_card_id == "INVOCATION":
        return fighter_is_invoked(fighter)
    if support_card_id == "LUNALIGHT_FUSION":
        return fighter_is_lunalight(fighter)
    if support_card_id == "TRICKSTAR_FUSION":
        return fighter_is_trickstar(fighter)
    if support_card_id == "THUNDER_DRAGON_FUSION":
        return fighter_is_thunder_dragon(fighter)
    if support_card_id == "PRIMITE_FUSION":
        return fighter_is_primite(fighter)
    if support_card_id == "PREDAPRIME_FUSION":
        return fighter_is_predaplant(fighter)
    if support_card_id == "TYRANT_DINO_FUSION":
        return fighter_is_dinowrestler(fighter)
    if support_card_id in {"RYZEAL_CROSS", "RYZEAL_PLUGIN"}:
        return fighter_is_ryzeal(fighter)
    if support_card_id == "PRIMEVAL_PLANET_PERLEREINO":
        return fighter_is_tearlaments(fighter) or fighter_is_visas_starfrost(fighter)
    if support_card_id == "PRESSURED_PLANET_WRAITSOTH":
        return fighter_is_kashtira(fighter) or fighter_is_visas_starfrost(fighter)
    if support_card_id == "VANQUISH_SOUL_CALAMITY_CAESAR":
        return fighter_is_vanquish_soul(fighter)
    if support_card_id == "FILIA_DIABELL":
        return fighter_is_diabell(fighter)
    if support_card_id == "NECROVALLEY":
        return fighter_is_gravekeeper(fighter)
    if support_card_id == "MILLENNIUM_ANKH":
        return fighter_is_forbidden_one(fighter)
    if support_card_id == "WIGHT_REANIMATOR":
        return fighter_is_skull_servant_or_wight(fighter)
    if support_card_id == "PANDEMONIUM":
        return fighter_is_archfiend(fighter)
    if support_card_id == "ROYAL_PENGUINS_GARDEN":
        return fighter_is_penguin(fighter)
    if support_card_id == "BLAZE_ACCELERATOR":
        return fighter_is_volcanic(fighter)
    if support_card_id == "GRAND_SPIRITUAL_ART_ICHIRIN":
        return fighter_is_charmer_familiar_channeler(fighter)
    if support_card_id == "TRICKSTAR_REINCARNATION":
        return fighter_is_trickstar(fighter)
    if support_card_id == "LIGHT_WING_SHIELD":
        return fighter_is_utopia_or_utopic(fighter)
    if support_card_id == "CERULEAN_SKYFIRE":
        return fighter_is_hamon(fighter)
    if support_card_id == "FALLEN_PARADISE":
        return fighter_is_raviel(fighter)
    if support_card_id == "HYPER_BLAZE":
        return fighter_is_uria_lord_of_searing_flames(fighter)
    if support_card_id in {"BEWARE_THE_WHITE_FOREST", "TALES_OF_THE_WHITE_FOREST"}:
        return fighter_is_white_forest(fighter)
    if support_card_id == "YUM_YUM_YUMMYS":
        return fighter_is_yummy(fighter)
    if support_card_id == "ULTIMATE_CRYSTAL_MAGIC":
        return fighter_is_crystal_beast(fighter)
    if support_card_id == "TOON_BRIEFCASE":
        return fighter_is_toon(fighter)
    if support_card_id == "MOUND_OF_THE_BOUND_CREATOR":
        return (
            fighter_is_the_winged_dragon_of_ra(fighter)
            or fighter_is_slifer_the_sky_dragon(fighter)
            or fighter_is_obelisk_the_tormentor(fighter)
        )
    if support_card_id == "JJ_KEWL_TUNE":
        return fighter_is_kewl_tune(fighter)
    if support_card_id == "MITSURUGI_GREAT_PURIFICATION":
        return fighter_is_mitsurugi(fighter)
    if support_card_id == "DRACOTAIL_FLAME":
        return fighter_is_dracotail(fighter)
    if support_card_id in {"SOUL_SERVANT", "ETERNAL_SOUL"}:
        return fighter_is_dark_magician(fighter)
    if support_card_id == "BURST_STREAM_OF_DESTRUCTION":
        return fighter_is_blue_eyes(fighter)
    if support_card_id == "RELINQUISHED_FUSION":
        return fighter_is_relinquished(fighter)
    if support_card_id == "SPEEDROID_CLEAR_WING_WONDER":
        return fighter_is_speedroid(fighter) or fighter_is_clear_wing(fighter)
    if support_card_id == "FIRE_KING_ISLAND":
        return fighter_is_fire_king(fighter)
    if support_card_id == "VOID_PURIFICATION":
        return fighter_is_infernoid(fighter)
    return False


def team_eligible_special_support_cards(team: Sequence[Fighter]) -> List[SupportCardDef]:
    special_ids = list(SPECIAL_SUPPORT_IDS)
    cards: List[SupportCardDef] = []
    for support_card_id in special_ids:
        if any(fighter_matches_special_support(support_card_id, fighter) for fighter in team):
            card = get_support_card_def(support_card_id)
            if card is not None:
                cards.append(card)
    return cards


def support_runtime_flag(runtime: Optional[SupportDeckRuntime], key: str, default=None):
    if runtime is None:
        return default
    return runtime.flags.get(key, default)


def set_support_runtime_flag(runtime: Optional[SupportDeckRuntime], key: str, value) -> None:
    if runtime is None:
        return
    runtime.flags[key] = value


def apply_special_support_fusion_bonus(
    team: Sequence[Fighter],
    enemy_team: Sequence[Fighter],
    runtime: Optional[SupportDeckRuntime],
    support_card_id: str,
    card_name: str,
    matcher,
    ally_pct: float,
    enemy_pct: float,
    log_entries: List[str],
) -> None:
    if runtime is None:
        return
    support_ids = {card.support_card_id for card in runtime.active_cards}
    if support_card_id not in support_ids:
        return
    used_flag = f"{support_card_id.lower()}_used"
    if support_runtime_flag(runtime, used_flag, False):
        return
    candidates = [fighter for fighter in team if fighter.alive and matcher(fighter)]
    if not candidates:
        return
    chosen = RNG.choice(candidates)
    ally_choices = [ally for ally in team if ally.alive and ally is not chosen]
    if not ally_choices:
        return
    donor_ally = RNG.choice(ally_choices)
    atk_gain = gain_flat_stat_bonus(chosen, "ATK", max(1, int(round(donor_ally.atk * ally_pct))))
    def_gain = gain_flat_stat_bonus(chosen, "DEF", max(1, int(round(donor_ally.defense * ally_pct))))
    spd_gain = gain_flat_stat_bonus(chosen, "SPD", max(1, int(round(donor_ally.spd * ally_pct))))
    details = [f"**+{atk_gain} ATK**", f"**+{def_gain} DEF**", f"**+{spd_gain} SPD** from **{donor_ally.name}**"]
    if enemy_pct > 0:
        enemy_choices = [enemy for enemy in enemy_team if enemy.alive]
        if enemy_choices:
            donor_enemy = RNG.choice(enemy_choices)
            enemy_atk_gain = gain_flat_stat_bonus(chosen, "ATK", max(1, int(round(donor_enemy.atk * enemy_pct))))
            enemy_def_gain = gain_flat_stat_bonus(chosen, "DEF", max(1, int(round(donor_enemy.defense * enemy_pct))))
            enemy_spd_gain = gain_flat_stat_bonus(chosen, "SPD", max(1, int(round(donor_enemy.spd * enemy_pct))))
            details.append(f"**+{enemy_atk_gain} ATK**")
            details.append(f"**+{enemy_def_gain} DEF**")
            details.append(f"**+{enemy_spd_gain} SPD** from **{donor_enemy.name}**")
    set_support_runtime_flag(runtime, used_flag, True)
    if support_card_id == "POWER_BOND":
        set_support_runtime_flag(runtime, "power_bond_target_monster_id", chosen.monster_id)
        set_support_runtime_flag(runtime, "power_bond_target_name", chosen.name)
    log_entries.append(f"**{card_name}** empowers **{chosen.name}** with " + ", ".join(details) + ".")


def special_support_active_for_fighter(fighter: Optional[Fighter], support_card_id: str, matcher=None) -> bool:
    if fighter is None:
        return False
    team = list(ACTIVE_TEAMMATES_BY_FIGHTER.get(id(fighter), []))
    if not team:
        return False
    return team_has_active_special_support(team, support_card_id, matcher)


def global_ojama_country_active(fighter: Optional[Fighter]) -> bool:
    return special_support_active_for_fighter(fighter, "OJAMA_COUNTRY", fighter_is_ojama)


def shuffle_special_support_back_into_deck(runtime: Optional[SupportDeckRuntime], support_card_id: str) -> bool:
    if runtime is None:
        return False
    card = next((card for card in SUPPORT_CARD_DEFS if card.support_card_id == support_card_id), None)
    if card is None:
        return False
    remaining = runtime.shuffled_cards[runtime.draw_index:]
    insert_at = runtime.draw_index + (RNG.randint(0, len(remaining)) if remaining else 0)
    runtime.shuffled_cards.insert(insert_at, card)
    return True


def _team_has_active_special_support_base(team: Sequence[Fighter], support_card_id: str, matcher=None) -> bool:
    for fighter in team:
        if not fighter.alive:
            continue
        if matcher is not None and not matcher(fighter):
            continue
        if any(card.support_card_id == support_card_id for card in ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(fighter), [])):
            return True
    return False


def _fighter_had_active_special_support_base(fighter: Fighter, support_card_id: str, matcher=None) -> bool:
    """Return whether a fighter had an active special support card at the time it was defeated.

    team_has_active_special_support intentionally ignores defeated fighters so passive
    effects from already-dead monsters do not remain online. Defeat-triggered cards
    such as Puppet Parade need to check the fallen monster itself before it is removed.
    """
    if matcher is not None and not matcher(fighter):
        return False
    return any(card.support_card_id == support_card_id for card in ACTIVE_SUPPORT_CARDS_BY_FIGHTER.get(id(fighter), []))


def fighter_matches_special_support(support_card_id: str, fighter: Fighter) -> bool:
    if is_extra_deck_fighter(fighter):
        return False
    return _fighter_matches_special_support_base(support_card_id, fighter)


def team_has_active_special_support(team: Sequence[Fighter], support_card_id: str, matcher=None) -> bool:
    non_extra = [fighter for fighter in team if not is_extra_deck_fighter(fighter)]
    return _team_has_active_special_support_base(non_extra, support_card_id, matcher)


def fighter_had_active_special_support(fighter: Fighter, support_card_id: str, matcher=None) -> bool:
    if is_extra_deck_fighter(fighter):
        return False
    return _fighter_had_active_special_support_base(fighter, support_card_id, matcher)
