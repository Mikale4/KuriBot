"""Safe interaction helpers shared by every menu (send, edit, acknowledge, files)."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import List, Optional, Sequence

import discord

from kuribot.core import is_debug_send_allowed
from kuribot.discord_helpers import fit_discord_message_content


def disable_view_items(view: discord.ui.View) -> None:
    """discord.py 2.x has no View.disable_all_items(); calling it crashed after rewards were granted."""
    for child in view.children:
        if hasattr(child, "disabled"):
            child.disabled = True


async def safe_acknowledge_interaction(interaction: discord.Interaction, *, ephemeral: bool = False, thinking: bool = False) -> bool:
    try:
        if interaction.response.is_done():
            return True
        await interaction.response.defer(ephemeral=ephemeral, thinking=thinking)
        return True
    except (discord.NotFound, discord.HTTPException):
        return False


def _reset_discord_file_handles(files: Sequence[discord.File]) -> None:
    for current_file in files:
        if current_file is None:
            continue
        try:
            current_file.reset(seek=0)
            continue
        except TypeError:
            try:
                current_file.reset()
                continue
            except Exception:
                pass
        except Exception:
            pass
        try:
            current_file.fp.seek(0)
        except Exception:
            pass


def _new_discord_file(source, *, filename: Optional[str] = None, spoiler: bool = False, description: Optional[str] = None) -> discord.File:
    kwargs = {"filename": filename, "spoiler": bool(spoiler)}
    if description is not None:
        kwargs["description"] = description
    try:
        return discord.File(source, **kwargs)
    except TypeError:
        kwargs.pop("description", None)
        return discord.File(source, **kwargs)


def _discord_file_clone_factory(current_file: discord.File):
    if current_file is None:
        return lambda: None

    filename = getattr(current_file, "filename", None)
    spoiler = bool(getattr(current_file, "spoiler", False))
    description = getattr(current_file, "description", None)
    fp = getattr(current_file, "fp", None)

    raw_name = str(getattr(fp, "name", "") or "")
    if raw_name and Path(raw_name).exists():
        return lambda path=raw_name, filename=filename, spoiler=spoiler, description=description: _new_discord_file(
            path,
            filename=filename,
            spoiler=spoiler,
            description=description,
        )

    try:
        try:
            current_file.reset(seek=0)
        except TypeError:
            current_file.reset()
        data = fp.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        try:
            current_file.reset(seek=0)
        except TypeError:
            current_file.reset()
        return lambda data=bytes(data), filename=filename, spoiler=spoiler, description=description: _new_discord_file(
            io.BytesIO(data),
            filename=filename,
            spoiler=spoiler,
            description=description,
        )
    except Exception:
        return lambda current_file=current_file: current_file


def _discord_file_clone_factories(files: Sequence[discord.File]):
    return [_discord_file_clone_factory(current_file) for current_file in list(files or []) if current_file is not None]


async def safe_send_interaction_message(
    interaction: discord.Interaction,
    *,
    content: Optional[str] = None,
    file: Optional[discord.File] = None,
    files: Optional[Sequence[discord.File]] = None,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
    ephemeral: bool = False,
) -> Optional[discord.Message]:
    content = fit_discord_message_content(content)
    if not is_debug_send_allowed(getattr(interaction, 'channel', None)):
        if not interaction.response.is_done():
            await safe_acknowledge_interaction(interaction, ephemeral=ephemeral)
        return interaction.message
    send_files = list(files) if files is not None else ([] if file is None else [file])

    def _build_send_kwargs(*, wait: bool = False) -> dict:
        kwargs = {"ephemeral": ephemeral}
        if wait:
            kwargs["wait"] = True
        if content is not None:
            kwargs["content"] = content
        if embed is not None:
            kwargs["embed"] = embed
        if view is not None:
            kwargs["view"] = view
        if len(send_files) == 1:
            kwargs["file"] = send_files[0]
        elif send_files:
            kwargs["files"] = send_files
        return kwargs

    try:
        if not interaction.response.is_done():
            _reset_discord_file_handles(send_files)
            await interaction.response.send_message(**_build_send_kwargs())
            try:
                return await interaction.original_response()
            except (discord.NotFound, discord.HTTPException):
                return interaction.message
        _reset_discord_file_handles(send_files)
        return await interaction.followup.send(**_build_send_kwargs(wait=True))
    except (discord.NotFound, discord.HTTPException):
        message = interaction.message
        if message is not None and not ephemeral and not send_files and embed is None:
            try:
                reply_kwargs = {"content": content or "", "mention_author": False}
                if view is not None:
                    reply_kwargs["view"] = view
                return await message.reply(**reply_kwargs)
            except (discord.NotFound, discord.HTTPException, TypeError):
                return None
        return None


async def safe_send_interaction_modal(interaction: discord.Interaction, modal: discord.ui.Modal) -> bool:
    try:
        if interaction.response.is_done():
            return False
        await interaction.response.send_modal(modal)
        return True
    except (discord.NotFound, discord.HTTPException):
        return False


async def render_discord_file_async(renderer, filename: str, *args, **kwargs) -> discord.File:
    buffer = await asyncio.to_thread(renderer, *args, **kwargs)
    return discord.File(buffer, filename=filename)


async def safe_channel_send_from_interaction(
    interaction: discord.Interaction,
    *,
    content: Optional[str] = None,
    file: Optional[discord.File] = None,
    files: Optional[Sequence[discord.File]] = None,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> Optional[discord.Message]:
    """Last-resort send path for expired interaction webhooks.

    Button interactions can lose their webhook token if rendering/battle work takes too
    long before the first response. This helper falls back to the channel so long
    tournament battles can still post updates instead of raising Unknown Webhook.
    """
    channel = getattr(interaction, 'channel', None)
    if channel is None or not is_debug_send_allowed(channel):
        return None
    send_files = list(files) if files is not None else ([] if file is None else [file])
    try:
        _reset_discord_file_handles(send_files)
        kwargs = {"content": fit_discord_message_content(content) or ""}
        if embed is not None:
            kwargs["embed"] = embed
        if view is not None:
            kwargs["view"] = view
        if len(send_files) == 1:
            kwargs["file"] = send_files[0]
        elif send_files:
            kwargs["files"] = send_files
        return await channel.send(**kwargs)
    except (discord.NotFound, discord.HTTPException, TypeError, AttributeError):
        return None


async def safe_send_or_channel_message(
    interaction: discord.Interaction,
    *,
    content: Optional[str] = None,
    file: Optional[discord.File] = None,
    files: Optional[Sequence[discord.File]] = None,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
    ephemeral: bool = False,
) -> Optional[discord.Message]:
    message = await safe_send_interaction_message(
        interaction,
        content=content,
        file=file,
        files=files,
        embed=embed,
        view=view,
        ephemeral=ephemeral,
    )
    if message is not None or ephemeral:
        return message
    return await safe_channel_send_from_interaction(
        interaction,
        content=content,
        file=file,
        files=files,
        embed=embed,
        view=view,
    )


async def safe_edit_interaction_message(
    interaction: discord.Interaction,
    *,
    content: Optional[str] = None,
    attachments: Optional[Sequence[discord.File]] = None,
    attachments_factory=None,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
):
    content = fit_discord_message_content(content)
    supplied_attachments = list(attachments) if attachments is not None else []
    attachment_factories = _discord_file_clone_factories(supplied_attachments)

    async def _build_attachments() -> List[discord.File]:
        if attachments_factory is not None:
            try:
                fresh_attachments = await asyncio.to_thread(attachments_factory)
            except Exception:
                return []
            return [current_file for current_file in list(fresh_attachments or []) if current_file is not None]
        fresh_files: List[discord.File] = []
        for factory in attachment_factories:
            current_file = factory()
            if current_file is not None:
                fresh_files.append(current_file)
        return fresh_files

    if attachments_factory is not None:
        acknowledged = await safe_acknowledge_interaction(interaction, thinking=False)
        if not acknowledged and interaction.message is None:
            return None

    clear_only_view = (
        content is None
        and embed is None
        and view is None
        and attachments_factory is None
        and attachments is None
    )
    if clear_only_view:
        try:
            await interaction.delete_original_response()
            return None
        except (discord.NotFound, discord.HTTPException, AttributeError):
            message = interaction.message
            if message is not None:
                try:
                    await message.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass
            return None

    async def _edit_with(target_callable):
        built_attachments = await _build_attachments()
        kwargs = {
            "content": content,
            "attachments": built_attachments,
            "embed": embed,
            "view": view,
        }
        _reset_discord_file_handles(built_attachments)
        return await target_callable(**kwargs)

    try:
        if attachments_factory is None and not interaction.response.is_done():
            await _edit_with(interaction.response.edit_message)
            return None
    except (discord.NotFound, discord.HTTPException):
        pass

    try:
        if not interaction.response.is_done():
            acknowledged = await safe_acknowledge_interaction(interaction, thinking=False)
            if not acknowledged and interaction.message is None:
                return None
        await _edit_with(interaction.edit_original_response)
        return None
    except (discord.NotFound, discord.HTTPException):
        message = interaction.message
        if message is None:
            return None
        try:
            await _edit_with(message.edit)
        except (discord.NotFound, discord.HTTPException, ValueError):
            return None
