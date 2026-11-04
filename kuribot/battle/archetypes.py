"""Archetype checks used by card effects (is this fighter a Blue-Eyes, a HERO, ...)."""

from __future__ import annotations

import re

from kuribot.models import Fighter


def fighter_is_cyber_dragon(fighter: Fighter) -> bool:
    return "cyber dragon" in fighter.name.lower()


def fighter_is_dark_magician(fighter: Fighter) -> bool:
    return "dark magician" in fighter.name.lower()


def fighter_is_magician_girl(fighter: Fighter) -> bool:
    return "magician girl" in fighter.name.lower()


def fighter_is_galaxy_or_photon(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "galaxy", "photon")


def fighter_is_blue_eyes(fighter: Fighter) -> bool:
    return "blue-eyes" in fighter.name.lower() or "blue eyes" in fighter.name.lower()


def fighter_is_red_eyes(fighter: Fighter) -> bool:
    return "red-eyes" in fighter.name.lower() or "red eyes" in fighter.name.lower()


def fighter_is_elemental_hero(fighter: Fighter) -> bool:
    return "elemental hero" in fighter.name.lower()


def fighter_is_tearlaments(fighter: Fighter) -> bool:
    return "tearlaments" in fighter.name.lower()


def fighter_is_destiny_hero(fighter: Fighter) -> bool:
    normalized = fighter.name.lower()
    return "destiny hero" in normalized or "destiny-hero" in normalized


def fighter_is_crystron(fighter: Fighter) -> bool:
    return "crystron" in fighter.name.lower()


def fighter_is_dark_world(fighter: Fighter) -> bool:
    return "dark world" in fighter.name.lower()


def fighter_is_magnet_warrior(fighter: Fighter) -> bool:
    normalized = fighter.name.lower()
    return "magnet warrior" in normalized or "magnetwarrior" in normalized


def fighter_is_labrynth(fighter: Fighter) -> bool:
    return "labrynth" in fighter.name.lower()


def fighter_is_orcust(fighter: Fighter) -> bool:
    return "orcust" in fighter.name.lower()


def fighter_is_gimmick_puppet(fighter: Fighter) -> bool:
    return "gimmick puppet" in fighter.name.lower()


def fighter_is_kashtira(fighter: Fighter) -> bool:
    return "kashtira" in fighter.name.lower()


def fighter_is_blackwing(fighter: Fighter) -> bool:
    return "blackwing" in fighter.name.lower()


def fighter_is_ojama(fighter: Fighter) -> bool:
    return "ojama" in fighter.name.lower()


def fighter_is_vanquish_soul(fighter: Fighter) -> bool:
    return "vanquish soul" in fighter.name.lower()


def fighter_is_silent_swordsman(fighter: Fighter) -> bool:
    return "silent swordsman" in fighter.name.lower()


def fighter_is_clown_clan(fighter: Fighter) -> bool:
    return "clown clan" in fighter.name.lower()


def fighter_is_mimighoul(fighter: Fighter) -> bool:
    return "mimighoul" in fighter.name.lower()


def fighter_is_yummy(fighter: Fighter) -> bool:
    return "yummy" in fighter.name.lower()


def fighter_is_masked_hero(fighter: Fighter) -> bool:
    name = fighter.name.lower()
    return "masked hero" in name or ("masked" in name and "hero" in name)


def fighter_is_true_king_or_true_draco(fighter: Fighter) -> bool:
    name = fighter.name.lower()
    return "true king" in name or "true draco" in name


def fighter_is_albaz(fighter: Fighter) -> bool:
    return "albaz" in fighter.name.lower()


def fighter_name_contains_any(fighter: Fighter, *needles: str) -> bool:
    name = fighter.name.lower()
    return any(str(needle).lower() in name for needle in needles)


def normalized_fighter_name(fighter: Fighter) -> str:
    # Card art/database names are not always punctuation-identical (for example,
    # "Slifer the Sky Dragon" vs. "Slifer, the Sky Dragon"). Normalize names
    # for exact-card support checks so special supports work across both forms.
    return re.sub(r"[^a-z0-9]+", " ", str(fighter.name or "").casefold()).strip()


def fighter_is_gem_knight(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "gem-knight", "gem knight")


def fighter_is_frightfur_or_fluffal(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "frightfur", "fluffal")


def fighter_is_neos(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "neos")


def fighter_is_myutant(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "myutant")


def fighter_is_shaddoll(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "shaddoll")


def fighter_is_vision_hero(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "vision hero", "vision-hero")


def fighter_is_fossil(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "fossil")


def fighter_is_ancient_gear(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "ancient gear")


def fighter_is_metalfoes(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "metalfoes")


def fighter_is_invoked(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "invoke", "invoked")


def fighter_is_lunalight(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "lunalight")


def fighter_is_trickstar(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "trickstar")


def fighter_is_thunder_dragon(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "thunder dragon")


def fighter_is_primite(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "primite")


def fighter_is_predaplant(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "predaplant")


def fighter_is_dinowrestler(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "dinowrestler")


def fighter_is_ryzeal(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "ryzeal")


def fighter_is_visas_starfrost(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "visas starfrost")


def fighter_is_diabell(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "diabell")


def fighter_is_gravekeeper(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "gravekeeper")


def fighter_is_forbidden_one(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "forbidden one")


def fighter_is_skull_servant_or_wight(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "skull servant", "wight")


def fighter_is_archfiend(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "archfiend")


def fighter_is_penguin(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "penguin")


def fighter_is_volcanic(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "volcanic")


def fighter_is_charmer_familiar_channeler(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "charmer", "familiar-possessed", "familiar possessed", "channeler")


def fighter_is_utopia_or_utopic(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "utopia", "utopic")


def fighter_is_hamon(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "hamon")


def fighter_is_raviel(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "raviel")


def fighter_is_uria_lord_of_searing_flames(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "uria, lord of searing flames")


def fighter_is_white_forest(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "white forest")


def fighter_is_crystal_beast(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "crystal beast")


def fighter_is_toon(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "toon")


def fighter_is_kewl_tune(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "kewl tune")


def fighter_is_mitsurugi(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "mitsurugi")


def fighter_is_dracotail(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "dracotail")


def fighter_is_relinquished(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "relinquished")


def fighter_is_speedroid(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "speedroid")


def fighter_is_clear_wing(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "clear wing")


def fighter_is_fire_king(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "fire king")


def fighter_is_infernoid(fighter: Fighter) -> bool:
    return fighter_name_contains_any(fighter, "infernoid")


RA_MOUND_ELIGIBLE_NAMES = {
    "the winged dragon of ra",
    "the winged dragon of ra sphere mode",
    "the winged dragon of ra immortal phoenix",
}


def fighter_is_the_winged_dragon_of_ra(fighter: Fighter) -> bool:
    return normalized_fighter_name(fighter) in RA_MOUND_ELIGIBLE_NAMES


def fighter_is_slifer_the_sky_dragon(fighter: Fighter) -> bool:
    return normalized_fighter_name(fighter) == "slifer the sky dragon"


def fighter_is_obelisk_the_tormentor(fighter: Fighter) -> bool:
    return normalized_fighter_name(fighter) == "obelisk the tormentor"
