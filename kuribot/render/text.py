"""Text layout helpers: wrapping, fitting and rich battle-log text."""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

from PIL import ImageDraw

from kuribot.models import StatusEffect
from kuribot.cards.equip_spells import build_equip_spell_defs
from kuribot.render.fonts import (
    FONT_L_BOLD, FONT_M_BOLD, FONT_S_BOLD, FONT_XL_BOLD, FONT_XS_BOLD, get_bold_font, get_font,
)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> List[str]:

    words = text.split()
    if not words:
        return [""]
    lines: List[str] = []
    cur = words[0]
    for word in words[1:]:
        test = cur + " " + word
        if draw.textlength(test, font=font) <= max_width:
            cur = test
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines[:2]


def ellipsize_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    text = str(text)
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "..."
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]
    return (text.rstrip() + ellipsis) if text else ellipsis


def ellipsize_plain_text(text: str, max_length: int) -> str:
    text = str(text).strip()
    if len(text) <= max_length:
        return text
    if max_length <= 3:
        return text[:max_length]
    return text[: max_length - 3].rstrip() + "..."


def fit_wrapped_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int) -> List[str]:
    raw = str(text or "").replace("\r", "").strip()
    if not raw:
        return []
    paragraphs = [segment.strip() for segment in raw.split("\n")] or [raw]
    lines: List[str] = []
    for paragraph in paragraphs:
        if not paragraph:
            if lines and len(lines) < max_lines:
                lines.append("")
            continue
        words = paragraph.split()
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
                if len(lines) >= max_lines:
                    break
        if len(lines) >= max_lines:
            break
        lines.append(current)
        if len(lines) >= max_lines:
            break
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if lines:
        lines[-1] = ellipsize_text(draw, lines[-1], font, max_width)
    return lines


def draw_centered_text_block(
    draw: ImageDraw.ImageDraw,
    box: Tuple[int, int, int, int],
    lines: Sequence[str],
    fonts: Sequence,
    fills: Sequence[Tuple[int, int, int]],
    *,
    line_gap: int = 6,
    x_align: str = "left",
    x_padding: int = 0,
):
    if not lines:
        return
    metrics = []
    total_height = 0
    for idx, line in enumerate(lines):
        font = fonts[idx]
        bbox = draw.textbbox((0, 0), str(line), font=font)
        height = bbox[3] - bbox[1]
        metrics.append(height)
        total_height += height
    total_height += line_gap * (len(lines) - 1)
    y = box[1] + ((box[3] - box[1]) - total_height) / 2
    for idx, line in enumerate(lines):
        font = fonts[idx]
        fill = fills[idx]
        if x_align == "center":
            x = (box[0] + box[2]) / 2
            anchor = "mm"
            bbox = draw.textbbox((0, 0), str(line), font=font)
            height = bbox[3] - bbox[1]
            draw.text((x, y + height / 2), str(line), font=font, fill=fill, anchor=anchor)
        else:
            x = box[0] + x_padding
            draw.text((x, y), str(line), font=font, fill=fill)
        y += metrics[idx] + line_gap


def _font_is_bold(font) -> bool:
    if font in {FONT_XL_BOLD, FONT_L_BOLD, FONT_M_BOLD, FONT_S_BOLD, FONT_XS_BOLD}:
        return True
    try:
        name_info = font.getname()
        if len(name_info) >= 2 and "bold" in str(name_info[1]).lower():
            return True
    except Exception:
        pass
    return False


