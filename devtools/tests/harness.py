"""Shared setup for the offline checks (battle_check.py, menu_check.py).

Importing this module:
- copies the live database into devtools/sandbox/check-<name>.sqlite3 (a consistent snapshot,
  safe while the bot runs) and points the bot at that copy,
- loads the whole bot (kuribot.app.load()),
- exposes every bot name as `k` (for example k.db, k.start_campaign_stage_battle),
- provides simulated Discord objects that check every message against Discord's size limits.

Nothing here connects to Discord or touches the real database.
"""
from __future__ import annotations

import asyncio
import collections
import importlib
import io
import os
import pkgutil
import sqlite3
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SANDBOX = ROOT / "devtools" / "sandbox"
# One sandbox file per check, so battle_check and menu_check can run at the same time.
CHECK_DB = SANDBOX / f"check-{Path(sys.argv[0]).stem or 'adhoc'}.sqlite3"


def _prepare_database() -> None:
    SANDBOX.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(CHECK_DB) + suffix)
        if path.exists():
            path.unlink()
    live = ROOT / "kuribot.sqlite3"
    destination = sqlite3.connect(str(CHECK_DB))
    if live.exists():
        source = sqlite3.connect(f"file:{live.as_posix()}?mode=ro", uri=True)
        source.backup(destination)
        source.close()
    else:
        print("No kuribot.sqlite3 found - the checks run on an empty database.")
    destination.close()


_prepare_database()
os.environ["KURIBOT_DB_PATH"] = str(CHECK_DB)
sys.path.insert(0, str(ROOT))

import discord  # noqa: E402
from PIL import Image  # noqa: E402

import kuribot  # noqa: E402
import kuribot.app  # noqa: E402

kuribot.app.load()


def _all_bot_names() -> types.SimpleNamespace:
    names = {}
    for info in pkgutil.walk_packages(kuribot.__path__, "kuribot."):
        if info.name == "kuribot.__main__":
            continue
        module = importlib.import_module(info.name)
        for key, value in vars(module).items():
            if key.startswith("__"):
                continue
            if key not in names or getattr(value, "__module__", None) == module.__name__:
                names[key] = value
    return types.SimpleNamespace(**names)


k = _all_bot_names()

# Battles and menus wait between animation frames; the checks don't need to.
_real_sleep = asyncio.sleep


async def _no_wait(delay=0, result=None):
    return await _real_sleep(0, result)


asyncio.sleep = _no_wait

_png = io.BytesIO()
Image.new("RGBA", (64, 64), (200, 50, 50, 255)).save(_png, "PNG")
PNG = _png.getvalue()

LIMIT_PROBLEMS: list = []  # (where, problem) - things Discord would reject
SENT_VIEWS: list = []  # (where, owner_id, view, message) - menus the bot sent, for clicking through


