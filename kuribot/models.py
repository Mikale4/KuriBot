"""Core data classes: monsters, fighters, status effects, support cards and equip spells."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from kuribot.config import BASE_ACCURACY, BASE_CRIT_DAMAGE, BASE_CRIT_RATE, BASE_DODGE


@dataclass
class MonsterDef:
    monster_id: str
    name: str
    attribute: str
    hp: int
    atk: int
    defense: int
    spd: int
    image_path: str


class StatusEffect(str, Enum):
    BURN = "BURN"
    POISON = "POISON"
    STUN = "STUN"
    FREEZE = "FREEZE"
    PETRIFY = "PETRIFY"
    TAUNT = "TAUNT"
    FEAR = "FEAR"
    SLEEP = "SLEEP"
    CHARM = "CHARM"
    SILENCE = "SILENCE"


STATUS_BLOCKS_ACTION = {
    StatusEffect.STUN,
    StatusEffect.FREEZE,
    StatusEffect.PETRIFY,
    StatusEffect.SLEEP,
}


@dataclass
class StatusState:
    effect: StatusEffect
    source_monster_id: Optional[str] = None
    source_name: str = ""
    poison_pct: int = 1


@dataclass(eq=False)
class Fighter:
    monster_id: str
    name: str
    attribute: str
    monster_types: Tuple[str, ...]
    max_hp: int
    hp: int
    atk: int
    defense: int
    spd: int
    image_path: str
    crit_rate: float = BASE_CRIT_RATE
    crit_damage: float = BASE_CRIT_DAMAGE
    accuracy: float = BASE_ACCURACY
    dodge: float = BASE_DODGE
    speed_gauge: float = 0.0
    status: Optional[StatusState] = None
    runtime: Dict[str, object] = field(default_factory=dict)

    @property
    def alive(self) -> bool:
        return self.hp > 0


@dataclass
class SupportCardDef:
    support_card_id: str
    name: str
    attribute: str
    bonus_pct: int
    stat_type: str
    description: str


@dataclass
class EquipSpellDef:
    equip_spell_id: str
    name: str
    description: str


@dataclass
class SupportDeckRuntime:
    deck_name: str
    shuffled_cards: List[SupportCardDef] = field(default_factory=list)
    draw_index: int = 0
    active_cards: List[SupportCardDef] = field(default_factory=list)
    rounds_remaining: int = 0
    flags: Dict[str, object] = field(default_factory=dict)

    def has_cards_remaining(self) -> bool:
        return self.draw_index < len(self.shuffled_cards)
