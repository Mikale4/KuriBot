"""Equip spell definitions."""

from __future__ import annotations

import re
import uuid
from typing import List, Optional

from kuribot.config import RNG
from kuribot.models import EquipSpellDef


EQUIP_SPELL_DEFS: List[EquipSpellDef] = []


def _slugify_equip_spell_name(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    return normalized or f"equip_spell_{uuid.uuid4().hex[:8]}"


def build_equip_spell_defs() -> List[EquipSpellDef]:
    specs = [
        ("Axe of Despair", "The equipped monster deals 50% more damage."),
        ("Rainbow Veil", "The equipped monster takes 25% less damage from all sources."),
        ("Malevolent Nuzzler", "At the start of battle, the equipped monster gains LIFESTEAL."),
        ("Imperial Iron Wall", "At the start of battle, grant the equipped monster IMMUNITY."),
        ("Battle Mania", "At the start of battle, the equipped monster casts TAUNT on all enemies."),
        ("Monster Reborn", "Once per battle, if the equipped monster is knocked out, revive it with 100% max HP."),
        ("Solemn Wishes", "When the equipped monster takes damage, restore HP equal to 50% of that damage."),
        ("Poison of the Old Man", "The equipped monster's attacks have a 35% chance to inflict POISON."),
        ("Hinotama", "The equipped monster's attacks have a 35% chance to inflict BURN."),
        ("Frozen Soul", "The equipped monster's attacks have a 35% chance to inflict FREEZE for 1 turn."),
        ("Thunder Vassal", "The equipped monster's attacks have a 35% chance to inflict STUN for 1 turn."),
        ("Curse of Fiend", "The equipped monster's attacks have a 35% chance to inflict PETRIFY for 1 turn."),
        ("Spellbinding Circle", "The equipped monster's attacks have a 35% chance to inflict SLEEP for 1 turn."),
        ("Shadow Spell", "The equipped monster's attacks have a 45% chance to inflict FEAR."),
        ("Brain Control", "The equipped monster's attacks have a 45% chance to inflict CHARM."),
        ("Cursed Seal of the Forbidden Spell", "The equipped monster's attacks have a 55% chance to inflict SILENCE."),
        ("Forbidden Chalice", "Enemies damaged by the equipped monster receive HEAL BLOCK."),
        ("Bad Reaction to Simochi", "At the start of battle, all enemies receive HEAL BLOCK."),
        ("Trap Jammer", "At the start of battle, grant all allies IMMUNITY."),
        ("Nutrient Z", "Once per battle, if the equipped monster drops below 25% HP, heal it to 100% HP."),
        ("Mirror Force", "At the start of battle, the equipped monster gains REFLECT."),
        ("Fairy Box", "At the start of the equipped monster's turn, the equipped monster has a 50% chance to gain REFLECT."),
        ("Megamorph", "The equipped monster deals 50% more damage to enemies below 50% HP."),
        ("Rain of Mercy", "Once per battle, when an ally's HP falls below 25% by any means, they gain RECOVERY."),
        ("Blast with Chain", "The equipped monster's attacks have a 55% chance to place a BOMB."),
        ("Time Bomb", "At the start of battle, place a BOMB on all enemies. Bombs explode after a random 1 to 12 monster turns, deal damage equal to the caster's ATK when applied, ignore defense, and then inflict STUN for 1 turn."),
        ("Dust Tornado", "At the start of the equipped monster's turn, remove 1 debuff from it."),
        ("Harpie's Feather Duster", "At the start of the equipped monster's turn, remove 1 debuff from all allies."),
        ("Noble Arms - Caliburn", "At the start of battle, grant the equipped monster RECOVERY."),
        ("Dian Keto the Cure Master", "Once per round, when an ally becomes BURNED or POISONED, remove that debuff and heal them for 25% max HP."),
        ("Rising Energy", "Each time the equipped monster takes damage, it gains 25% increased damage, stacking up to 100%."),
        ("Last Turn", "While below 50% HP, the equipped monster deals 50% more damage."),
        ("Stronghold the Moving Fortress", "While above 50% HP, the equipped monster takes 50% less damage."),
        ("Lightforce Sword", "At the start of battle, the equipped monster gains PRECOG."),
        ("Nobleman of Crossout", "At the start of the equipped monster's turn, the equipped monster has a 50% chance to gain PRECOG."),
        ("Limiter Removal", "At the start of battle, the equipped monster gains 250% ATK for 1 round. After the round is over it takes 50% max HP damage and is STUNNED for 1 turn."),
        ("Ojama Trio", "Once per round, the first enemy that attacks the equipped monster is inflicted with a BOMB."),
        ("Ring of Destruction", "When the equipped monster is knocked out, inflict STUN on all enemies for 1 turn."),
        ("Torrential Tribute", "When the equipped monster is knocked out, deal damage equal to 25% of its max HP to all enemies."),
        ("Draining Shield", "When the equipped monster is knocked out, heal all allies by 25% max HP."),
        ("Rainbow Life", "Once per battle, if the equipped monster would take lethal damage, it survives at 1 HP and gains PROTECTION for 2 rounds."),
        ("Return from the Different Dimension", "Once per battle, when the equipped monster is knocked out, immediately revive it with 50% HP and grant it IMMUNITY."),
        ("Dark Room of Nightmare", "The equipped monster deals 5% more damage to enemies suffering from a debuff."),
        ("Curse of Darkness", "The equipped monster's attacks have a 35% chance to inflict MARK."),
        ("Waboku", "Allies take 25% less damage while the equipped monster is alive."),
        ("Spell of Pain", "The equipped monster takes 50% of damage redirected from the ally with the lowest HP."),
        ("Wave-Motion Cannon", "The equipped monster gains +25% DEF. When the equipped monster is attacked, it gains 1 charge stack. Once it reaches 10 charge stacks, it explodes, KOs the equipped monster, and deals damage to all enemies equal to 125% of the equipped monster's max HP. As long as the equipped monster is alive, all enemies are TAUNTED."),
        ("Swords of Revealing Light", "At the start of battle, 35% chance (individually) to inflict STUN on all enemies for 1 turn."),
        ("Dark Sanctuary", "At the start of battle, inflict SLEEP on all enemies for 1 turn."),
        ("Nightmare Wheel", "At the start of battle, inflict FEAR on all enemies."),
        ("Mask of Restrict", "Healing received by enemies is reduced by 50% while the equipped monster is alive."),
        ("Skull Invitation", "Enemies under 50% HP cannot be healed."),
        ("Soul Taker", "At the start of the equipped monster's turn, 50% chance to cast HEAL BLOCK on a random enemy."),
        ("My Body as a Shield", "At the start of battle, grant the equipped monster a SHIELD equal to 25% of its max HP."),
        ("Scrap-Iron Scarecrow", "At the start of battle, grant all allies a shield equal to 25% of the equipped monster's max HP."),
        ("Mystical Space Typhoon", "The equipped monster's attacks have a 35% chance to apply BURN, POISON, FEAR, or SILENCE."),
        ("Double Attack", "When the equipped monster attacks, it has a 25% chance to attack again."),
        ("Twin Swords of Flashing Light - Tryce", "The equipped monster's attacks hit twice, with the second hit dealing 50% damage."),
        ("Forbidden Lance", "The equipped monster is immune to STUN, FREEZE, and SLEEP."),
        ("Forbidden Dress", "The equipped monster is immune to all debuffs."),
        ("Curse Transfer", "When the equipped monster is inflicted with a debuff, the attacker receives the same debuff."),
        ("Sakuretsu Armor", "At the start of battle, the equipped monster gains COUNTER."),
        ("Kunai with Chain", "At the start of the equipped monster's turn, 50% chance to gain COUNTER."),
        ("Burden of the Mighty", "When an ally is knocked out, heal the equipped monster by 25% max HP and increase its damage by 50%."),
        ("Soul Resurrection", "Once per battle, when an ally is knocked out, immediately revive that ally with 50% HP."),
        ("Chain Energy", "Once per round, when the equipped monster is inflicted with a debuff, cast a BOMB on the enemy who inflicted them with that debuff."),
        ("Inferno Tempest", "The equipped monster causes BOMBS to explode immediately when attacking a target that has one."),
        ("Gift of the Mystical Elf", "At the start of battle, grant all allies RECOVERY. RECOVERY heals 5% max HP at the end of every monster's turn."),
        ("Dark Snake Syndrome", "At the start of each enemy turn, enemies suffering from debuffs take 5% max HP damage."),
        ("Blast Held by a Tribute", "Once per battle, when the equipped monster drops below 25% HP, place a BOMB on all enemies and grant itself PROTECTION for 2 rounds."),
        ("Heavy Storm", "At the start of battle, remove all removable buffs from all enemies."),
        ("Galaxy Cyclone", "At the start of the equipped monster's turn, remove 1 removable buff from the enemy with the most buffs."),
        ("Typhoon", "When the equipped monster takes damage, remove 1 removable buff from the attacker."),
        ("Night Beam", "At the start of the equipped monster's turn, remove 1 random removable buff from all enemies."),
        ("Spell Shattering Arrow", "When the equipped monster attacks, it has a 55% chance to remove 1 removable buff from the target."),
        ("Book of Moon", "Once per round, when targeted by an attack, 35% chance to apply SLEEP to the attacker before damage."),
        ("Book of Eclipse", "At the start of battle, apply SLEEP to all enemies for 2 turns, but sleeping enemies gain IMMUNITY after they wake."),
        ("Swords of Concealing Light", "Once per round, when targeted by an attack, 35% chance to apply FEAR to the attacker before damage."),
        ("Messenger of Peace", "The equipped monster takes 75% less damage, but its own damage dealt is reduced by 75%."),
        ("Spell Absorption", "Whenever any monster gains a buff, heal the equipped monster for 10% max HP."),
        ("Mystik Wok", "At the start of battle, sacrifice 25% HP to gain PROTECTION for 1 round and +50% damage for 1 round."),
        ("Disarmament", "As long as the equipped monster is alive, all other equip spells are disabled."),
        ("Dark Ruler No More", "At the start of battle, destroy any active Support Card and/or Equip Spell that has an effect to disable other Support Cards or Equip Spells. If any cards are destroyed this way, destroy the equipped monster."),
        ("Drastic Drop Off", "At the start of the equipped monster's turn, remove all SHIELDS and PROTECTION buffs from every monster (ally/enemy)."),
        ("Book of Taiyou", "When attacking a SLEEPING/FROZEN/STUNNED/PETRIFIED target, gain +100% crit rate for that hit."),
        ("Diffusion Wave-Motion", "Attacks cause splash damage to other enemies for 25% of the damage dealt."),
        ("Dimension Magic", "Once per battle, when the equipped monster would take lethal damage, transfer that damage to a random ally instead then gain PRECOG."),
        ("Enemy Controller", "When the equipped monster attacks, 35% chance to apply TAUNT to the target."),
        ("Soul Exchange", "When the equipped monster defeats an enemy, heal self and lowest-HP ally by 25% of that enemy's max HP."),
        ("Shrink", "At the start of battle, inflict MARK on the highest-ATK enemy."),
        ("Rush Recklessly", "The equipped monster gains +25% crit and +50% crit damage."),
        ("Darklord Enchantment", "When struck by a critical hit, inflict CHARM the attacker."),
        ("Book of Secret Arts", "At the start of battle, if the equipped monster is a Spellcaster, Black Magic, White Magic, Illusion Magic, or Dragon Magic type, gain IMMUNITY plus attacks have a 55% chance to inflict SILENCE."),
        ("Super Rush Headlong", "Attacks by the equipped monster always land as type advantage, regardless of the type matchup."),
        ("Dangerous Machine Type-6", "At the start of the equipped monster's turn, 1 random ally and 1 random enemy receive a random 1-turn buff or debuff."),
        ("There Can Be Only One", "While the equipped monster is alive, if 2 or more monsters on a side share a type, only 1 random one of them can act each turn."),
        ("Bottomless Trap Hole", "At the start of battle, 1 random enemy with more than 150 ATK loses 50% HP."),
        ("Trap Hole", "At the start of battle, 1 random enemy with less than 125 ATK loses 25% HP."),
        ("Floodgate Trap Hole", "At the start of battle, 1 random enemy with more than 175 ATK cannot act for 1 round."),
        ("Delinquent Duo", "At the start of battle, you lose 2 hearts; discard 1 random Special Support Card from the opponent's hand."),
        ("Raigeki", "At the start of battle, 10% chance to destroy 1 random enemy monster."),
        ("Clockwork Night", "While the equipped monster is alive, all monsters become Machine type. Monsters that were already Machine gain +25% ATK."),
        ("Solidarity", "The equipped monster deals double damage to targets that share its attribute or type."),
        ("Ground Collapse", "At the start of battle, 1 random ally and 1 random enemy cannot act for 1 round."),
        ("Morale Boost", "Restore 1 heart if an enemy monster is knocked out."),
        ("Vengeful Bog Spirit", "At the start of battle, all monsters cannot act for 1 round."),
        ("Hippo Carnival", "At the start of battle, grant a SHIELD to the ally with the lowest HP. Monsters with SHIELD or TOKEN must be targeted first."),
        ("Rose Bell of Revelation", "If this equip spell is destroyed or disabled, the equipped monster gains +100% ATK."),
        ("Attack and Receive", "While the equipped monster is alive, your opponent loses 1 heart whenever you lose 1 heart except by knockout."),
        ("Venom Shot", "At the start of battle, POISON all enemies."),
        ("Tremendous Fire", "At the start of battle, BURN all enemies."),
        ("Time Seal", "As long as the equipped monster is alive, speed order is reversed."),
        ("Warning Point", "If the equipped monster dodges an attack, 45% chance to STUN the attacker."),
        ("Lucky Iron Axe", "The equipped monster gains +5% DODGE, +10% ACCURACY, and +5% CRIT RATE."),
        ("Top Share", "At the start of battle, 1 support card drawn for both sides becomes a random Special Support Card from their own deck, if possible."),
        ("Magic Cylinder", "Once per battle, when the equipped monster is threatened with lethal damage by an attack, redirect the attack to a random enemy at 150% of the damage."),
    ]
    return [EquipSpellDef(equip_spell_id=_slugify_equip_spell_name(name), name=name, description=description) for name, description in specs]


def init_equip_spells():
    if not EQUIP_SPELL_DEFS:
        EQUIP_SPELL_DEFS[:] = build_equip_spell_defs()  # in place: other modules hold this list


def get_equip_spell_def(equip_spell_id: str) -> Optional[EquipSpellDef]:
    init_equip_spells()
    for spell in EQUIP_SPELL_DEFS:
        if spell.equip_spell_id == str(equip_spell_id):
            return spell
    return None


def random_equip_spell() -> EquipSpellDef:
    init_equip_spells()
    return RNG.choice(EQUIP_SPELL_DEFS)