def check_payload(where: str, kw: dict) -> None:
    """Record anything in a send/edit that Discord would reject."""
    files = []
    if kw.get("file") is not None:
        files.append(kw["file"])
    files += list(kw.get("files") or [])
    files += [a for a in (kw.get("attachments") or []) if isinstance(a, discord.File)]
    for f in files:
        f.reset(seek=0)
        if not f.fp.read():
            LIMIT_PROBLEMS.append((where, f"empty file upload {f.filename}"))
        f.close()
    content = kw.get("content")
    if content is None and kw.get("_args"):
        content = kw["_args"][0]
    if content is not None and len(str(content)) > 2000:
        LIMIT_PROBLEMS.append((where, f"message text {len(str(content))} chars > 2000"))
    for embed in ([kw["embed"]] if kw.get("embed") else []) + list(kw.get("embeds") or []):
        d = embed.to_dict()
        total = len(d.get("title", "") or "") + len(d.get("description", "") or "")
        if len(d.get("title", "") or "") > 256:
            LIMIT_PROBLEMS.append((where, "embed title > 256"))
        if len(d.get("description", "") or "") > 4096:
            LIMIT_PROBLEMS.append((where, "embed description > 4096"))
        if len(d.get("fields", [])) > 25:
            LIMIT_PROBLEMS.append((where, f"embed has {len(d['fields'])} fields > 25"))
        for fld in d.get("fields", []):
            total += len(fld["name"]) + len(fld["value"])
            if len(fld["value"]) > 1024:
                LIMIT_PROBLEMS.append((where, f"embed field '{fld['name'][:30]}' value > 1024"))
            if len(fld["name"]) > 256:
                LIMIT_PROBLEMS.append((where, "embed field name > 256"))
            if not fld["value"].strip() or not fld["name"].strip():
                LIMIT_PROBLEMS.append((where, f"empty embed field '{fld['name'][:30]}'"))
        total += len((d.get("footer") or {}).get("text", ""))
        if total > 6000:
            LIMIT_PROBLEMS.append((where, f"embed total {total} > 6000"))
    view = kw.get("view")
    if isinstance(view, discord.ui.View):
        try:
            view.to_components()
        except Exception as exc:
            LIMIT_PROBLEMS.append((where, f"menu can't be sent: {exc!r}"))
        if len(view.children) > 25:
            LIMIT_PROBLEMS.append((where, f"menu has {len(view.children)} items > 25"))
        for item in view.children:
            if isinstance(item, discord.ui.Select):
                if not item.options and item.type == discord.ComponentType.string_select:
                    LIMIT_PROBLEMS.append((where, f"dropdown '{item.placeholder}' has no options"))
                if len(item.options) > 25:
                    LIMIT_PROBLEMS.append((where, f"dropdown '{item.placeholder}' has {len(item.options)} options > 25"))
                values = [o.value for o in item.options]
                if len(values) != len(set(values)):
                    LIMIT_PROBLEMS.append((where, f"dropdown '{item.placeholder}' has duplicate values"))
                for o in item.options:
                    if len(o.label) > 100 or len(o.value) > 100 or len(o.description or "") > 100:
                        LIMIT_PROBLEMS.append((where, f"dropdown option text > 100: '{o.label[:40]}'"))
                if item.options and item.max_values > len(item.options):
                    LIMIT_PROBLEMS.append((where, f"dropdown '{item.placeholder}' max_values > options"))
            if isinstance(item, discord.ui.Button) and item.label and len(item.label) > 80:
                LIMIT_PROBLEMS.append((where, f"button label > 80: {item.label[:40]}"))
            if getattr(item, "custom_id", None) and len(item.custom_id) > 100:
                LIMIT_PROBLEMS.append((where, f"custom_id > 100: {item.custom_id[:40]}"))


def _remember_view(where: str, owner_id: int, kw: dict, message) -> None:
    if isinstance(kw.get("view"), discord.ui.View):
        SENT_VIEWS.append((where, owner_id, kw["view"], message))


class FakeAsset:
    url = "https://example.invalid/a.png"
    key = "abc"

    def replace(self, **kw):
        return self

    def with_size(self, *a):
        return self

    def with_format(self, *a):
        return self

    async def read(self):
        return PNG


class FakeUser:
    def __init__(self, uid: int):
        self.id = uid
        self.name = f"user{uid}"
        self.display_name = self.name
        self.global_name = self.name
        self.mention = f"<@{uid}>"
        self.bot = False
        self.display_avatar = FakeAsset()
        self.avatar = FakeAsset()
        self.guild_permissions = discord.Permissions.none()
        self.roles = []

    async def send(self, *a, **kw):
        kw["_args"] = a
        check_payload("user.send", kw)
        return FakeMessage(None, self.id)

    def __str__(self):
        return self.name


class FakeGuild:
    id = 999
    name = "Test Guild"
    members: list = []

    def get_member(self, uid):
        return FakeUser(uid)

    async def fetch_member(self, uid):
        return FakeUser(uid)


GUILD = FakeGuild()


class FakeChannel:
    def __init__(self, owner_id: int):
        self.id = k.DEBUG_ALLOWED_CHANNEL_ID
        self.guild = GUILD
        self.owner_id = owner_id
        self.type = discord.ChannelType.text

    async def send(self, *a, **kw):
        kw["_args"] = a
        check_payload("channel.send", kw)
        message = FakeMessage(self, self.owner_id)
        _remember_view("channel.send", self.owner_id, kw, message)
        return message

    def typing(self):
        class _Typing:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        return _Typing()

    async def fetch_message(self, mid):
        return FakeMessage(self, self.owner_id)


