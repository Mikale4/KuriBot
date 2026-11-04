"""Support card definitions, stages and experience."""

from __future__ import annotations

import re
import sqlite3
from typing import Dict, List, Optional, Sequence, Tuple

from kuribot.config import ATTRIBUTES, MONSTER_TYPES, RNG
from kuribot.models import SupportCardDef
from kuribot.database import db


def special_support_stage_one_cards() -> List[SupportCardDef]:
    return [card for card in support_card_stage_one_defs() if str(card.attribute or "").upper() == "SPECIAL" or str(card.stat_type or "").upper() == "SPECIAL"]


def random_special_support_card() -> SupportCardDef:
    cards = special_support_stage_one_cards()
    return RNG.choice(cards) if cards else random_support_card()


SUPPORT_CARD_DEFS: List[SupportCardDef] = []
SUPPORT_CARD_DEF_BY_ID: Dict[str, SupportCardDef] = {}
SUPPORT_CARD_STAGES_BY_FAMILY: Dict[str, List[SupportCardDef]] = {}


def _slugify_support_target_name(name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(name or "").strip()).strip("_")
    return normalized.upper() or "UNKNOWN"


def support_target_card_id(target_name: str, stat_type: str, bonus: int) -> str:
    return f"{_slugify_support_target_name(target_name)}_{str(stat_type or '').upper()}_{int(bonus)}"


