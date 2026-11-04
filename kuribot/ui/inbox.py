"""The inbox menu."""

from __future__ import annotations

import asyncio
import io
import sqlite3
from typing import List, Optional, Sequence

import discord
from PIL import ImageDraw

from kuribot.database import db
from kuribot.render.fonts import (
    FONT_L_BOLD, FONT_M, FONT_M_BOLD, FONT_S, FONT_S_BOLD, FONT_XL, FONT_XS, FONT_XS_BOLD,
)
from kuribot.render.themes import get_ui_theme_palette
from kuribot.render.images import (
    _PanelUserContext, open_binder_canvas_background, paste_binder_panel,
)
from kuribot.render.text import draw_centered_text_block, ellipsize_text, fit_wrapped_text
from kuribot.mail import (
    claim_mail_reward, format_mail_timestamp, mail_has_unclaimed_reward, reward_type_label,
)
from kuribot.ui.common import safe_edit_interaction_message


def render_inbox_page(user_name: str, theme_name: str, mails: Sequence[sqlite3.Row], selected_index: int) -> io.BytesIO:
    width, height = 960, 960
    img = open_binder_canvas_background((width, height))
    draw = ImageDraw.Draw(img)
    palette = get_ui_theme_palette(theme_name)

    draw.text((34, 28), f"{user_name}'s Inbox", font=FONT_XL, fill=palette["title"])
    unread_total = sum(1 for row in mails if not int(row["is_read"] or 0))
    draw.text((34, 76), f"Unread: {unread_total} • Messages: {len(mails)}", font=FONT_S, fill=palette["subtitle"])

    outer = (24, 112, 936, 934)
    paste_binder_panel(img, outer, radius=34)
    draw.rounded_rectangle(outer, radius=34, outline=palette["outer_outline"], width=3)

    list_box = (48, 144, 388, 900)
    detail_box = (410, 144, 912, 900)
    draw.rounded_rectangle(list_box, 26, fill=palette["panel_fill"], outline=palette["panel_outline"], width=3)
    draw.rounded_rectangle(detail_box, 26, fill=palette["panel_fill"], outline=palette["panel_outline"], width=3)
    draw.text((list_box[0] + 18, list_box[1] + 16), "Mail List", font=FONT_M_BOLD, fill=palette["title"])

    if not mails:
        draw.text((detail_box[0] + 26, detail_box[1] + 26), "Inbox Empty", font=FONT_L_BOLD, fill=palette["title"])
        draw.text((detail_box[0] + 26, detail_box[1] + 78), "You do not have any mail yet.", font=FONT_M, fill=palette["info_text"])
    else:
        selected_index = max(0, min(selected_index, len(mails) - 1))
        top = max(0, min(selected_index - 3, max(0, len(mails) - 6)))
        visible = mails[top:top + 6]
        card_height = 106
        for idx, mail in enumerate(visible):
            actual_index = top + idx
            y1 = list_box[1] + 60 + idx * (card_height + 12)
            y2 = y1 + card_height
            selected = actual_index == selected_index
            unread = not int(mail["is_read"] or 0)
            reward_text = reward_type_label(mail["reward_type"], int(mail["reward_amount"] or 0)) if mail["reward_type"] else "Message"
            fill = palette["card_fill"] if not selected else palette["panel_outline"]
            outline = palette["accent_binder"] if unread else palette["outer_outline"]
            draw.rounded_rectangle((list_box[0] + 14, y1, list_box[2] - 14, y2), 18, fill=fill, outline=outline, width=3)
            status = "NEW" if unread else "READ"
            draw.text((list_box[0] + 28, y1 + 14), status, font=FONT_XS_BOLD, fill=(230, 80, 80) if unread else palette["subtitle"])
            draw.text((list_box[0] + 90, y1 + 12), ellipsize_text(draw, reward_text, FONT_S_BOLD, 220), font=FONT_S_BOLD, fill=palette["title"])
            preview = ellipsize_text(draw, str(mail["body"] or "").replace("\n", " "), FONT_XS, 260)
            draw.text((list_box[0] + 28, y1 + 48), preview, font=FONT_XS, fill=palette["info_text"])
            draw.text((list_box[0] + 28, y1 + 74), format_mail_timestamp(mail["created_at"]), font=FONT_XS, fill=palette["subtitle"])

        current = mails[selected_index]
        unread = not int(current["is_read"] or 0)
        reward_type = str(current["reward_type"] or "").strip().lower()
        reward_amount = int(current["reward_amount"] or 0)
        reward_claimed = bool(int(current["reward_claimed"] or 0))
        sender_name = str(current["sender_name"] or "Admin")

        draw.text((detail_box[0] + 24, detail_box[1] + 18), "Selected Mail", font=FONT_M_BOLD, fill=palette["title"])
        draw.text((detail_box[0] + 24, detail_box[1] + 58), f"From: {sender_name}", font=FONT_S_BOLD, fill=palette["info_text"])
        draw.text((detail_box[0] + 24, detail_box[1] + 88), format_mail_timestamp(current["created_at"]), font=FONT_XS, fill=palette["subtitle"])
        status_text = "Unread" if unread else "Read"
        draw.text((detail_box[2] - 26, detail_box[1] + 22), status_text, font=FONT_XS_BOLD, fill=(230, 80, 80) if unread else palette["subtitle"], anchor="ra")

        if reward_type and reward_amount > 0:
            reward_box = (detail_box[0] + 22, detail_box[1] + 126, detail_box[2] - 22, detail_box[1] + 220)
            draw.rounded_rectangle(reward_box, 18, fill=palette["card_fill"], outline=palette["accent_support"], width=3)
            draw.text((reward_box[0] + 18, reward_box[1] + 14), "Attached Reward", font=FONT_S_BOLD, fill=palette["accent_support"])
            claim_status = "CLAIMED" if reward_claimed else "UNCLAIMED"
            reward_value = reward_type_label(reward_type, reward_amount)
            reward_value = ellipsize_text(draw, reward_value, FONT_L_BOLD, reward_box[2] - reward_box[0] - 36)
            info_box = (reward_box[0] + 18, reward_box[1] + 40, reward_box[2] - 18, reward_box[3] - 12)
            draw_centered_text_block(
                draw,
                info_box,
                [reward_value, claim_status],
                [FONT_L_BOLD, FONT_XS_BOLD],
                [palette["title"], (100, 190, 120) if reward_claimed else (230, 80, 80)],
                line_gap=8,
                x_align="left",
            )
            body_top = reward_box[3] + 18
        else:
            body_top = detail_box[1] + 126

        draw.text((detail_box[0] + 24, body_top), "Message", font=FONT_S_BOLD, fill=palette["title"])
        lines = fit_wrapped_text(draw, str(current["body"] or ""), FONT_S, detail_box[2] - detail_box[0] - 48, 16)
        y = body_top + 34
        for line in lines:
            draw.text((detail_box[0] + 24, y), line, font=FONT_S, fill=palette["signature_text"])
            y += 28

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", compress_level=3)
    buffer.seek(0)
    return buffer