class FakeMessage:
    _next_id = 1000

    def __init__(self, channel, owner_id: int):
        FakeMessage._next_id += 1
        self.id = FakeMessage._next_id
        self.channel = channel or FakeChannel(owner_id)
        self.owner_id = owner_id
        self.attachments = []
        self.embeds = []
        self.content = ""
        self.guild = GUILD
        self.author = FakeUser(1)
        self.mentions = []

    async def edit(self, **kw):
        check_payload("message.edit", kw)
        _remember_view("message.edit", self.owner_id, kw, self)
        return self

    async def delete(self, *a, **kw):
        pass

    async def reply(self, *a, **kw):
        kw["_args"] = a
        check_payload("message.reply", kw)
        return FakeMessage(self.channel, self.owner_id)

    async def add_reaction(self, *a):
        pass


class FakeResponse:
    def __init__(self, interaction):
        self._done = False
        self.interaction = interaction

    def is_done(self):
        return self._done

    async def defer(self, **kw):
        if self._done:
            raise discord.InteractionResponded(self.interaction)
        self._done = True

    async def send_message(self, *a, **kw):
        if self._done:
            raise discord.InteractionResponded(self.interaction)
        kw["_args"] = a
        check_payload("response.send_message", kw)
        self._done = True
        _remember_view("response.send_message", self.interaction.user.id, kw, self.interaction.message)

    async def edit_message(self, **kw):
        if self._done:
            raise discord.InteractionResponded(self.interaction)
        check_payload("response.edit_message", kw)
        self._done = True
        _remember_view("response.edit_message", self.interaction.user.id, kw, self.interaction.message)

    async def send_modal(self, modal):
        if self._done:
            raise discord.InteractionResponded(self.interaction)
        self._done = True


class FakeFollowup:
    def __init__(self, interaction):
        self.interaction = interaction

    async def send(self, *a, **kw):
        kw.pop("wait", None)
        kw["_args"] = a
        check_payload("followup.send", kw)
        message = FakeMessage(self.interaction.channel, self.interaction.user.id)
        _remember_view("followup.send", self.interaction.user.id, kw, message)
        return message


class FakeInteraction:
    def __init__(self, uid: int, message=None):
        self.user = FakeUser(uid)
        self.channel = message.channel if message else FakeChannel(uid)
        self.message = message or FakeMessage(self.channel, uid)
        self.response = FakeResponse(self)
        self.followup = FakeFollowup(self)
        self.guild = GUILD
        self.guild_id = GUILD.id
        self.channel_id = self.channel.id
        self.id = 1
        self.client = k.bot
        self.data = {}
        self.type = discord.InteractionType.component

    async def original_response(self):
        return self.message

    async def edit_original_response(self, **kw):
        check_payload("edit_original_response", kw)
        _remember_view("edit_original_response", self.user.id, kw, self.message)
        return self.message


class FakeContext:
    def __init__(self, uid: int, command):
        self.author = FakeUser(uid)
        self.channel = FakeChannel(uid)
        self.guild = GUILD
        self.message = FakeMessage(self.channel, uid)
        self.message.author = self.author
        self.bot = k.bot
        self.command = command
        self.prefix = k.PREFIX
        self.invoked_with = command.name

    async def send(self, *a, **kw):
        kw["_args"] = a
        check_payload("ctx.send", kw)
        message = FakeMessage(self.channel, self.author.id)
        _remember_view("ctx.send", self.author.id, kw, message)
        return message

    async def reply(self, *a, **kw):
        return await self.send(*a, **kw)

    def typing(self):
        return self.channel.typing()


# The bot never logs in here: answer Discord lookups locally, and treat "wait for the
# player's next message" (image uploads, typed answers) as the player not replying.
async def _fake_fetch_user(uid):
    return FakeUser(int(uid))


async def _no_reply(*a, **kw):
    raise asyncio.TimeoutError()


k.bot.fetch_user = _fake_fetch_user
k.bot.wait_for = _no_reply


def summarize_failures(failures: dict) -> int:
    """Print grouped tracebacks; returns the number of failures."""
    total = sum(len(v) for v in failures.values())
    by_last_line = collections.defaultdict(list)
    for label, tracebacks in failures.items():
        for tb in tracebacks:
            by_last_line[tb.strip().splitlines()[-1]].append((label, tb))
    for last, items in sorted(by_last_line.items(), key=lambda kv: -len(kv[1])):
        where = collections.Counter(label for label, _ in items)
        print(f"\n--- x{len(items)}: {last}\n    where: {dict(where.most_common(4))}")
        print("    " + items[0][1].strip()[-1500:].replace("\n", "\n    "))
    return total
