"""Image loading, caching, masks and panel backgrounds."""

from __future__ import annotations

import contextvars
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from kuribot.config import (
    BINDER_PANEL_ALPHA, BINDER_PANEL_IMAGE_PATH, CAMPAIGN_PANEL_BG_ALPHA,
    CAMPAIGN_PANEL_TINT_ALPHA, SUPPORT_CARD_IMAGE_PATH,
)
from kuribot.database import db
from kuribot.render.fonts import FONT_M_BOLD, FONT_S
from kuribot.render.themes import UI_THEME_PALETTES


def apply_rounded_mask(img: Image.Image, radius: int) -> Image.Image:
    img = img.convert("RGBA")
    if radius <= 0:
        return img
    mask = Image.new("L", img.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, img.size[0] - 1, img.size[1] - 1), radius, fill=255)
    rounded = Image.new("RGBA", img.size, (0, 0, 0, 0))
    rounded.paste(img, (0, 0), mask)
    return rounded


# Full-size decoded images are big (a 624x624 card is ~1.5 MB in memory, the binder panel
# ~17 MB). The old 512-entry cache could grow to ~800 MB, enough for Android to kill the
# bot on a phone. Keep only a few full-size images, and cache the small resized versions
# that renderers actually draw (see fit_cached_rgba_image).
@lru_cache(maxsize=48)
def load_rgba_image_cached(path_value: str) -> Image.Image:
    return Image.open(path_value).convert("RGBA")


@lru_cache(maxsize=384)
def _resized_rgba_image_cached(path_value: str, mtime_ns: int, size: Tuple[int, int], mode: str) -> Image.Image:
    with Image.open(path_value) as source:
        if source.format == "JPEG":
            # Let libjpeg decode at a reduced scale (still >= 2x the target) - much faster.
            try:
                source.draft("RGB", (size[0] * 2, size[1] * 2))
            except Exception:
                pass
        source = source.convert("RGBA")
    if mode == "contain":
        return ImageOps.contain(source, size, method=Image.Resampling.LANCZOS)
    return ImageOps.fit(source, size, method=Image.Resampling.LANCZOS)


def fit_cached_rgba_image(path_value: str | Path, size: Tuple[int, int], mode: str = "fit") -> Image.Image:
    """Return a resized copy of an image file, reusing earlier resizes of the same file.

    The file's modification time is part of the cache key, so an image that is replaced
    on disk (e.g. a re-approved profile background) is picked up automatically.
    """
    path_text = str(path_value)
    try:
        mtime_ns = os.stat(path_text).st_mtime_ns
    except OSError:
        mtime_ns = 0
    target = (max(1, int(size[0])), max(1, int(size[1])))
    return _resized_rgba_image_cached(path_text, mtime_ns, target, mode).copy()