class InboxView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, avatar_bytes: bytes, return_page: int = 1):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.avatar_bytes = avatar_bytes
        self.return_page = max(1, min(2, return_page))
        self.selected_index = 0
        self.message: Optional[discord.Message] = None
        self._sync_state()

    def _mailbox(self) -> List[sqlite3.Row]:
        return db.user_mailbox(self.owner_id)

    def _sync_state(self):
        mails = self._mailbox()
        read_count = sum(1 for mail in mails if int(mail["is_read"] or 0))
        if mails:
            self.selected_index = max(0, min(self.selected_index, len(mails) - 1))
        else:
            self.selected_index = 0
        unread_mails = [mail for mail in mails if not int(mail["is_read"] or 0)]
        self.prev_mail_btn.disabled = self.selected_index <= 0 or not mails
        self.next_mail_btn.disabled = self.selected_index >= len(mails) - 1 or not mails
        self.read_all_btn.disabled = not mails or read_count >= len(mails)
        self.claim_all_btn.disabled = not unread_mails and not any(mail_has_unclaimed_reward(mail) for mail in mails)
        self.delete_read_btn.disabled = not any(int(mail["is_read"] or 0) and not mail_has_unclaimed_reward(mail) for mail in mails)
        selected = mails[self.selected_index] if mails else None
        self.claim_btn.disabled = not selected or not selected["reward_type"] or int(selected["reward_amount"] or 0) <= 0 or bool(int(selected["reward_claimed"] or 0))

    def _mark_selected_read(self):
        mails = self._mailbox()
        if mails:
            selected = mails[self.selected_index]
            if not int(selected["is_read"] or 0):
                db.mark_mail_read(int(selected["id"]), self.owner_id)

    async def _edit_current_message(self, interaction: discord.Interaction, *, extra_message: Optional[str] = None):
        if not interaction.response.is_done():
            try:
                await interaction.response.defer()
            except (discord.NotFound, discord.HTTPException):
                pass
        self._sync_state()
        self._mark_selected_read()
        self._sync_state()
        content = self.summary_text()
        if extra_message:
            content = f"{content}\n{extra_message}"
        kwargs = {
            "content": content,
            "attachments": [await asyncio.to_thread(self.make_file)],
            "view": self,
        }
        try:
            await interaction.edit_original_response(**kwargs)
        except (discord.NotFound, discord.HTTPException):
            if interaction.message is None:
                raise
            await interaction.message.edit(**kwargs)

    def make_file(self):
        mails = db.user_mailbox(self.owner_id)
        theme_name = db.get_ui_theme(self.owner_id)
        with _PanelUserContext(self.owner_id):
            return discord.File(render_inbox_page(self.user_name, theme_name, mails, self.selected_index), filename="inbox.png")

    def summary_text(self) -> str:
        mails = self._mailbox()
        unread = db.unread_mail_count(self.owner_id)
        if not mails:
            return "Inbox • **0 messages**"
        current = mails[self.selected_index]
        reward = reward_type_label(current["reward_type"], int(current["reward_amount"] or 0)) if current["reward_type"] else "Message"
        return f"Inbox • **{len(mails)} messages** • **{unread} unread** • Viewing **{self.selected_index + 1}/{len(mails)}** • Type: **{reward}**"

    async def refresh(self, interaction: discord.Interaction):
        await self._edit_current_message(interaction)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This inbox belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Prev Mail", style=discord.ButtonStyle.secondary)
    async def prev_mail_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_index = max(0, self.selected_index - 1)
        await self.refresh(interaction)

    @discord.ui.button(label="Claim Reward", style=discord.ButtonStyle.success)
    async def claim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        mails = self._mailbox()
        if not mails:
            await interaction.response.send_message("Your inbox is empty.", ephemeral=True)
            return
        mail_row = mails[self.selected_index]
        ok, message = claim_mail_reward(mail_row)
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return
        self._sync_state()
        await self._edit_current_message(interaction, extra_message=message)

    @discord.ui.button(label="Claim All", style=discord.ButtonStyle.success)
    async def claim_all_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        mails = self._mailbox()
        unread_mails = [mail for mail in mails if not int(mail["is_read"] or 0)]
        # Opening a mail marks it read, so attachments on viewed mail used to be skipped
        # by Claim All forever. Claim every unclaimed attachment, read or not.
        claimable_mails = [mail for mail in mails if mail_has_unclaimed_reward(mail)]
        if not unread_mails and not claimable_mails:
            await interaction.response.send_message("You do not have any unread mail or unclaimed rewards.", ephemeral=True)
            return
        claimed_parts: List[str] = []
        claimed_count = 0
        target_ids = {int(mail["id"]) for mail in [*unread_mails, *claimable_mails]}
        for mail_row in [mail for mail in mails if int(mail["id"]) in target_ids]:
            has_reward = (
                (((str(mail_row["reward_type"] or "").strip()) and int(mail_row["reward_amount"] or 0) > 0)
                or str(mail_row["reward_bundle_json"] or "").strip())
                and not bool(int(mail_row["reward_claimed"] or 0))
            )
            if has_reward:
                ok, message = claim_mail_reward(mail_row)
                if ok:
                    claimed_count += 1
                    cleaned = str(message).strip()
                    if cleaned.lower().startswith("claimed "):
                        cleaned = cleaned[8:]
                    if cleaned.endswith('.'):
                        cleaned = cleaned[:-1]
                    claimed_parts.append(cleaned)
            if not int(mail_row["is_read"] or 0):
                db.mark_mail_read(int(mail_row["id"]), self.owner_id)
        self._sync_state()
        read_count = len(unread_mails)
        if claimed_parts:
            extra_message = (
                f"Marked **{read_count}** unread message{'s' if read_count != 1 else ''} as read and claimed rewards from "
                f"**{claimed_count}** message{'s' if claimed_count != 1 else ''}: " + "; ".join(claimed_parts[:5])
            )
            if len(claimed_parts) > 5:
                extra_message += f"; +{len(claimed_parts) - 5} more"
            extra_message += "."
        else:
            extra_message = f"Marked **{read_count}** unread message{'s' if read_count != 1 else ''} as read. No attachments were available to claim."
        await self._edit_current_message(interaction, extra_message=extra_message)

    @discord.ui.button(label="Delete Read", style=discord.ButtonStyle.danger)
    async def delete_read_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        read_count = sum(1 for mail in self._mailbox() if int(mail["is_read"] or 0) and not mail_has_unclaimed_reward(mail))
        if read_count <= 0:
            await interaction.response.send_message("You do not have any read mail to delete (mail with unclaimed rewards is kept).", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Delete **{read_count}** read message{'s' if read_count != 1 else ''}? This cannot be undone.",
            ephemeral=True,
            view=DeleteReadConfirmView(self),
        )

    @discord.ui.button(label="Mark All Read", style=discord.ButtonStyle.primary)
    async def read_all_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        db.mark_all_mail_read(self.owner_id)
        await self.refresh(interaction)

    @discord.ui.button(label="Back to Profile", style=discord.ButtonStyle.secondary)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from kuribot.ui.profile import ProfileView
        view = ProfileView(self.owner_id, self.user_name, self.avatar_bytes, viewer_id=self.owner_id, editable=True)
        view.page = self.return_page
        await view.refresh(interaction)

    @discord.ui.button(label="Next Mail", style=discord.ButtonStyle.secondary)
    async def next_mail_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.selected_index += 1
        await self.refresh(interaction)


class DeleteReadConfirmView(discord.ui.View):
    def __init__(self, inbox_view: InboxView):
        super().__init__(timeout=120)
        self.inbox_view = inbox_view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.inbox_view.owner_id:
            await interaction.response.send_message("This confirmation belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        deleted = db.delete_read_mail(self.inbox_view.owner_id)
        self.inbox_view._sync_state()
        self.inbox_view.selected_index = 0 if not self.inbox_view._mailbox() else min(self.inbox_view.selected_index, len(self.inbox_view._mailbox()) - 1)
        self.inbox_view._sync_state()
        for child in self.children:
            child.disabled = True
        await safe_edit_interaction_message(interaction, 
            content=f"Deleted **{deleted}** read message{'s' if deleted != 1 else ''}.",
            view=self,
        )
        if self.inbox_view.message is not None:
            await self.inbox_view.message.edit(content=self.inbox_view.summary_text(), attachments=[await asyncio.to_thread(self.inbox_view.make_file)], view=self.inbox_view)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await safe_edit_interaction_message(interaction, content="Delete read cancelled.", view=self)