def build_support_card_defs() -> List[SupportCardDef]:
    defs: List[SupportCardDef] = []
    stat_suffix = {"ATK": "Force", "DEF": "Fortress", "SPD": "Flight"}
    for attribute in ATTRIBUTES:
        display_name = attribute.title()
        for stat_type in ("ATK", "DEF", "SPD"):
            suffix = stat_suffix[stat_type]
            bonus = 25
            defs.append(SupportCardDef(
                support_card_id=support_target_card_id(attribute, stat_type, bonus),
                name=f"{display_name} {suffix}",
                attribute=attribute,
                bonus_pct=bonus,
                stat_type=stat_type,
                description=f"{display_name} monsters gain {bonus}% {stat_type}.",
            ))

    for monster_type in MONSTER_TYPES:
        for stat_type in ("ATK", "DEF", "SPD"):
            suffix = stat_suffix[stat_type]
            bonus = 25
            defs.append(SupportCardDef(
                support_card_id=support_target_card_id(monster_type, stat_type, bonus),
                name=f"{monster_type} {suffix}",
                attribute=monster_type,
                bonus_pct=bonus,
                stat_type=stat_type,
                description=f"{monster_type} monsters gain {bonus}% {stat_type}.",
            ))

    defs.extend([
        SupportCardDef(
            support_card_id="CYBER_REVSYSTEM",
            name="Cyber Revsystem",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Once per round, if a Cyber Dragon monster is knocked out, revive it with 100% max HP.",
        ),
        SupportCardDef(
            support_card_id="DARK_BURNING_ATTACK",
            name="Dark Burning Attack",
            attribute="SPECIAL",
            bonus_pct=50,
            stat_type="SPECIAL",
            description="Magician Girl monsters gain +50% ATK and their attacks inflict BURN.",
        ),
        SupportCardDef(
            support_card_id="BLUE_EYES_SPIRIT_CALL",
            name="Blue-Eyes Spirit Call",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Once per battle, if a Blue-Eyes monster would be defeated, it survives at 1 HP and gains SHIELD.",
        ),
        SupportCardDef(
            support_card_id="RED_EYES_INFERNO_CHAIN",
            name="Red-Eyes Inferno Chain",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Red-Eyes monsters' attacks apply BURN. If the target is already burned, Red-Eyes monsters deal +50% more damage.",
        ),
        SupportCardDef(
            support_card_id="CYBER_EMERGENCY",
            name="Cyber Emergency",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Once per battle, if a Cyber Dragon monster drops below 50% HP, create a TOKEN for them.",
        ),
        SupportCardDef(
            support_card_id="HERO_SIGNAL",
            name="HERO Signal",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="When an Elemental HERO monster is defeated, the strongest surviving ally gains +100% ATK and +50% SPD for 2 rounds.",
        ),
        SupportCardDef(
            support_card_id="ANCIENT_GEAR_FORTRESS",
            name="Ancient Gear Fortress",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Ancient Gear monsters gain PROTECTION. While an Ancient Gear monster has PROTECTION, their attacks ignore DEF.",
        ),
        SupportCardDef(
            support_card_id="BLACKWING_WHIRLWIND",
            name="Blackwing Whirlwind",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Blackwing monsters gain +100% SPD and +25% DODGE.",
        ),
        SupportCardDef(
            support_card_id="GALAXY_EXPEDITION",
            name="Galaxy Expedition",
            attribute="SPECIAL",
            bonus_pct=50,
            stat_type="SPECIAL",
            description="Galaxy monsters and Photon monsters gain +50% ATK. If they defeat an enemy, shuffle this card back into the deck after it's been discarded.",
        ),
        SupportCardDef(
            support_card_id="DARK_MAGIC_ATTACK",
            name="Dark Magic Attack",
            attribute="SPECIAL",
            bonus_pct=50,
            stat_type="SPECIAL",
            description="Dark Magician monsters gain 50% ATK.",
        ),
        SupportCardDef(
            support_card_id="NEUTRON_BLAST",
            name="Neutron Blast",
            attribute="SPECIAL",
            bonus_pct=35,
            stat_type="SPECIAL",
            description="Blue-Eyes monsters have a 35% chance to act again.",
        ),
        SupportCardDef(
            support_card_id="INFERNO_FIRE_BLAST",
            name="Inferno Fire Blast",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="While a Red-Eyes monster is alive, whenever the opponent loses a heart by any means, they lose 1 extra heart.",
        ),
        SupportCardDef(
            support_card_id="WRATH_OF_NEOS",
            name="Wrath of Neos",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="If an Elemental HERO monster is knocked out, deal damage equal to half of its max HP to a random enemy.",
        ),
        SupportCardDef(
            support_card_id="TEARLAMENTS_SULLIEK",
            name="Tearlaments Sulliek",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="While a Tearlaments monster is alive, enemy equip spells are disabled.",
        ),
        SupportCardDef(
            support_card_id="CLOCK_TOWER_PRISON",
            name="Clock Tower Prison",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Destiny HERO monsters gain PROTECTION. After this card is discarded, destroy 1 random Destiny HERO monster and grant 50% of its ATK to a random ally.",
        ),
        SupportCardDef(
            support_card_id="CRYSTRON_INCLUSION",
            name="Crystron Inclusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Crystron monsters gain PROTECTION. After this card is discarded, destroy 1 random Crystron monster and deal damage to all enemies equal to 25% of its max HP.",
        ),
        SupportCardDef(
            support_card_id="DARK_WORLD_DEALINGS",
            name="Dark World Dealings",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Dark World monsters gain 25% ATK. After this card is discarded, revive all Dark World monsters with 50% HP.",
        ),
        SupportCardDef(
            support_card_id="MAGNET_CONVERSION",
            name="Magnet Conversion",
            attribute="SPECIAL",
            bonus_pct=50,
            stat_type="SPECIAL",
            description="Magnet Warrior monsters gain 50% DEF. When a Magnet Warrior monster is attacked, it heals for 50% of the damage taken.",
        ),
        SupportCardDef(
            support_card_id="LABRYNTH_BARRAGE",
            name="Labrynth Barrage",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Labrynth monsters' attacks have a 50% chance to destroy the target's equip spell. Once per battle, after this card is discarded, shuffle it back into the deck.",
        ),
        SupportCardDef(
            support_card_id="ORCUSTRATED_BABEL",
            name="Orcustrated Babel",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Orcust monsters gain PRECOG. Once per battle, after this card is discarded, shuffle it back into the deck.",
        ),
        SupportCardDef(
            support_card_id="PUPPET_PARADE",
            name="Puppet Parade",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="When a Gimmick Puppet monster is knocked out, the enemy loses 1 heart.",
        ),
        SupportCardDef(
            support_card_id="KASHTIRA_BIG_BANG",
            name="Kashtira Big Bang",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="While a Kashtira monster is alive, enemies cannot revive.",
        ),
        SupportCardDef(
            support_card_id="BLACK_SONIC",
            name="Black Sonic",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Once per round, if a Blackwing monster is targeted for an attack, negate the attack and deal damage to the attacker equal to 25% of its HP.",
        ),
        SupportCardDef(
            support_card_id="OJAMA_COUNTRY",
            name="Ojama Country",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="While an Ojama monster is alive, all monsters have their ATK and DEF swapped.",
        ),
        SupportCardDef(
            support_card_id="STAKE_YOUR_SOUL",
            name="Stake your Soul",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="When a Vanquish Soul monster is targeted for an attack, reduce the incoming damage by 50% and redirect that attack to a different ally if possible.",
        ),
        SupportCardDef(
            support_card_id="SILENT_SWORD_SLASH",
            name="Silent Sword Slash",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Silent Swordsman monsters gain 10% ATK at the end of every monster's turn.",
        ),
        SupportCardDef(
            support_card_id="CLOWN_CREW_REHEARSAL",
            name="Clown Clan Rehersal",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Clown Clan monster copies the ATK, DEF, and SPD of 1 random enemy monster.",
        ),
        SupportCardDef(
            support_card_id="MIMIGHOUL_CHARM",
            name="Mimighoul Charm",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Mimighoul monster becomes CHARMED. While a CHARMED Mimighoul monster is alive, all enemies take 25% HP damage at the end of a round.",
        ),
        SupportCardDef(
            support_card_id="YUMMY_SURPRISE",
            name="Yummy Surprise",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Yummy monsters gain 50% DODGE. If a Yummy monster successfully dodges an attack, the attacker loses 25% of their max HP.",
        ),
        SupportCardDef(
            support_card_id="MASK_CHANGE",
            name="Mask Change",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Once per round, if a Masked HERO monster is targeted for an attack, dodge the attack, then that monster gains 25% ATK.",
        ),
        SupportCardDef(
            support_card_id="DRAGONIC_DIAGRAM",
            name="Dragonic Diagram",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="True King and True Draco monsters gain IMMUNITY and PROTECTION. After this card is discarded, destroy 1 random True King or True Draco monster.",
        ),
        SupportCardDef(
            support_card_id="BRANDED_FUSION",
            name="Branded Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Albaz monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="OVERLOAD_FUSION",
            name="Overload Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="When a Cyber Dragon monster is knocked out, transfer 100% of its ATK to a random ally.",
        ),
        SupportCardDef(
            support_card_id="FUTURE_FUSION",
            name="Future Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Cyber Dragon monsters cannot act. After this card is discarded, all Cyber Dragon monsters gain 100% of a random enemy's ATK.",
        ),
        SupportCardDef(
            support_card_id="RED_EYES_FUSION",
            name="Red-Eyes Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Red-Eyes monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="GEM_KNIGHT_FUSION",
            name="Gem-Knight Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Gem-Knight monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="FRIGHTFUR_FUSION",
            name="Frightfur Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Frightfur or Fluffal monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="NEOS_FUSION",
            name="Neos Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Neos monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="POWER_BOND",
            name="Power Bond",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Cyber Dragon monster gains 100% of 1 random ally's ATK, DEF, and SPD. After this card is discarded, destroy that Cyber Dragon monster and lose 1 heart.",
        ),
        SupportCardDef(
            support_card_id="MYUTANT_FUSION",
            name="Myutant Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Myutant monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="SHADDOLL_FUSION",
            name="Shaddoll Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Shaddoll monster gains 50% of 1 random ally's ATK, DEF, and 25% of 1 random enemy's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="FUSION_DESTINY",
            name="Fusion Destiny",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Destiny HERO monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="VISION_FUSION",
            name="Vision Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Vision HERO monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="FOSSIL_FUSION",
            name="Fossil Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Fossil monster gains 50% of 1 random ally's ATK, DEF, and 25% of 1 random enemy's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="ANCIENT_GEAR_FUSION",
            name="Ancient Gear Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Ancient Gear monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="METALFOES_FUSION",
            name="Metalfoes Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Metalfoes monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="INVOCATION",
            name="Invocation",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Invoke monster gains 50% of 1 random ally's ATK, DEF, and 25% of 1 random enemy's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="LUNALIGHT_FUSION",
            name="Lunalight Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Lunalight monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="TRICKSTAR_FUSION",
            name="Trickstar Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Trickstar monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="THUNDER_DRAGON_FUSION",
            name="Thunder Dragon Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Thunder Dragon monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="PRIMITE_FUSION",
            name="Primite Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Primite monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="PREDAPRIME_FUSION",
            name="Predaprime Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Predaplant monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="TYRANT_DINO_FUSION",
            name="Tyrant Dino Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random Dinowrestler monster gains 50% of 1 random ally's ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="RYZEAL_CROSS",
            name="Ryzeal Cross",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="While a Ryzeal monster is alive, enemy equip spells are disabled. Once per battle, after this card is discarded, shuffle it back into the deck.",
        ),
        SupportCardDef(
            support_card_id="RYZEAL_PLUGIN",
            name="Ryzeal Plugin",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Revive 1 random Ryzeal monster to 100% HP that was previously knocked out before drawing this card. After this card is discarded, all Ryzeal monsters gain PROTECTION for 1 round.",
        ),
        SupportCardDef(
            support_card_id="PRIMEVAL_PLANET_PERLEREINO",
            name="Primeval Planet Perlereino",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Tearlament monsters and Visas Starfrost gain +50% ATK. After this card is discarded, 1 random monster takes 50% max HP damage.",
        ),
        SupportCardDef(
            support_card_id="PRESSURED_PLANET_WRAITSOTH",
            name="Pressured Planet Wraitsoth",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Kashtira monsters and Visas Starfrost gain +50% ATK. Once per battle, if a Kashtira monster is knocked out, destroy 1 random enemy monster.",
        ),
        SupportCardDef(
            support_card_id="VANQUISH_SOUL_CALAMITY_CAESAR",
            name="Vanquish Soul Calamity Caesar",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="If a Vanquish Soul monster has debuffs before drawing this card, remove them, then destroy the equip spell if one caused them.",
        ),
        SupportCardDef(
            support_card_id="FILIA_DIABELL",
            name="Filia Diabell",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="When a Diabell monster is knocked out, replace it with a randomly generated Diabell monster.",
        ),
        SupportCardDef(
            support_card_id="NECROVALLEY",
            name="Necrovalley",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="While a Gravekeeper monster is alive, enemy support cards are disabled.",
        ),
        SupportCardDef(
            support_card_id="MILLENNIUM_ANKH",
            name="Millennium Ankh",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="All Forbidden One monsters lose 50% ATK and DEF. After this card is discarded, destroy 3 Forbidden One monsters and replace them with 1 randomly generated Exodia monster with +100% HP, ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="WIGHT_REANIMATOR",
            name="Wight Reanimator",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="When a Skull Servant or Wight monster is knocked out, revive it with 1 HP and +25% ATK.",
        ),
        SupportCardDef(
            support_card_id="PANDEMONIUM",
            name="Pandemonium",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="While an Archfiend monster is alive, you cannot lose Hearts.",
        ),
        SupportCardDef(
            support_card_id="ROYAL_PENGUINS_GARDEN",
            name="Royal Penguins Garden",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="When a Penguin monster is targeted for an attack, 50% chance to FREEZE the attacker for 1 turn before damage calculation.",
        ),
        SupportCardDef(
            support_card_id="BLAZE_ACCELERATOR",
            name="Blaze Accelerator",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="When a Volcanic monster is attacked, 50% chance to inflict BURN on the attacker and 25% chance for the opponent to lose 1 Heart.",
        ),
        SupportCardDef(
            support_card_id="GRAND_SPIRITUAL_ART_ICHIRIN",
            name="Grand Spiritual Art - Ichirin",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="When a Charmer, Familiar-Possessed, or Channeler monster is attacked, 50% chance to inflict CHARM on the attacker.",
        ),
        SupportCardDef(
            support_card_id="TRICKSTAR_REINCARNATION",
            name="Trickstar Reincarnation",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="If a Trickstar monster is knocked out, 25% chance for the opponent to discard all cards in their deck.",
        ),
        SupportCardDef(
            support_card_id="LIGHT_WING_SHIELD",
            name="Light Wing Shield",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Utopia monsters and Utopic monsters gain COUNTER and REFLECT.",
        ),
        SupportCardDef(
            support_card_id="CERULEAN_SKYFIRE",
            name="Cerulean Skyfire",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Once per round, if a Hamon monster has debuffs, remove them and gain +25% ATK and DEF for each debuff removed.",
        ),
        SupportCardDef(
            support_card_id="FALLEN_PARADISE",
            name="Fallen Paradise",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Raviel monsters gain IMMUNITY. After this card is discarded, if a Raviel monster is alive, draw 4 cards instead of 3.",
        ),
        SupportCardDef(
            support_card_id="HYPER_BLAZE",
            name="Hyper Blaze",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Once per round, Uria destroys an ally equip spell to gain +100% ATK.",
        ),
        SupportCardDef(
            support_card_id="BEWARE_THE_WHITE_FOREST",
            name="Beware the White Forest",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="White Forest monsters' attacks have a 50% chance to destroy the target's equip spell. Once per battle, after this card is discarded, shuffle it back into the deck.",
        ),
        SupportCardDef(
            support_card_id="TALES_OF_THE_WHITE_FOREST",
            name="Tales of the White Forest",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="1 random White Forest monster gains a TOKEN. Once per battle, after this card is discarded, shuffle it back into the deck.",
        ),
        SupportCardDef(
            support_card_id="YUM_YUM_YUMMYS",
            name="Yum Yum Yummys",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="While 2 or more Yummy monsters are alive, you cannot lose Hearts. After this card is discarded, restore 1 Heart, if able.",
        ),
        SupportCardDef(
            support_card_id="ULTIMATE_CRYSTAL_MAGIC",
            name="Ultimate Crystal Magic",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="If 3 Crystal Beast monsters are defeated before drawing this card, activate this card immediately instead of losing the duel; replace them with a randomly generated Rainbow Dragon monster with +100% HP, ATK, DEF, and SPD.",
        ),
        SupportCardDef(
            support_card_id="TOON_BRIEFCASE",
            name="Toon Briefcase",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="If drawn on round 3 or 5 while a Toon monster is alive, 2 random enemies cannot act for 1 or 2 turns respectively.",
        ),

        SupportCardDef(
            support_card_id="JJ_KEWL_TUNE",
            name="JJ Kewl Tune",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Kewl Tune monsters gain the effects of the opponent's non-special support cards in hand.",
        ),
        SupportCardDef(
            support_card_id="MITSURUGI_GREAT_PURIFICATION",
            name="Mitsurugi Great Purification",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="When drawn, destroy 1 random Mitsurugi monster; then destroy 1 random enemy equip spell and 1 random enemy Special Support Card from hand, if any.",
        ),
        SupportCardDef(
            support_card_id="DRACOTAIL_FLAME",
            name="Dracotail Flame",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="When drawn, destroy 1 random enemy equip spell; then revive 1 random Dracotail monster with 100% HP. If none can revive, give 1 random Dracotail monster a TOKEN.",
        ),
        SupportCardDef(
            support_card_id="SOUL_SERVANT",
            name="Soul Servant",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Always appears in the opening hand. Once per battle, if a Dark Magician monster is alive, after discard shuffle this card and the other active support cards that were with it back into the deck.",
        ),
        SupportCardDef(
            support_card_id="BURST_STREAM_OF_DESTRUCTION",
            name="Burst Stream of Destruction",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Blue-Eyes monsters' attacks have a 25% chance to destroy the target's equip spell.",
        ),
        SupportCardDef(
            support_card_id="ETERNAL_SOUL",
            name="Eternal Soul",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Dark Magician monsters gain IMMUNITY. Once per round, when a Dark Magician monster attacks an enemy, inflict SILENCE on that enemy.",
        ),
        SupportCardDef(
            support_card_id="RELINQUISHED_FUSION",
            name="Relinquished Fusion",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Relinquished monsters absorb 15% of all enemies' ATK.",
        ),
        SupportCardDef(
            support_card_id="SPEEDROID_CLEAR_WING_WONDER",
            name="Speedroid Clear Wing Wonder",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Speedroid and Clear Wing monsters gain PRECOG. Once per round, when a Clear Wing monster is targeted for an attack, increase its ATK by +50% before PRECOG.",
        ),
        SupportCardDef(
            support_card_id="FIRE_KING_ISLAND",
            name="Fire King Island",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Once per round, when a Fire King monster is attacked, destroy the attacker's equip spell. When a Fire King monster is knocked out, 25% chance to revive with 100% HP.",
        ),
        SupportCardDef(
            support_card_id="VOID_PURIFICATION",
            name="Void Purification",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="Revive all Infernoid monsters knocked out before drawing this card to 100% HP. After discard, Infernoid monsters gain +50% ATK for 1 round, then are destroyed when the round ends.",
        ),
        SupportCardDef(
            support_card_id="MOUND_OF_THE_BOUND_CREATOR",
            name="Mound of the Bound Creator",
            attribute="SPECIAL",
            bonus_pct=0,
            stat_type="SPECIAL",
            description="The Winged Dragon of Ra gains or loses 100% ATK whenever either side loses a heart. Slifer gains 20% ATK per alive monster. Obelisk gains 100% of a knocked out monster's ATK.",
        ),
    ])
    return defs


