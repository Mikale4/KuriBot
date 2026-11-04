"""Player setting options: battle speed and notification choices."""

from __future__ import annotations

from typing import Optional


BATTLE_SPEED_LABELS = {
    "normal": "Normal",
    "fast": "Fast",
    "very_fast": "Very Fast",
    "instant": "Instant",
}
BATTLE_SPEED_DESCRIPTIONS = {
    "normal": "Refresh every turn",
    "fast": "Refresh every 2 turns",
    "very_fast": "Refresh every round",
    "instant": "Skip to final result",
}

NOTIFICATION_SETTING_LABELS = {
    "on": "ON",
    "off": "OFF",
}
NOTIFICATION_SETTING_DESCRIPTIONS = {
    "on": "Kuribot can DM you when ;ping affects you",
    "off": "Kuribot will not DM you for ;ping notifications",
}
PING_DM_SETTINGS_FOOTER = "If you'd like these notifications turned off, open `;settings` and set **Notifications** to **OFF**."


def battle_speed_label(speed_name: str) -> str:
    return BATTLE_SPEED_LABELS.get(str(speed_name or "normal").strip().lower(), "Normal")


def should_refresh_battle(speed_name: str, turn_number: int, round_completed: bool, battle_over: bool) -> bool:
    normalized = str(speed_name or "normal").strip().lower()
    if battle_over:
        return True
    if normalized == "instant":
        return False
    if normalized == "very_fast":
        return round_completed
    if normalized == "fast":
        return turn_number % 2 == 0
    return True


def battle_refresh_status_text(mode_label: str, enemy_name: str, speed_name: str, turn_number: Optional[int] = None) -> str:
    speed_label = battle_speed_label(speed_name)
    if turn_number is None:
        return f"⚔️ {mode_label} • {enemy_name} duel in progress... ({speed_label})"
    return f"⚔️ {mode_label} • {enemy_name} duel in progress... Turn {turn_number} ({speed_label})"


def notification_status_label(enabled: bool) -> str:
    return "ON" if enabled else "OFF"
