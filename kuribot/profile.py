"""Profile showcase and background uploads, and their moderation."""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

import discord
from PIL import Image, ImageOps

from kuribot.config import (
    BASE_DIR, PROFILE_TAG_MODERATION_CHANNEL_IDS, PROFILE_TAG_SIZE, PROFILE_TAGS_APPROVED_DIR,
    PROFILE_TAGS_PENDING_DIR,
)
from kuribot.core import bot, is_bot_creator
from kuribot.database import db
from kuribot.render.images import invalidate_rgba_image_cache
from kuribot.mail import format_mail_timestamp


def wipe_profile_tag_assets() -> None:
    for folder in (PROFILE_TAGS_PENDING_DIR, PROFILE_TAGS_APPROVED_DIR):
        folder.mkdir(parents=True, exist_ok=True)
        for child in folder.iterdir():
            try:
                if child.is_file() or child.is_symlink():
                    child.unlink()
                elif child.is_dir():
                    shutil.rmtree(child)
            except Exception:
                continue


def normalize_profile_tag_image(raw_bytes: bytes, size: Tuple[int, int] = PROFILE_TAG_SIZE) -> bytes:
    with Image.open(io.BytesIO(raw_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        fitted = ImageOps.fit(image, size, method=Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        fitted.save(buffer, format="PNG", compress_level=3)
        return buffer.getvalue()


def load_profile_tag_image(user_id: int, size: Tuple[int, int] = PROFILE_TAG_SIZE) -> Optional[Image.Image]:
    raw_path = db.get_tag_image_path(user_id)
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.exists():
        return None
    try:
        with Image.open(path) as source:
            image = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGBA"), size, method=Image.Resampling.LANCZOS)
            return image.copy()
    except Exception:
        return None


def get_profile_tag_request_guild(row: sqlite3.Row) -> Optional[discord.Guild]:
    guild_id = int(row["guild_id"] or 0)
    if guild_id <= 0:
        return None
    return bot.get_guild(guild_id)


def is_profile_tag_moderator(user: discord.abc.User, row: Optional[sqlite3.Row]) -> bool:
    if is_bot_creator(user):
        return True
    guild_permissions = getattr(user, "guild_permissions", None)
    if guild_permissions and getattr(guild_permissions, "administrator", False):
        return True
    if row is None:
        return False
    guild = get_profile_tag_request_guild(row)
    if guild is None:
        return False
    member = guild.get_member(int(getattr(user, "id", 0)))
    return bool(member and getattr(member.guild_permissions, "administrator", False))


def parse_admin_message_ids(row: Optional[sqlite3.Row]) -> List[int]:
    if not row:
        return []
    try:
        raw = json.loads(str(row["admin_message_ids_json"] or "[]"))
    except Exception:
        return []
    result: List[int] = []
    for value in raw if isinstance(raw, list) else []:
        try:
            result.append(int(value))
        except Exception:
            continue
    return result


# Profile background uploads (unlocked in the Customization shop).
PROFILE_BACKGROUNDS_DIR = BASE_DIR / "profile_backgrounds"
PROFILE_BACKGROUNDS_PENDING_DIR = PROFILE_BACKGROUNDS_DIR / "pending"
PROFILE_BACKGROUNDS_APPROVED_DIR = PROFILE_BACKGROUNDS_DIR / "approved"
PROFILE_BACKGROUND_MAX_SIZE = (1400, 1400)
for _dir in (PROFILE_BACKGROUNDS_DIR, PROFILE_BACKGROUNDS_PENDING_DIR, PROFILE_BACKGROUNDS_APPROVED_DIR):
    _dir.mkdir(parents=True, exist_ok=True)


def _ensure_background_customization_schema():
    try:
        user_columns = {row[1] for row in db.conn.execute("PRAGMA table_info(users)").fetchall()}
        if "background_image_path" not in user_columns:
            db.conn.execute("ALTER TABLE users ADD COLUMN background_image_path TEXT NOT NULL DEFAULT ''")
        if "background_unlocked" not in user_columns:
            db.conn.execute("ALTER TABLE users ADD COLUMN background_unlocked INTEGER NOT NULL DEFAULT 0")
        tag_columns = {row[1] for row in db.conn.execute("PRAGMA table_info(profile_tag_uploads)").fetchall()}
        if "upload_type" not in tag_columns:
            db.conn.execute("ALTER TABLE profile_tag_uploads ADD COLUMN upload_type TEXT NOT NULL DEFAULT 'showcase'")
        db.conn.commit()
    except Exception:
        pass


def profile_customization_upload_type(row: Optional[sqlite3.Row]) -> str:
    raw = str((row["upload_type"] if row and "upload_type" in row.keys() else "showcase") or "showcase").strip().lower()
    return raw if raw in {"showcase", "background"} else "showcase"


def profile_customization_label(upload_type: str) -> str:
    return "Background" if str(upload_type).strip().lower() == "background" else "Showcase"


def normalize_profile_background_image(raw_bytes: bytes, size: Tuple[int, int] = PROFILE_BACKGROUND_MAX_SIZE) -> bytes:
    with Image.open(io.BytesIO(raw_bytes)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")
        image.thumbnail(size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", compress_level=3)
        return buffer.getvalue()


async def send_profile_tag_moderation_request(request_id: str) -> int:
    from kuribot.ui.profile import ProfileTagModerationView
    row = db.get_profile_tag_upload(request_id)
    if not row or str(row["status"]).lower() != "pending":
        return 0
    existing_admin_message_ids = parse_admin_message_ids(row)
    if existing_admin_message_ids:
        return 0
    pending_path = Path(str(row["pending_image_path"]))
    if not pending_path.exists():
        return 0
    user = bot.get_user(int(row["user_id"]))
    if user is None:
        try:
            user = await bot.fetch_user(int(row["user_id"]))
        except Exception:
            user = None
    guild = get_profile_tag_request_guild(row)
    guild_label = getattr(guild, "name", "Direct Message") if guild is not None else "Direct Message"
    upload_type = profile_customization_upload_type(row)
    label = profile_customization_label(upload_type)
    target_text = "the background outer panel shown on the bot's rendered images" if upload_type == "background" else "the bottom-left Showcase slot on the user's profile"
    content = (
        f"Profile customization moderation request **{request_id}**\n"
        f"User: **{getattr(user, 'display_name', 'Unknown User')}** (`{row['user_id']}`)\n"
        f"Type: **{label}**\n"
        f"File: **{row['original_filename'] or 'upload.png'}**\n"
        f"Submitted: **{format_mail_timestamp(row['submitted_at'])}**\n"
        f"Approve or deny this image for {target_text}.\n"
        f"Guild: **{guild_label}**"
    )
    sent_message_ids: List[int] = []
    sent_count = 0
    for channel_id in PROFILE_TAG_MODERATION_CHANNEL_IDS:
        try:
            moderation_channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
        except Exception:
            continue
        try:
            message = await moderation_channel.send(
                content,
                file=discord.File(str(pending_path), filename=f"{upload_type}.png"),
                view=ProfileTagModerationView(request_id),
            )
        except Exception:
            continue
        sent_message_ids.append(int(message.id))
        sent_count += 1
    if not sent_message_ids:
        return 0
    db.set_profile_tag_admin_message_ids(request_id, sent_message_ids)
    return sent_count


async def notify_profile_tag_result(user_id: int, accepted: bool, upload_type: str = "showcase") -> bool:
    user = bot.get_user(int(user_id))
    if user is None:
        try:
            user = await bot.fetch_user(int(user_id))
        except Exception:
            return False
    label = profile_customization_label(upload_type)
    try:
        dm = user.dm_channel or await user.create_dm()
        if accepted:
            if upload_type == "background":
                await dm.send("Your profile **Background** image was **accepted** and has been applied to your rendered panels across the bot.")
            else:
                await dm.send("Your profile **Showcase** image was **accepted** and has been applied to your profile.")
        else:
            await dm.send(f"Your profile **{label}** image was **denied** by the admins. You can upload a different one from Customize.")
        return True
    except Exception:
        return False


async def finalize_profile_tag_request(request_id: str, accepted: bool, moderator: Optional[discord.abc.User] = None) -> Tuple[bool, str]:
    row = db.get_profile_tag_upload(request_id)
    if not row:
        return False, "That moderation request no longer exists."
    if str(row["status"]).lower() != "pending":
        existing = str(row["status"]).strip().lower()
        return False, f"This request was already {existing}."
    upload_type = profile_customization_upload_type(row)
    label = profile_customization_label(upload_type)
    pending_path = Path(str(row["pending_image_path"]))
    approved_dir = PROFILE_BACKGROUNDS_APPROVED_DIR if upload_type == "background" else PROFILE_TAGS_APPROVED_DIR
    approved_path = approved_dir / f"{int(row['user_id'])}.png"
    if accepted:
        if not pending_path.exists():
            return False, "The uploaded image file could not be found."
        approved_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pending_path, approved_path)
        invalidate_rgba_image_cache()
        if not db.resolve_profile_tag_upload(request_id, "accepted", decided_by=int(getattr(moderator, "id", 0) or 0), approved_image_path=str(approved_path)):
            return False, "This request was already handled."
        if upload_type == "background":
            db.set_background_image_path(int(row["user_id"]), str(approved_path))
        else:
            db.set_tag_image_path(int(row["user_id"]), str(approved_path))
        try:
            pending_path.unlink(missing_ok=True)
        except Exception:
            pass
        await notify_profile_tag_result(int(row["user_id"]), True, upload_type)
        return True, f"Accepted {label.lower()} image for user `{row['user_id']}`."
    try:
        pending_path.unlink(missing_ok=True)
    except Exception:
        pass
    if not db.resolve_profile_tag_upload(request_id, "denied", decided_by=int(getattr(moderator, "id", 0) or 0), approved_image_path=""):
        return False, "This request was already handled."
    await notify_profile_tag_result(int(row["user_id"]), False, upload_type)
    return True, f"Denied {label.lower()} image for user `{row['user_id']}`."