def _fit_text_block_fonts(
    draw: ImageDraw.ImageDraw,
    box: Tuple[int, int, int, int],
    lines: Sequence[str],
    fonts: Sequence,
    *,
    line_gap: int = 6,
    x_padding: int = 0,
    min_font_size: int = 11,
) -> Tuple[List, int]:
    if not lines:
        return list(fonts), line_gap

    box_width = max(1, box[2] - box[0] - x_padding)
    box_height = max(1, box[3] - box[1])
    font_specs = []
    for font in fonts:
        try:
            size = max(min_font_size, int(getattr(font, "size", min_font_size)))
        except Exception:
            size = min_font_size
        font_specs.append((size, _font_is_bold(font)))

    max_size = max((size for size, _ in font_specs), default=min_font_size)
    for target_max_size in range(max_size, min_font_size - 1, -1):
        scaled_fonts = []
        current_sizes = []
        for original_size, is_bold in font_specs:
            shrink_by = max_size - target_max_size
            current_size = max(min_font_size, original_size - shrink_by)
            current_sizes.append(current_size)
            scaled_fonts.append(get_bold_font(current_size) if is_bold else get_font(current_size))
        scale = min(current_sizes) / max(1, min(size for size, _ in font_specs))
        scaled_gap = max(2, int(round(line_gap * scale)))

        total_height = 0
        max_width = 0
        for idx, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), str(line), font=scaled_fonts[idx])
            width = bbox[2] - bbox[0]
            height = bbox[3] - bbox[1]
            total_height += height
            max_width = max(max_width, width)
        total_height += scaled_gap * max(0, len(lines) - 1)

        if total_height <= box_height and max_width <= box_width:
            return scaled_fonts, scaled_gap

    fallback_fonts = []
    for _, is_bold in font_specs:
        fallback_fonts.append(get_bold_font(min_font_size) if is_bold else get_font(min_font_size))
    return fallback_fonts, 2


def draw_evenly_spaced_text_block(
    draw: ImageDraw.ImageDraw,
    box: Tuple[int, int, int, int],
    lines: Sequence[str],
    fonts: Sequence,
    fills: Sequence[Tuple[int, int, int]],
    *,
    x_align: str = "left",
    x_padding: int = 0,
    min_gap: int = 3,
):
    """Draw a text stack with balanced vertical whitespace inside a panel."""
    entries = []
    for idx, line in enumerate(lines):
        if idx >= len(fonts) or idx >= len(fills):
            break
        clean_line = str(line or "").strip()
        if not clean_line:
            continue
        font = fonts[idx]
        bbox = draw.textbbox((0, 0), clean_line, font=font)
        height = max(1, bbox[3] - bbox[1])
        entries.append((clean_line, font, fills[idx], height))

    if not entries:
        return

    box_height = max(1, box[3] - box[1])
    total_text_height = sum(entry[3] for entry in entries)
    gap_count = len(entries) + 1
    gap = max(min_gap, (box_height - total_text_height) / gap_count)
    if total_text_height + (gap * gap_count) > box_height:
        gap = max(1, (box_height - total_text_height) / max(1, len(entries) - 1)) if len(entries) > 1 else max(1, (box_height - total_text_height) / 2)
        y = box[1] + max(0, (box_height - (total_text_height + gap * max(0, len(entries) - 1))) / 2)
    else:
        y = box[1] + gap

    for line, font, fill, height in entries:
        if x_align == "center":
            draw.text(((box[0] + box[2]) / 2, y + height / 2), line, font=font, fill=fill, anchor="mm")
        else:
            draw.text((box[0] + x_padding, y), line, font=font, fill=fill)
        y += height + gap


def split_bold_segments(text: str) -> List[Tuple[str, bool]]:
    segments: List[Tuple[str, bool]] = []
    parts = text.split("**")
    for idx, part in enumerate(parts):
        if not part:
            continue
        segments.append((part, idx % 2 == 1))
    return segments or [(text, False)]