def init_support_cards():
    if SUPPORT_CARD_DEFS and SUPPORT_CARD_DEF_BY_ID and SUPPORT_CARD_STAGES_BY_FAMILY:
        return

    # The tables are filled in place: other modules import them, so they must stay the same objects.
    SUPPORT_CARD_DEFS[:] = build_support_card_defs()
    SUPPORT_CARD_DEF_BY_ID.clear()
    SUPPORT_CARD_DEF_BY_ID.update({card.support_card_id: card for card in SUPPORT_CARD_DEFS})

    stages_by_family: Dict[str, List[SupportCardDef]] = {}
    for card in SUPPORT_CARD_DEFS:
        if card.attribute == "SPECIAL" or card.stat_type == "SPECIAL":
            family = card.support_card_id
        else:
            family = f"{card.attribute}_{card.stat_type}"
        stages_by_family.setdefault(family, []).append(card)
    for family, cards in stages_by_family.items():
        cards.sort(key=lambda card: card.bonus_pct)
    SUPPORT_CARD_STAGES_BY_FAMILY.clear()
    SUPPORT_CARD_STAGES_BY_FAMILY.update(stages_by_family)


def canonical_support_card_id(support_card_id: str) -> str:
    raw_id = str(support_card_id or "").strip()
    if not raw_id:
        return raw_id
    init_support_cards()
    if raw_id in SUPPORT_CARD_DEF_BY_ID:
        return raw_id
    match = re.match(r"^(.+)_(ATK|DEF|SPD)_(5|10|15|20|25)$", raw_id)
    if match:
        canonical_id = f"{match.group(1)}_{match.group(2)}_25"
        if canonical_id in SUPPORT_CARD_DEF_BY_ID:
            return canonical_id
    return raw_id


