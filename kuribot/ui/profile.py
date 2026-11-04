"""Profile, avatar and customization menus."""

from __future__ import annotations

import asyncio
import io
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

import discord

from kuribot.config import (
    LEVELING_MAX_LEVEL, PLAYER_AVATAR_PAGE_SIZE, PREMIUM_WELCOME_TICKETS,
    PROFILE_TAG_ALLOWED_CONTENT_TYPES, PROFILE_TAG_ALLOWED_EXTENSIONS,
    PROFILE_TAG_MAX_UPLOAD_BYTES, PROFILE_TAGS_PENDING_DIR,
)
from kuribot.timeutil import is_current_reset_window, reset_countdown_text
from kuribot.core import bot
from kuribot.database import db
from kuribot.render.themes import (
    UI_THEME_KEYS, UI_THEME_LABELS, ui_theme_page_count, ui_theme_page_keys, UI_THEME_PAGE_SIZE,
)
from kuribot.render.images import _PanelUserContext
from kuribot.players import (
    ensure_start_welcome_mail, grant_start_registration_rewards, xp_required_for_level,
)
from kuribot.portraits import (
    get_player_avatar_choices, get_player_avatar_page, get_player_avatar_path_by_name,
    get_user_profile_avatar_bytes, render_avatar_selection_page,
)
from kuribot.ranked import ranked_gxe_from_row
from kuribot.profile import (
    finalize_profile_tag_request, is_profile_tag_moderator, normalize_profile_background_image,
    normalize_profile_tag_image, PROFILE_BACKGROUNDS_PENDING_DIR, profile_customization_label,
    send_profile_tag_moderation_request,
)
from kuribot.render.profile import render_profile_page
from kuribot.ui.common import (
    safe_acknowledge_interaction, safe_edit_interaction_message, safe_send_interaction_message,
)
from kuribot.ui.inbox import InboxView
from kuribot.ui.start import StartTutorialView