def _support_text_spans(text: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    raw = str(text or "")
    lower = raw.lower()
    support_patterns = [
        r"support cards?",
        r"support deck",
        r"active support",
        r"no active support",
    ]
    for pattern in support_patterns:
        for match in re.finditer(pattern, lower, flags=re.IGNORECASE):
            spans.append((match.start(), match.end()))
    refresh_match = re.search(r"refreshed:\s*", raw, flags=re.IGNORECASE)
    if refresh_match:
        spans.append((refresh_match.end(), len(raw)))
    return spans


def _range_overlaps(spans: Sequence[Tuple[int, int]], start: int, end: int) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _tokenize_text_with_ranges(text: str) -> List[Tuple[str, int, int]]:
    tokens: List[Tuple[str, int, int]] = []
    for match in re.finditer(r"\S+|\s+", text):
        tokens.append((match.group(0), match.start(), match.end()))
    return tokens


def wrap_rich_text_lines(draw: ImageDraw.ImageDraw, text: str, normal_font, bold_font, max_width: int) -> List[str]:
    wrapped_lines: List[str] = []
    current_line = ""
    current_width = 0.0
    for segment, is_bold in split_bold_segments(str(text or "")):
        font = bold_font if is_bold else normal_font
        tokens = _tokenize_text_with_ranges(segment)
        pending_space = ""
        for token, _, _ in tokens:
            if not token:
                continue
            if token.isspace():
                pending_space = " "
                continue
            piece = f"{pending_space}{token}" if current_line else token
            piece_width = draw.textlength(piece, font=font)
            if current_line and current_width + piece_width > max_width:
                wrapped_lines.append(current_line)
                current_line = token
                current_width = draw.textlength(token, font=font)
            else:
                current_line += piece
                current_width += piece_width
            pending_space = ""
        if pending_space and current_line:
            current_line += pending_space
            current_width += draw.textlength(pending_space, font=font)
    if current_line or not wrapped_lines:
        wrapped_lines.append(current_line.rstrip())
    return [line.rstrip() for line in wrapped_lines if line.strip()] or [""]


def draw_rich_text(draw: ImageDraw.ImageDraw, position: Tuple[int, int], text: str, normal_font, bold_font, fill, bullet_prefix: str = "", support_fill: Optional[Tuple[int, int, int]] = None, positive_fill: Optional[Tuple[int, int, int]] = None, negative_fill: Optional[Tuple[int, int, int]] = None, equip_fill: Optional[Tuple[int, int, int]] = None):
    x, y = position
    cursor_x = x
    if bullet_prefix:
        draw.text((cursor_x, y), bullet_prefix, font=normal_font, fill=fill)
        cursor_x += draw.textlength(bullet_prefix, font=normal_font)
    support_spans = _support_text_spans(text) if support_fill is not None else []
    positive_terms = BATTLE_LOG_POSITIVE_TERMS if positive_fill is not None else set()
    negative_terms = BATTLE_LOG_NEGATIVE_TERMS if negative_fill is not None else set()
    equip_spell_names = BATTLE_LOG_EQUIP_SPELL_NAMES if equip_fill is not None else set()
    cursor_index = 0
    for segment, is_bold in split_bold_segments(text):
        font = bold_font if is_bold else normal_font
        segment_stripped = segment.strip()
        segment_fill = fill
        if segment_stripped:
            normalized_segment = re.sub(r"\s+", " ", segment_stripped).upper()
            if equip_fill is not None and normalized_segment in equip_spell_names:
                segment_fill = equip_fill
            elif positive_fill is not None and normalized_segment in positive_terms:
                segment_fill = positive_fill
            elif negative_fill is not None and normalized_segment in negative_terms:
                segment_fill = negative_fill
        for token, token_start, token_end in _tokenize_text_with_ranges(segment):
            token_fill = segment_fill
            absolute_start = cursor_index + token_start
            absolute_end = cursor_index + token_end
            token_key = token.strip(" .,!?:;()[]{}\"'“”‘’").upper()
            if support_fill is not None:
                if re.fullmatch(r"[+−-]?\d+%?", token.strip()):
                    token_fill = support_fill
                elif _range_overlaps(support_spans, absolute_start, absolute_end):
                    token_fill = support_fill
            if token_key:
                if equip_fill is not None and token_key in equip_spell_names:
                    token_fill = equip_fill
                elif positive_fill is not None and token_key in positive_terms:
                    token_fill = positive_fill
                elif negative_fill is not None and token_key in negative_terms:
                    token_fill = negative_fill
            draw.text((cursor_x, y), token, font=font, fill=token_fill)
            cursor_x += draw.textlength(token, font=font)
        cursor_index += len(segment)


BATTLE_LOG_EQUIP_SPELL_NAMES = {spell.name.upper() for spell in build_equip_spell_defs()}
BATTLE_LOG_POSITIVE_TERMS = {
    "IMMUNITY", "RECOVERY", "PROTECTION", "SHIELD", "TOKEN", "PRECOG", "LIFESTEAL",
    "REFLECT", "COUNTERATTACK", "COUNTER", "HEALS", "HEAL", "REVIVES", "REVIVE",
}
BATTLE_LOG_NEGATIVE_TERMS = {
    *(effect.value.upper() for effect in StatusEffect),
    "BOMB", "HEAL BLOCK", "MARK", "HEAL", "BLOCK", "DAMAGE", "DEFEATED", "KO", "KNOCKED OUT",
}