def get_support_card_def(support_card_id: str) -> Optional[SupportCardDef]:
    init_support_cards()
    return SUPPORT_CARD_DEF_BY_ID.get(canonical_support_card_id(str(support_card_id)))


def support_card_family_key(support_card_id: str) -> str:
    canonical_id = canonical_support_card_id(str(support_card_id))
    card = get_support_card_def(canonical_id)
    if card is None:
        return canonical_id
    if card.attribute == "SPECIAL" or card.stat_type == "SPECIAL":
        return card.support_card_id
    return f"{card.attribute}_{card.stat_type}"


def support_card_stage_defs(support_card_id: str) -> List[SupportCardDef]:
    init_support_cards()
    family = support_card_family_key(support_card_id)
    return list(SUPPORT_CARD_STAGES_BY_FAMILY.get(family, []))


def support_card_stage_number(support_card_id: str) -> int:
    stages = support_card_stage_defs(support_card_id)
    for idx, card in enumerate(stages, start=1):
        if card.support_card_id == support_card_id:
            return idx
    return 1


def support_card_max_stage(support_card_id: str) -> int:
    return max(1, len(support_card_stage_defs(support_card_id)))


def support_card_stage_one_def(support_card_id: str) -> Optional[SupportCardDef]:
    stages = support_card_stage_defs(support_card_id)
    return stages[0] if stages else get_support_card_def(support_card_id)