@lru_cache(maxsize=128)
def blurred_round_rect_layer(
    layer_size: Tuple[int, int],
    rect: Tuple[int, int, int, int],
    radius: int,
    fill: Tuple[int, ...],
    blur_radius: float,
) -> Image.Image:
    """A soft shadow/glow layer. Cached because battle frames redraw identical shadows every turn."""
    layer = Image.new("RGBA", layer_size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(rect, radius, fill=fill)
    return layer.filter(ImageFilter.GaussianBlur(blur_radius))


def invalidate_rgba_image_cache() -> None:
    try:
        load_rgba_image_cached.cache_clear()
        _resized_rgba_image_cached.cache_clear()
    except Exception:
        pass


def clone_cached_rgba_image(path_value: str | Path) -> Image.Image:
    return load_rgba_image_cached(str(path_value)).copy()


def open_card_image(path: str, size: Tuple[int, int], radius: int = 0, grayscale: bool = False) -> Image.Image:
    try:
        img = fit_cached_rgba_image(path, size, "fit")
    except Exception:
        img = Image.new("RGBA", size, (72, 72, 78, 255))
        d = ImageDraw.Draw(img)
        fallback_radius = min(max(radius, 16), min(size) // 2)
        d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), fallback_radius, outline=(188, 188, 194, 255), width=3)
        d.text((size[0] // 2, size[1] // 2), "NO IMAGE", fill="white", font=FONT_S, anchor="mm")

    if grayscale:
        alpha = img.getchannel("A")
        gray = ImageOps.grayscale(img.convert("RGB"))
        img = Image.merge("RGBA", (gray, gray, gray, alpha))

    return apply_rounded_mask(img, radius)


def open_card_image_contain(path: str, size: Tuple[int, int], radius: int = 0, grayscale: bool = False, background: Tuple[int, int, int, int] = (36, 36, 40, 255)) -> Image.Image:
    try:
        contained = fit_cached_rgba_image(path, size, "contain")
        img = Image.new("RGBA", size, background)
        paste_x = (size[0] - contained.width) // 2
        paste_y = (size[1] - contained.height) // 2
        img.paste(contained, (paste_x, paste_y), contained)
    except Exception:
        img = Image.new("RGBA", size, (72, 72, 78, 255))
        d = ImageDraw.Draw(img)
        fallback_radius = min(max(radius, 16), min(size) // 2)
        d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), fallback_radius, outline=(188, 188, 194, 255), width=3)
        d.text((size[0] // 2, size[1] // 2), "NO IMAGE", fill="white", font=FONT_S, anchor="mm")

    if grayscale:
        alpha = img.getchannel("A")
        gray = ImageOps.grayscale(img.convert("RGB"))
        img = Image.merge("RGBA", (gray, gray, gray, alpha))

    return apply_rounded_mask(img, radius)


def open_card_image_contain_tight(path: str, size: Tuple[int, int], radius: int = 0, grayscale: bool = False) -> Image.Image:
    """Open an image using contain scaling without adding letterbox padding.

    This keeps the returned image at the actual contained dimensions so debug
    renderer outlines/masks wrap the visible card art instead of the full zone.
    """
    try:
        img = fit_cached_rgba_image(path, size, "contain")
    except Exception:
        img = Image.new("RGBA", size, (72, 72, 78, 255))
        d = ImageDraw.Draw(img)
        fallback_radius = min(max(radius, 16), min(size) // 2)
        d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), fallback_radius, outline=(188, 188, 194, 255), width=3)
        d.text((size[0] // 2, size[1] // 2), "NO IMAGE", fill="white", font=FONT_S, anchor="mm")

    if grayscale:
        alpha = img.getchannel("A")
        gray = ImageOps.grayscale(img.convert("RGB"))
        img = Image.merge("RGBA", (gray, gray, gray, alpha))

    return apply_rounded_mask(img, radius)


def open_card_image_square_cover(path: str, size: Tuple[int, int], radius: int = 0, grayscale: bool = False) -> Image.Image:
    """Open a monster image as a tight 1:1 cover crop without letterbox padding."""
    try:
        source = clone_cached_rgba_image(path)
        bbox = source.getbbox()
        if bbox:
            source = source.crop(bbox)
        img = ImageOps.fit(source, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    except Exception:
        img = Image.new("RGBA", size, (72, 72, 78, 255))
        d = ImageDraw.Draw(img)
        fallback_radius = min(max(radius, 16), min(size) // 2)
        d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), fallback_radius, outline=(188, 188, 194, 255), width=3)
        d.text((size[0] // 2, size[1] // 2), "NO IMAGE", fill="white", font=FONT_S, anchor="mm")

    if grayscale:
        alpha = img.getchannel("A")
        gray = ImageOps.grayscale(img.convert("RGB"))
        img = Image.merge("RGBA", (gray, gray, gray, alpha))

    return apply_rounded_mask(img, radius)


def set_image_alpha(img: Image.Image, alpha: int) -> Image.Image:
    alpha = max(0, min(255, int(alpha)))
    if alpha >= 255:
        return img
    updated = img.copy()
    existing_alpha = updated.getchannel("A")
    adjusted_alpha = existing_alpha.point(lambda px: (px * alpha) // 255)
    updated.putalpha(adjusted_alpha)
    return updated


def open_binder_canvas_background(size: Tuple[int, int], alpha: int = 255) -> Image.Image:
    try:
        background = fit_cached_rgba_image(BINDER_PANEL_IMAGE_PATH, size, "fit")
    except Exception:
        background = Image.new("RGBA", size, (24, 24, 28, 255))
    return set_image_alpha(background, alpha)


def _default_binder_panel_background(size: Tuple[int, int], radius: int = 34, alpha: int = BINDER_PANEL_ALPHA) -> Image.Image:
    panel = Image.new("RGBA", size, (34, 34, 38, 235))
    shade = Image.new("RGBA", size, (0, 0, 0, 0))
    shade_draw = ImageDraw.Draw(shade)
    shade_draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=(255, 255, 255, 14))
    panel = Image.alpha_composite(panel, shade)
    panel = apply_rounded_mask(panel, radius)
    return set_image_alpha(panel, alpha)


_ACTIVE_PANEL_USER_ID: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("_ACTIVE_PANEL_USER_ID", default=None)


class _PanelUserContext:
    def __init__(self, user_id: Optional[int]):
        self.user_id = int(user_id) if user_id is not None else None
        self._token: Optional[contextvars.Token] = None

    def __enter__(self):
        self._token = _ACTIVE_PANEL_USER_ID.set(self.user_id)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._token is not None:
            _ACTIVE_PANEL_USER_ID.reset(self._token)
            self._token = None


def open_binder_panel_background(size: Tuple[int, int], radius: int = 34, alpha: int = BINDER_PANEL_ALPHA, user_id: Optional[int] = None) -> Image.Image:
    resolved_user_id = user_id if user_id is not None else _ACTIVE_PANEL_USER_ID.get()
    if resolved_user_id is not None:
        background_path = db.get_background_image_path(int(resolved_user_id))
        if background_path:
            custom_panel = open_image_fill(background_path, size)
            if custom_panel is not None:
                tint = Image.new("RGBA", size, (12, 12, 18, 132))
                gloss = Image.new("RGBA", size, (0, 0, 0, 0))
                gloss_draw = ImageDraw.Draw(gloss)
                gloss_draw.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=(255, 255, 255, 18))
                custom_panel = Image.alpha_composite(custom_panel, tint)
                custom_panel = Image.alpha_composite(custom_panel, gloss)
                custom_panel = apply_rounded_mask(custom_panel, radius)
                return set_image_alpha(custom_panel, alpha)
    return _default_binder_panel_background(size, radius=radius, alpha=alpha)


def paste_binder_panel(base_img: Image.Image, outer: Tuple[int, int, int, int], radius: int = 34, user_id: Optional[int] = None) -> None:
    panel_size = (outer[2] - outer[0], outer[3] - outer[1])
    panel = open_binder_panel_background(panel_size, radius=radius, user_id=user_id)
    base_img.alpha_composite(panel, (outer[0], outer[1]))


def with_panel_user_context(resolver):
    """Decorator for render functions: draw panels with the player's custom background.

    `resolver` receives the render call's arguments and returns the id of the player whose
    background should be used (None = the default panel)."""
    def decorate(func):
        def wrapped(*args, **kwargs):
            try:
                user_id = resolver(*args, **kwargs)
            except Exception:
                user_id = None
            with _PanelUserContext(user_id):
                return func(*args, **kwargs)
        return wrapped
    return decorate


def open_image_fill(path_value: str | Path, size: Tuple[int, int], *, alpha: int = 255) -> Optional[Image.Image]:
    try:
        resolved = Path(path_value)
        image = fit_cached_rgba_image(resolved, size, "fit")
        return set_image_alpha(image, alpha)
    except Exception:
        return None


def paste_panel_background_image(
    base_img: Image.Image,
    outer: Tuple[int, int, int, int],
    image_path: str | Path,
    *,
    radius: int = 24,
    image_alpha: int = CAMPAIGN_PANEL_BG_ALPHA,
    tint_alpha: int = CAMPAIGN_PANEL_TINT_ALPHA,
    tint_rgb: Tuple[int, int, int] = (20, 14, 32),
) -> None:
    size = (outer[2] - outer[0], outer[3] - outer[1])
    bg = open_image_fill(image_path, size, alpha=image_alpha)
    if bg is None:
        return
    if tint_alpha > 0:
        tint = Image.new("RGBA", size, (*tint_rgb, tint_alpha))
        bg = Image.alpha_composite(bg, tint)
    bg = apply_rounded_mask(bg, radius)
    base_img.alpha_composite(bg, (outer[0], outer[1]))


def open_support_card_template(size: Tuple[int, int], radius: int = 18) -> Image.Image:
    if SUPPORT_CARD_IMAGE_PATH.exists():
        try:
            img = Image.open(SUPPORT_CARD_IMAGE_PATH).convert("RGBA")
            return apply_rounded_mask(ImageOps.fit(img, size, method=Image.Resampling.LANCZOS), radius)
        except Exception:
            pass
    img = Image.new("RGBA", size, (66, 66, 72, 255))
    d = ImageDraw.Draw(img)
    palette = UI_THEME_PALETTES["gray"]
    d.rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), 18, outline=(210, 210, 216, 255), width=3)
    d.text((size[0] // 2, size[1] // 2), "SUPPORT", font=FONT_M_BOLD, fill=palette["title"], anchor="mm")
    return apply_rounded_mask(img, radius)
