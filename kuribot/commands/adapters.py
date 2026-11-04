"""Adapters that let commands reuse menu code written for interactions."""

from __future__ import annotations

from typing import Optional

import discord
from discord.ext import commands


class _CommandFollowupAdapter:
    def __init__(self, ctx: commands.Context):
        self.ctx = ctx

    async def send(self, *args, wait: bool = False, **kwargs):
        message = await self.ctx.send(*args, **kwargs)
        if wait:
            return message
        return message


class _CommandResponseAdapter:
    """Minimal discord.InteractionResponse shim for prefix-command shortcuts."""

    def __init__(self, parent: "_CommandInteractionAdapter"):
        self.parent = parent
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *args, **kwargs):
        self._done = True
        return None

    async def edit_message(self, **kwargs):
        self._done = True
        return await self.parent.edit_original_response(**kwargs)

    async def send_message(self, *args, **kwargs):
        self._done = True
        kwargs.pop('ephemeral', None)
        message = await self.parent.ctx.send(*args, **kwargs)
        self.parent._message = message
        return message


class _CommandInteractionAdapter:
    def __init__(self, ctx: commands.Context, message: discord.Message):
        self.ctx = ctx
        self._message = message
        self.followup = _CommandFollowupAdapter(ctx)
        self.response = _CommandResponseAdapter(self)
        self.guild = ctx.guild
        self.channel = ctx.channel
        self.user = ctx.author

    @property
    def message(self) -> Optional[discord.Message]:
        return self._message

    async def edit_original_response(self, **kwargs):
        attachments = kwargs.pop('attachments', None)
        file = kwargs.pop('file', None)
        files = kwargs.pop('files', None)
        if attachments is not None:
            kwargs['attachments'] = attachments
        elif file is not None:
            kwargs['attachments'] = [file]
        elif files is not None:
            kwargs['attachments'] = list(files)
        if self._message is None:
            # ctx.send does not accept the Interaction-only attachments kwarg.
            send_attachments = kwargs.pop('attachments', [])
            if len(send_attachments) == 1:
                kwargs['file'] = send_attachments[0]
            elif send_attachments:
                kwargs['files'] = list(send_attachments)
            self._message = await self.ctx.send(content=kwargs.pop('content', '') or '', **kwargs)
            return self._message
        message = await self._message.edit(**kwargs)
        self._message = message
        return message

    async def original_response(self):
        return self._message

    async def delete_original_response(self):
        if self._message is not None:
            await self._message.delete()
        self._message = None