def is_support_card_stage_one(support_card_id: str) -> bool:
    card = get_support_card_def(support_card_id)
    if card is None:
        return False
    stage_one = support_card_stage_one_def(support_card_id)
    return stage_one is not None and stage_one.support_card_id == card.support_card_id


def support_card_exp_to_next_stage(support_card_id: str) -> int:
    return 0


def support_card_stage_one_defs() -> List[SupportCardDef]:
    init_support_cards()
    stage_one: List[SupportCardDef] = []
    seen = set()
    for card in sorted(SUPPORT_CARD_DEFS, key=lambda c: (c.attribute, c.stat_type, c.bonus_pct)):
        family = support_card_family_key(card.support_card_id)
        if family in seen:
            continue
        seen.add(family)
        stage_one.append(card)
    return stage_one


def random_support_card() -> SupportCardDef:
    return RNG.choice(support_card_stage_one_defs())


def add_random_support_card_to_user(user_id: int, *, obtained_at: Optional[str] = None) -> SupportCardDef:
    card = random_support_card()
    db.add_support_card_to_inventory(user_id, canonical_support_card_id(card.support_card_id), obtained_at=obtained_at)
    return card


def get_current_support_card_def_for_user(user_id: int, saved_support_card_id: str) -> Optional[SupportCardDef]:
    family = support_card_family_key(saved_support_card_id)
    row = db.get_support_card_instance_by_family(user_id, family)
    if row is not None:
        current = get_support_card_def(str(row["support_card_id"]))
        if current is not None:
            return current
    # The player no longer owns this card (e.g. sold it on the market). It used to keep
    # working from the saved deck anyway, so a card could be sold and still used.
    return None


def award_support_deck_battle_exp(user_id: int, deck_row: Optional[sqlite3.Row], won_battle: bool) -> Tuple[int, int, int]:
    # Support cards no longer gain battle EXP.
    return 0, 0, 0


def support_card_effect_label(card: SupportCardDef) -> str:
    if str(card.stat_type).upper() == "SPECIAL":
        return "Special Effect"
    return f"{card.attribute} +{card.bonus_pct}% {card.stat_type}"


def support_card_compact_label(card: SupportCardDef) -> str:
    if str(card.stat_type).upper() == "SPECIAL":
        return str(card.attribute)
    return f"{card.attribute} • +{card.bonus_pct}% {card.stat_type}"


def support_card_summary(cards: Sequence[SupportCardDef]) -> str:
    return "\n".join(f"• **{card.name}** — {support_card_effect_label(card)}" for card in cards) if cards else "None"
