"""Command and menu error handling, and log file setup."""

from __future__ import annotations

import logging
import logging.handlers
from typing import Optional

import discord
from discord.ext import commands

from kuribot.config import BASE_DIR, PREFIX
from kuribot.core import bot, is_debug_send_allowed, log
from kuribot.ui.common import safe_send_interaction_message


LOG_PATH = BASE_DIR / "kuribot.log"


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    # Commands with their own error handler deal with it themselves.
    if ctx.command is not None and ctx.command.has_error_handler():
        return
    # The prefix is ";", so emoticons like ";)" or ";-;" are not commands - stay quiet.
    if isinstance(error, commands.CommandNotFound):
        return
    if not is_debug_send_allowed(getattr(ctx, "channel", None)):
        return
    command_name = f"{PREFIX}{ctx.command.qualified_name}" if ctx.command is not None else "that command"
    reply: Optional[str] = None
    if isinstance(error, commands.MissingRequiredArgument):
        reply = f"`{command_name}` is missing **{error.param.name}**. Use `{PREFIX}help` to see how it works."
    elif isinstance(error, (commands.MemberNotFound, commands.UserNotFound)):
        reply = "I couldn't find that user. Mention them or use their user ID."
    elif isinstance(error, commands.BadArgument) or isinstance(error, commands.UserInputError):
        reply = f"I couldn't understand the input for `{command_name}`. Use `{PREFIX}help` to see how it works."
    elif isinstance(error, commands.CheckFailure):
        reply = "You can't use that command here."
    elif isinstance(error, commands.CommandOnCooldown):
        reply = f"Slow down - try `{command_name}` again in {error.retry_after:.0f}s."
    elif isinstance(error, commands.MaxConcurrencyReached):
        reply = f"`{command_name}` is already running. Wait for it to finish first."
    else:
        original = getattr(error, "original", error)
        log.error("Command %s failed (user %s)", command_name, getattr(ctx.author, "id", "?"), exc_info=original)
        reply = f"Something went wrong running `{command_name}`. It has been logged - please try again."
    try:
        await ctx.send(reply)
    except discord.HTTPException:
        pass


async def _default_view_on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item, /):
    # Fallback for every View that does not define its own on_error. Without this, the
    # player only sees Discord's generic "This interaction failed".
    log.error(
        "View %s failed on %s (user %s)",
        type(self).__name__,
        getattr(item, "custom_id", None) or getattr(item, "label", None) or type(item).__name__,
        getattr(interaction.user, "id", "?"),
        exc_info=error,
    )
    try:
        await safe_send_interaction_message(interaction, content="Something went wrong with that action. Please try again.", ephemeral=True)
    except Exception:
        pass


async def _default_modal_on_error(self, interaction: discord.Interaction, error: Exception, /):
    log.error("Modal %s failed (user %s)", type(self).__name__, getattr(interaction.user, "id", "?"), exc_info=error)
    try:
        await safe_send_interaction_message(interaction, content="Something went wrong submitting that form. Please try again.", ephemeral=True)
    except Exception:
        pass


discord.ui.View.on_error = _default_view_on_error
discord.ui.Modal.on_error = _default_modal_on_error


def setup_kuribot_logging() -> None:
    # Console keeps discord.py's normal output; warnings and errors (with full tracebacks)
    # are also written to kuribot.log so problems can be looked at after the fact.
    discord.utils.setup_logging(level=logging.INFO, root=True)
    file_handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(logging.Formatter("[{asctime}] [{levelname:<8}] {name}: {message}", "%Y-%m-%d %H:%M:%S", style="{"))
    logging.getLogger().addHandler(file_handler)