class ProfileTagUploadView(discord.ui.View):
    def __init__(self, profile_view: "ProfileView"):
        super().__init__(timeout=180)
        self.profile_view = profile_view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.profile_view.owner_id:
            await interaction.response.send_message("This tag uploader belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await safe_edit_interaction_message(interaction, content="Showcase upload cancelled.", view=None)


class ProfileTagModerationView(discord.ui.View):
    def __init__(self, request_id: str):
        super().__init__(timeout=None)
        self.request_id = request_id
        # Fixed custom IDs make this a persistent view: after a restart the bot re-attaches
        # it (see restore_moderation_views), so Accept/Deny keep working. Without them the
        # buttons died on every restart and the request stayed "pending" forever, which also
        # blocked that player from uploading again.
        self.accept_btn.custom_id = f"kuribot:tagmod:accept:{request_id}"[:100]
        self.deny_btn.custom_id = f"kuribot:tagmod:deny:{request_id}"[:100]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        row = db.get_profile_tag_upload(self.request_id)
        if not is_profile_tag_moderator(interaction.user, row):
            await interaction.response.send_message("You do not have permission to moderate this profile tag request.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, message = await finalize_profile_tag_request(self.request_id, True, interaction.user)
        self.accept_btn.disabled = True
        self.deny_btn.disabled = True
        await safe_edit_interaction_message(interaction, content=message, view=self)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, message = await finalize_profile_tag_request(self.request_id, False, interaction.user)
        self.accept_btn.disabled = True
        self.deny_btn.disabled = True
        await safe_edit_interaction_message(interaction, content=message, view=self)


class ProfileSignatureModal(discord.ui.Modal, title="Edit Signature"):
    signature = discord.ui.TextInput(
        label="Signature",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=400,
        placeholder="Write a short message for your profile.",
    )

    def __init__(self, profile_view: "ProfileView"):
        super().__init__()
        self.profile_view = profile_view
        current_value = str(db.get_user(profile_view.owner_id)["signature_text"] or "")
        self.signature.default = current_value

    async def on_submit(self, interaction: discord.Interaction):
        db.set_signature_text(self.profile_view.owner_id, str(self.signature).strip())
        await self.profile_view.refresh(interaction)


class ProfileThemeSelect(discord.ui.Select):
    def __init__(self, theme_view: "ProfileThemeView"):
        self.theme_view = theme_view
        current_theme = db.get_ui_theme(theme_view.profile_view.owner_id)
        page_keys = ui_theme_page_keys(theme_view.page)
        options = [
            discord.SelectOption(
                label=UI_THEME_LABELS[key],
                value=key,
                description=f"Switch to the {UI_THEME_LABELS[key].lower()} theme",
                default=(key == current_theme),
            )
            for key in page_keys
        ] or [discord.SelectOption(label="No themes", value="gray")]
        super().__init__(
            placeholder=f"Choose a UI theme • Page {theme_view.page + 1}/{ui_theme_page_count()}",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        chosen = str(self.values[0])
        db.set_ui_theme(self.theme_view.profile_view.owner_id, chosen)
        self.theme_view.sync_items()
        await interaction.response.edit_message(
            content=self.theme_view.status_text(chosen),
            view=self.theme_view,
        )
        try:
            await self.theme_view.profile_view.refresh(interaction)
        except Exception:
            pass


class ProfileThemeView(discord.ui.View):
    def __init__(self, profile_view: "ProfileView", page: int = 0):
        super().__init__(timeout=180)
        self.profile_view = profile_view
        self.page = max(0, min(ui_theme_page_count() - 1, int(page)))
        self.sync_items()

    def status_text(self, selected_theme: Optional[str] = None) -> str:
        active_theme = str(selected_theme or db.get_ui_theme(self.profile_view.owner_id)).strip().lower()
        active_label = UI_THEME_LABELS.get(active_theme, "Gray")
        start_index = (self.page * UI_THEME_PAGE_SIZE) + 1
        end_index = min(len(UI_THEME_KEYS), start_index + UI_THEME_PAGE_SIZE - 1)
        return (
            f"Choose your UI theme • Page **{self.page + 1}/{ui_theme_page_count()}** "
            f"(themes **{start_index}-{end_index}** of **{len(UI_THEME_KEYS)}**) • Current: **{active_label}**"
        )

    def sync_items(self):
        self.clear_items()
        self.add_item(ProfileThemeSelect(self))
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= ui_theme_page_count() - 1
        self.add_item(self.prev_button)
        self.add_item(self.next_button)

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=1)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self.sync_items()
        await interaction.response.edit_message(content=self.status_text(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(ui_theme_page_count() - 1, self.page + 1)
        self.sync_items()
        await interaction.response.edit_message(content=self.status_text(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.profile_view.owner_id:
            await interaction.response.send_message("This profile belongs to someone else.", ephemeral=True)
            return False
        return True


class AvatarJumpToModal(discord.ui.Modal, title="Jump To Page"):
    page_number = discord.ui.TextInput(
        label="Page number",
        placeholder="Enter a page number",
        required=True,
        max_length=4,
    )

    def __init__(self, avatar_view: "AvatarSelectionView"):
        super().__init__()
        self.avatar_view = avatar_view
        _, current_page, total_pages = avatar_view._page_info()
        self.page_number.default = str(current_page)
        self.page_number.placeholder = f"1-{total_pages}"

    async def on_submit(self, interaction: discord.Interaction):
        _, _, total_pages = self.avatar_view._page_info()
        raw_value = str(self.page_number).strip()
        try:
            target_page = int(raw_value)
        except Exception:
            await interaction.response.send_message("Enter a valid page number.", ephemeral=True)
            return
        if target_page < 1 or target_page > total_pages:
            await interaction.response.send_message(f"Choose a page between 1 and {total_pages}.", ephemeral=True)
            return
        self.avatar_view.page = target_page
        await self.avatar_view.update_message(interaction, notice=f"Jumped to page **{target_page}**.")


class AvatarSelect(discord.ui.Select):
    def __init__(self, avatar_view: "AvatarSelectionView"):
        self.avatar_view = avatar_view
        page_items, page, total_pages = get_player_avatar_page(avatar_view.page)
        options = [
            discord.SelectOption(
                label=name[:100],
                value=name,
                description=("Currently selected" if avatar_view.selected_name.lower() == name.lower() else f"Avatar {idx + 1 + (page - 1) * PLAYER_AVATAR_PAGE_SIZE}")[:100],
                default=avatar_view.selected_name.lower() == name.lower(),
            )
            for idx, (name, _) in enumerate(page_items)
        ]
        if not options:
            options = [discord.SelectOption(label="No avatars found", value="__none__", description="Check AVATARS_DIR / Portraits folder")]
        super().__init__(placeholder="Choose an avatar", min_values=1, max_values=1, options=options, disabled=(options[0].value == "__none__"))

    async def callback(self, interaction: discord.Interaction):
        chosen = str(self.values[0])
        if chosen == "__none__":
            await safe_acknowledge_interaction(interaction)
            return
        self.avatar_view.selected_name = chosen
        await self.avatar_view.refresh(interaction, notice=f"Selected **{chosen}**. Press **Confirm** to save it.")


class AvatarSelectionView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, *, page: int = 1, selected_name: str = "", mode: str = "profile", profile_view: Optional["ProfileView"] = None):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.user_name = user_name
        self.page = page
        self.selected_name = selected_name
        self.mode = mode
        self.profile_view = profile_view
        self.message: Optional[discord.Message] = None
        self._rebuild_items()

    def _default_selected_name(self) -> str:
        stored = db.get_avatar_path(self.owner_id)
        if stored:
            try:
                return Path(stored).stem
            except Exception:
                return ""
        return ""

    def _page_info(self) -> Tuple[List[Tuple[str, Path]], int, int]:
        return get_player_avatar_page(self.page)

    def _rebuild_items(self):
        self.clear_items()
        _, self.page, total_pages = self._page_info()
        if not self.selected_name:
            self.selected_name = self._default_selected_name()
        self.add_item(AvatarSelect(self))
        prev_btn = discord.ui.Button(label="Prev", style=discord.ButtonStyle.secondary, disabled=self.page <= 1)
        jump_btn = discord.ui.Button(label="Jump To", style=discord.ButtonStyle.primary, disabled=total_pages <= 1)
        next_btn = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary, disabled=self.page >= total_pages)
        confirm_btn = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.success, disabled=not bool(self.selected_name))
        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger)

        async def prev_callback(interaction: discord.Interaction):
            self.page = max(1, self.page - 1)
            await self.refresh(interaction)

        async def jump_callback(interaction: discord.Interaction):
            await interaction.response.send_modal(AvatarJumpToModal(self))

        async def next_callback(interaction: discord.Interaction):
            self.page = self.page + 1
            await self.refresh(interaction)

        async def confirm_callback(interaction: discord.Interaction):
            await self.confirm_selection(interaction)

        async def cancel_callback(interaction: discord.Interaction):
            await safe_edit_interaction_message(interaction, content="Avatar selection closed.", attachments=[], view=None)

        prev_btn.callback = prev_callback
        jump_btn.callback = jump_callback
        next_btn.callback = next_callback
        confirm_btn.callback = confirm_callback
        cancel_btn.callback = cancel_callback
        self.add_item(prev_btn)
        self.add_item(jump_btn)
        self.add_item(next_btn)
        self.add_item(confirm_btn)
        self.add_item(cancel_btn)

    def make_file(self):
        with _PanelUserContext(getattr(self, "owner_id", None)):
            return discord.File(render_avatar_selection_page(self.user_name, self.page, self.selected_name, db.get_ui_theme(self.owner_id)), filename="avatar_select.png")

    def intro_text(self) -> str:
        _, page, total_pages = self._page_info()
        if not get_player_avatar_choices():
            return "No avatars were found in the Portraits folder. Add images to the avatars directory first."
        selected = self.selected_name or "None"
        return f"Choose your KuriBot avatar • Page **{page}/{total_pages}** • Selected: **{selected}**"

    async def refresh(self, interaction: discord.Interaction, notice: Optional[str] = None):
        self._rebuild_items()
        content = notice or self.intro_text()
        await safe_edit_interaction_message(interaction, content=content, attachments=[await asyncio.to_thread(self.make_file)], view=self)
        if interaction.message is not None:
            self.message = interaction.message

    async def update_message(self, interaction: discord.Interaction, notice: Optional[str] = None):
        self._rebuild_items()
        content = notice or self.intro_text()
        if self.message is not None:
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer()
            except (discord.NotFound, discord.HTTPException):
                pass
            await self.message.edit(content=content, attachments=[await asyncio.to_thread(self.make_file)], view=self)
            return
        await safe_edit_interaction_message(interaction, content=content, attachments=[await asyncio.to_thread(self.make_file)], view=self)
        if interaction.message is not None:
            self.message = interaction.message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This avatar picker belongs to someone else.", ephemeral=True)
            return False
        return True

    async def confirm_selection(self, interaction: discord.Interaction):
        chosen_path = get_player_avatar_path_by_name(self.selected_name)
        if chosen_path is None:
            await interaction.response.send_message("I couldn't find that avatar anymore. Please choose again.", ephemeral=True)
            return
        db.set_avatar_path(self.owner_id, str(chosen_path))
        if self.mode == "start":
            db.complete_registration(self.owner_id)
            ensure_start_welcome_mail(self.owner_id, self.user_name)
            starter_monsters, starter_equips = grant_start_registration_rewards(self.owner_id, self.user_name)
            starter_text = ""
            if starter_monsters or starter_equips:
                starter_text = (
                    "\n\nStarter rewards added to your account: "
                    f"**{len(starter_monsters)} monsters** and **{len(starter_equips)} equip spells**."
                )
            help_text = (
                f"Avatar confirmed: **{self.selected_name}**\n\n"
                "The beginner tutorial is optional, so you can use KuriBot commands right away. "
                "Finishing the tutorial still gives free rewards, so it is worth doing when you are ready.\n\n"
                f"A welcome gift message is waiting in your inbox with **{PREMIUM_WELCOME_TICKETS} Random Pack** attached."
                f"{starter_text}\n\n"
                "Press **Tutorial** below to start the optional tutorial, or use `;help` to explore commands."
            )
            await safe_edit_interaction_message(
                interaction,
                content=help_text,
                attachments=[],
                view=StartTutorialView(self.owner_id, self.user_name),
            )
        else:
            if self.profile_view is not None and self.profile_view.message is not None:
                try:
                    member = interaction.guild.get_member(self.owner_id) if interaction.guild else None
                    avatar_user = member or interaction.user
                    self.profile_view.avatar_bytes = await get_user_profile_avatar_bytes(avatar_user, size=256)
                    await self.profile_view.message.edit(
                        content=None,
                        attachments=[await asyncio.to_thread(self.profile_view.make_file)],
                        view=self.profile_view,
                    )
                except Exception:
                    pass
            await safe_edit_interaction_message(
                interaction,
                content=f"Avatar confirmed: **{self.selected_name}**. Your profile has been updated.",
                attachments=[],
                view=None,
            )


class ProfileView(discord.ui.View):
    def __init__(self, owner_id: int, user_name: str, avatar_bytes: bytes, *, viewer_id: Optional[int] = None, editable: bool = True):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.viewer_id = int(viewer_id if viewer_id is not None else owner_id)
        self.editable = bool(editable)
        self.user_name = user_name
        self.avatar_bytes = avatar_bytes
        self.page = 1
        self.message: Optional[discord.Message] = None
        self.pending_tag_request_id: Optional[str] = None
        self.customize_btn = discord.ui.Button(label="Customize", style=discord.ButtonStyle.primary)

        async def customize_callback(interaction: discord.Interaction):
            await interaction.response.send_message("Choose what you want to customize:", view=ProfileCustomizeMenu(self), ephemeral=True)

        self.customize_btn.callback = customize_callback
        self._sync_buttons()

    def _sync_buttons(self):
        self.prev_btn.disabled = self.page <= 1
        self.next_btn.disabled = self.page >= 2
        for item in [getattr(self, name, None) for name in ("signature_btn", "tag_btn", "theme_btn")]:
            if item in self.children:
                self.remove_item(item)
        wanted = [self.avatar_btn, getattr(self, "customize_btn", None), self.inbox_btn]
        if not self.editable:
            for item in wanted:
                if item in self.children:
                    self.remove_item(item)
            return
        for item in wanted:
            if item is not None and item not in self.children:
                self.add_item(item)

    def _render_buffer(self) -> io.BytesIO:
        user_row = db.get_user(self.owner_id)
        binder_size = len(db.user_inventory(self.owner_id))
        support_card_count = len(db.user_support_cards(self.owner_id))
        membership = db.get_user_guild_membership(self.owner_id)
        daily_ready = not is_current_reset_window(user_row["last_daily"])
        daily_text = "Ready now" if daily_ready else reset_countdown_text()
        signature_text = str(user_row["signature_text"] or "")
        guild_text = str(membership["name"] or "None") if membership else "None"
        theme_name = db.get_ui_theme(self.owner_id)
        unread_mail = db.unread_mail_count(self.owner_id)
        pending_upload = db.get_pending_profile_tag_upload_for_user(self.owner_id)
        self.pending_tag_request_id = str(pending_upload["request_id"]) if pending_upload else None
        return render_profile_page(
            self.user_name,
            self.avatar_bytes,
            user_row,
            binder_size,
            support_card_count,
            daily_text,
            signature_text,
            guild_text,
            self.page,
            theme_name,
            unread_mail,
        )

    def summary_text(self) -> str:
        user_row = db.get_user(self.owner_id)
        binder_size = len(db.user_inventory(self.owner_id))
        support_card_count = len(db.user_support_cards(self.owner_id))
        membership = db.get_user_guild_membership(self.owner_id)
        daily_ready = not is_current_reset_window(user_row["last_daily"])
        daily_text = "Ready now" if daily_ready else f"Refresh in {reset_countdown_text()}"
        guild_text = str(membership["name"] or "None") if membership else "None"
        theme_label = UI_THEME_LABELS.get(db.get_ui_theme(self.owner_id), "Gray") if db.has_ui_theme_unlocked(self.owner_id) else "Locked"
        unread_mail = db.unread_mail_count(self.owner_id)
        pending_showcase = db.get_pending_profile_customization_upload_for_user(self.owner_id, "showcase")
        showcase_state = "Locked" if not db.has_showcase_unlocked(self.owner_id) else ("Pending review" if pending_showcase else ("Configured" if db.get_tag_image_path(self.owner_id) else "Empty"))
        pending_background = db.get_pending_profile_customization_upload_for_user(self.owner_id, "background")
        background_state = "Locked" if not db.has_background_unlocked(self.owner_id) else ("Pending review" if pending_background else ("Configured" if db.get_background_image_path(self.owner_id) else "Empty"))
        level_value = int(user_row["level"] or 1)
        next_level_xp = 0 if level_value >= LEVELING_MAX_LEVEL else xp_required_for_level(level_value)
        xp_text = "MAX" if level_value >= LEVELING_MAX_LEVEL else f"{int(user_row['level_xp'] or 0)}/{next_level_xp} XP"
        return (
            f"Profile page **{self.page}/2** • Level: **{level_value}** ({xp_text}) • Theme: **{theme_label}** • Packs: **{sum(int(row['quantity'] or 0) for row in db.user_packs(self.owner_id))}** • Gold: **{user_row['gold']}** • "
            f"Binder: **{binder_size}** • Cards: **{support_card_count}** • Ranked: **{user_row['ranked_elo']} ELO / {ranked_gxe_from_row(user_row):.1f}% GXE** • Daily: **{daily_text}** • Mail: **{unread_mail} unread** • "
            f"Guild: **{guild_text}** • Showcase: **{showcase_state}** • Background: **{background_state}**"
        )

    def make_file(self) -> discord.File:
        return discord.File(self._render_buffer(), filename="profile.png")

    async def refresh(self, interaction: discord.Interaction):
        self._sync_buttons()
        if not interaction.response.is_done():
            try:
                await interaction.response.defer()
            except (discord.NotFound, discord.HTTPException):
                pass
        kwargs = {
            "content": None,
            "attachments": [await asyncio.to_thread(self.make_file)],
            "view": self,
        }
        try:
            await interaction.edit_original_response(**kwargs)
        except (discord.NotFound, discord.HTTPException):
            if interaction.message is None:
                raise
            await interaction.message.edit(**kwargs)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("This profile session belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(1, self.page - 1)
        await self.refresh(interaction)

    @discord.ui.button(label="Avatar", style=discord.ButtonStyle.primary)
    async def avatar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        picker = AvatarSelectionView(self.owner_id, self.user_name, selected_name=Path(db.get_avatar_path(self.owner_id)).stem if db.get_avatar_path(self.owner_id) else "", mode="profile", profile_view=self)
        picker.message = await safe_send_interaction_message(
            interaction,
            content=picker.intro_text(),
            file=await asyncio.to_thread(picker.make_file),
            view=picker,
            ephemeral=True,
        )

    @discord.ui.button(label="Signature", style=discord.ButtonStyle.primary)
    async def signature_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ProfileSignatureModal(self))

    @discord.ui.button(label="Showcase", style=discord.ButtonStyle.primary)
    async def tag_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        existing_pending = db.get_pending_profile_tag_upload_for_user(self.owner_id)
        if existing_pending:
            await interaction.response.send_message("You already have a Showcase image pending review. Wait for an admin decision before uploading another one.", ephemeral=True)
            return
        prompt_view = ProfileTagUploadView(self)
        await interaction.response.send_message(
            "Upload **one image attachment** in this channel within **3 minutes** and KuriBot will send it to the admins for approval.",
            ephemeral=True,
            view=prompt_view,
        )

        def check(message: discord.Message) -> bool:
            if message.author.id != self.owner_id:
                return False
            if message.channel.id != interaction.channel_id:
                return False
            if not message.attachments:
                return False
            attachment = message.attachments[0]
            content_type = str(getattr(attachment, "content_type", "") or "").lower()
            suffix = Path(str(attachment.filename or "")).suffix.lower()
            return (content_type in PROFILE_TAG_ALLOWED_CONTENT_TYPES) or (suffix in PROFILE_TAG_ALLOWED_EXTENSIONS)

        try:
            upload_message = await bot.wait_for("message", timeout=180, check=check)
        except asyncio.TimeoutError:
            if interaction.response.is_done():
                await interaction.followup.send("Showcase upload timed out. Press **Showcase** again when you're ready.", ephemeral=True)
            return

        attachment = upload_message.attachments[0]
        if attachment.size and int(attachment.size) > PROFILE_TAG_MAX_UPLOAD_BYTES:
            await interaction.followup.send("That image is too large. Keep it under 8 MB and try again.", ephemeral=True)
            return
        try:
            raw_bytes = await attachment.read()
            normalized_bytes = normalize_profile_tag_image(raw_bytes)
        except Exception:
            await interaction.followup.send("I couldn't read that image. Please try a PNG, JPG, or WEBP file.", ephemeral=True)
            return

        request_id = uuid.uuid4().hex[:12]
        pending_path = PROFILE_TAGS_PENDING_DIR / f"{request_id}.png"
        pending_path.write_bytes(normalized_bytes)
        db.create_profile_tag_upload(
            request_id,
            self.owner_id,
            str(pending_path),
            guild_id=int(interaction.guild_id) if interaction.guild_id else None,
            channel_id=int(interaction.channel_id) if interaction.channel_id else None,
            original_filename=str(attachment.filename or "upload.png"),
        )
        sent_count = await send_profile_tag_moderation_request(request_id)
        if sent_count <= 0:
            try:
                pending_path.unlink(missing_ok=True)
            except Exception:
                pass
            db.resolve_profile_tag_upload(request_id, "denied", decided_by=0, approved_image_path="")
            await interaction.followup.send("I couldn't reach any admins right now, so the upload was not submitted. Please try again later.", ephemeral=True)
            return
        await interaction.followup.send(f"Showcase image submitted for moderation and sent to **{sent_count}** admin(s). You'll get a DM when it's accepted or denied.", ephemeral=True)

    @discord.ui.button(label="UI Theme", style=discord.ButtonStyle.primary)
    async def theme_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        theme_view = ProfileThemeView(self)
        await interaction.response.send_message(theme_view.status_text(), view=theme_view, ephemeral=True)

    @discord.ui.button(label="Inbox", style=discord.ButtonStyle.success)
    async def inbox_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = InboxView(self.owner_id, self.user_name, self.avatar_bytes, self.page)
        view.message = interaction.message
        await view.refresh(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(2, self.page + 1)
        await self.refresh(interaction)

    async def start_custom_image_upload(self, interaction: discord.Interaction, upload_type: str):
        normalized_type = str(upload_type or "showcase").strip().lower()
        label = profile_customization_label(normalized_type)
        if normalized_type == "background":
            if not db.has_background_unlocked(self.owner_id):
                await interaction.response.send_message("Unlock **Background** from the Customization Shop first.", ephemeral=True)
                return
            existing_pending = db.get_pending_profile_customization_upload_for_user(self.owner_id, "background")
            prompt_text = "Upload **one image attachment** in this channel within **3 minutes** and KuriBot will send it to the admins for approval as your Background image."
        else:
            if not db.has_showcase_unlocked(self.owner_id):
                await interaction.response.send_message("Unlock **Showcase** from the Customization Shop first.", ephemeral=True)
                return
            existing_pending = db.get_pending_profile_customization_upload_for_user(self.owner_id, "showcase")
            prompt_text = "Upload **one image attachment** in this channel within **3 minutes** and KuriBot will send it to the admins for approval as your Showcase image."
        if existing_pending:
            await interaction.response.send_message(f"You already have a {label} image pending review. Wait for an admin decision before uploading another one.", ephemeral=True)
            return
        prompt_view = ProfileTagUploadView(self)
        await interaction.response.send_message(prompt_text, ephemeral=True, view=prompt_view)

        def check(message: discord.Message) -> bool:
            if message.author.id != self.owner_id:
                return False
            if message.channel.id != interaction.channel_id:
                return False
            if not message.attachments:
                return False
            attachment = message.attachments[0]
            content_type = str(getattr(attachment, "content_type", "") or "").lower()
            suffix = Path(str(attachment.filename or "")).suffix.lower()
            return (content_type in PROFILE_TAG_ALLOWED_CONTENT_TYPES) or (suffix in PROFILE_TAG_ALLOWED_EXTENSIONS)

        try:
            upload_message = await bot.wait_for("message", timeout=180, check=check)
        except asyncio.TimeoutError:
            if interaction.response.is_done():
                await interaction.followup.send(f"{label} upload timed out. Press **{label}** again when you're ready.", ephemeral=True)
            return

        attachment = upload_message.attachments[0]
        if attachment.size and int(attachment.size) > PROFILE_TAG_MAX_UPLOAD_BYTES:
            await interaction.followup.send("That image is too large. Keep it under 8 MB and try again.", ephemeral=True)
            return
        try:
            raw_bytes = await attachment.read()
            normalized_bytes = normalize_profile_background_image(raw_bytes) if normalized_type == "background" else normalize_profile_tag_image(raw_bytes)
        except Exception:
            await interaction.followup.send("I couldn't read that image. Please try a PNG, JPG, or WEBP file.", ephemeral=True)
            return

        request_id = uuid.uuid4().hex[:12]
        pending_dir = PROFILE_BACKGROUNDS_PENDING_DIR if normalized_type == "background" else PROFILE_TAGS_PENDING_DIR
        pending_path = pending_dir / f"{request_id}.png"
        pending_path.write_bytes(normalized_bytes)
        db.create_profile_customization_upload(
            request_id,
            self.owner_id,
            str(pending_path),
            upload_type=normalized_type,
            guild_id=int(interaction.guild_id) if interaction.guild_id else None,
            channel_id=int(interaction.channel_id) if interaction.channel_id else None,
            original_filename=str(attachment.filename or "upload.png"),
        )
        sent_count = await send_profile_tag_moderation_request(request_id)
        if sent_count <= 0:
            try:
                pending_path.unlink(missing_ok=True)
            except Exception:
                pass
            db.resolve_profile_tag_upload(request_id, "denied", decided_by=0, approved_image_path="")
            await interaction.followup.send("I couldn't reach any admins right now, so the upload was not submitted. Please try again later.", ephemeral=True)
            return
        await interaction.followup.send(f"{label} image submitted for moderation and sent to **{sent_count}** admin(s). You'll get a DM when it's accepted or denied.", ephemeral=True)


class ProfileCustomizeMenu(discord.ui.View):
    def __init__(self, profile_view: "ProfileView"):
        super().__init__(timeout=180)
        self.profile_view = profile_view
        self._build()

    def _build(self):
        self.clear_items()
        signature_btn = discord.ui.Button(label="Signature", style=discord.ButtonStyle.primary)
        async def signature_callback(interaction: discord.Interaction):
            await interaction.response.send_modal(ProfileSignatureModal(self.profile_view))
        signature_btn.callback = signature_callback
        self.add_item(signature_btn)

        showcase_btn = discord.ui.Button(label="Showcase", style=discord.ButtonStyle.primary, disabled=not db.has_showcase_unlocked(self.profile_view.owner_id))
        async def showcase_callback(interaction: discord.Interaction):
            await self.profile_view.start_custom_image_upload(interaction, "showcase")
        showcase_btn.callback = showcase_callback
        self.add_item(showcase_btn)

        background_btn = discord.ui.Button(label="Background", style=discord.ButtonStyle.primary, disabled=not db.has_background_unlocked(self.profile_view.owner_id))
        async def background_callback(interaction: discord.Interaction):
            await self.profile_view.start_custom_image_upload(interaction, "background")
        background_btn.callback = background_callback
        self.add_item(background_btn)

        theme_btn = discord.ui.Button(label="UI Theme", style=discord.ButtonStyle.primary, disabled=not db.has_ui_theme_unlocked(self.profile_view.owner_id))
        async def theme_callback(interaction: discord.Interaction):
            await interaction.response.send_message("Choose your UI theme:", view=ProfileThemeView(self.profile_view), ephemeral=True)
        theme_btn.callback = theme_callback
        self.add_item(theme_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.profile_view.owner_id:
            await interaction.response.send_message("This customize menu belongs to someone else.", ephemeral=True)
            return False
        return True
