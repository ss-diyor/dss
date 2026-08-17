from __future__ import annotations

from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont

W, H = 1000, 1250
NAVY = "#0D2D61"
BLUE = "#1455A4"
MUTED = "#5B6B83"
PANEL = "#F0F5FC"
LINE = "#D8E2F0"
GREEN = "#218A3A"
GREEN_BG = "#E8F7EA"
GOLD = "#C99300"
GOLD_BG = "#FFF7DE"
WHITE = "#FFFFFF"

ROOT = Path(__file__).resolve().parent
FONT_DIRS = [
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation2"),
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"]
    for directory in FONT_DIRS:
        for name in names:
            path = directory / name
            if path.exists():
                return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int = 2) -> str:
    text = str(text or "—").strip()
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) <= max_lines:
        return "\n".join(lines)
    lines = lines[:max_lines]
    lines[-1] = lines[-1].rstrip(" .,;:") + "…"
    return "\n".join(lines)


def _fit_single_line(draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int = 27, min_size: int = 18, bold: bool = True) -> tuple[str, ImageFont.FreeTypeFont]:
    text = str(text or "—").strip()
    size = start_size
    while size >= min_size:
        font = _font(size, bold=bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return text, font
        size -= 1
    font = _font(min_size, bold=bold)
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text, font
    shortened = text
    while len(shortened) > 1 and draw.textbbox((0, 0), shortened + "…", font=font)[2] > max_width:
        shortened = shortened[:-1]
    return shortened.rstrip() + "…", font


def _center(draw: ImageDraw.ImageDraw, text: str, y: int, font: ImageFont.FreeTypeFont, fill: str) -> None:
    box = draw.multiline_textbbox((0, 0), text, font=font, align="center", spacing=4)
    x = (W - (box[2] - box[0])) // 2
    draw.multiline_text((x, y), text, font=font, fill=fill, align="center", spacing=4)


def _icon_circle(draw: ImageDraw.ImageDraw, center: tuple[int, int], kind: str, color: str = BLUE) -> None:
    x, y = center
    draw.ellipse((x - 46, y - 46, x + 46, y + 46), fill=WHITE, outline="#DCE7F5", width=2)
    if kind == "building":
        draw.polygon([(x - 25, y - 12), (x, y - 29), (x + 25, y - 12)], outline=color, width=5)
        draw.rectangle((x - 25, y - 10, x + 25, y + 25), outline=color, width=5)
        for dx in (-14, 0, 14):
            draw.line((x + dx, y - 5, x + dx, y + 23), fill=color, width=4)
        draw.line((x - 30, y + 27, x + 30, y + 27), fill=color, width=5)
    elif kind == "book":
        draw.line((x, y - 25, x, y + 27), fill=color, width=4)
        draw.polygon([(x - 29, y - 20), (x - 3, y - 26), (x - 3, y + 23), (x - 29, y + 16)], outline=color, width=4)
        draw.polygon([(x + 29, y - 20), (x + 3, y - 26), (x + 3, y + 23), (x + 29, y + 16)], outline=color, width=4)
    elif kind == "calendar":
        draw.rounded_rectangle((x - 27, y - 22, x + 27, y + 26), radius=6, outline=color, width=4)
        draw.line((x - 27, y - 6, x + 27, y - 6), fill=color, width=4)
        draw.line((x - 14, y - 30, x - 14, y - 14), fill=color, width=5)
        draw.line((x + 14, y - 30, x + 14, y - 14), fill=color, width=5)
        for dx, dy in [(-13, 7), (2, 7), (17, 7), (-13, 18), (2, 18)]:
            draw.ellipse((x + dx - 2, y + dy - 2, x + dx + 2, y + dy + 2), fill=color)
    elif kind == "wallet":
        draw.rounded_rectangle((x - 29, y - 20, x + 27, y + 22), radius=6, outline=color, width=4)
        draw.line((x - 18, y - 20, x - 8, y - 30, x + 28, y - 30), fill=color, width=4)
        draw.line((x + 8, y + 2, x + 28, y + 2), fill=color, width=4)
        draw.ellipse((x + 11, y - 3, x + 16, y + 2), fill=color)


def _draw_info_row(draw: ImageDraw.ImageDraw, y: int, icon: str, label: str, value: str, x: int = 150, width: int = 790) -> int:
    _icon_circle(draw, (x, y + 2), icon)
    label_font = _font(24)
    value_font = _font(28, bold=True)
    draw.text((x + 78, y - 30), label, font=label_font, fill=MUTED)
    value_text = _fit_text(draw, value, value_font, width - 98, max_lines=2)
    draw.multiline_text((x + 78, y + 3), value_text, font=value_font, fill=NAVY, spacing=4)
    line_y = y + 84 if "\n" not in value_text else y + 116
    draw.line((x + 78, line_y, x + width, line_y), fill=LINE, width=2)
    return line_y + 56


def render_result_card(data: dict, output_path: str | Path) -> str:
    """Render a shareable 4:5 result card containing only the accepted university."""
    image = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(image)
    result_status = str(data.get("result_status") or "")
    is_grant = result_status == "grant"
    accent = GREEN if is_grant else GOLD
    accent_bg = GREEN_BG if is_grant else GOLD_BG
    accepted = data.get("accepted_choice") or {}
    university = accepted.get("university") or "—"
    direction = accepted.get("direction") or "—"
    education_form = accepted.get("education_form") or "—"
    raw_status_text = str(accepted.get("status_text") or "").strip().lower()
    if is_grant or "grant" in raw_status_text:
        status_text = "Davlat granti"
    elif "kontrakt" in raw_status_text or "shartnoma" in raw_status_text:
        status_text = "To‘lov shartnoma"
    else:
        status_text = "To‘lov shartnoma"
    score = data.get("score") or "—"
    name = data.get("name") or "—"
    entrant_id = data.get("id") or "—"

    draw.rounded_rectangle((22, 22, W - 22, H - 22), radius=28, fill=WHITE, outline="#E5EAF1", width=3)
    _center(draw, "MANDAT NATIJASI", 95, _font(52, bold=True), NAVY)
    _center(draw, str(name).upper(), 175, _font(36, bold=True), NAVY)
    _center(draw, f"ID: {entrant_id}", 225, _font(25), MUTED)

    draw.rounded_rectangle((330, 285, 670, 420), radius=22, fill="#F3F7FD", outline="#D8E4F5", width=2)
    _center(draw, str(score), 300, _font(76, bold=True), NAVY)
    _center(draw, "BALL", 385, _font(25, bold=True), BLUE)

    draw.rounded_rectangle((105, 445, W - 105, 510), radius=30, fill=accent_bg, outline=accent, width=2)
    draw.ellipse((140, 460, 190, 510), fill=accent)
    draw.line((153, 484, 165, 496), fill=WHITE, width=5)
    draw.line((165, 496, 181, 474), fill=WHITE, width=5)
    status_label = "DAVLAT GRANTI ASOSIDA QABUL" if is_grant else "TO‘LOV-KONTRAKT ASOSIDA QABUL"
    banner_text, banner_font = _fit_single_line(draw, status_label, 640, start_size=25, min_size=20)
    draw.text((215, 466), banner_text, font=banner_font, fill=accent)

    panel_top, panel_bottom = 555, 1085
    draw.rounded_rectangle((70, panel_top, W - 70, panel_bottom), radius=22, fill=PANEL, outline="#DCE6F3", width=2)
    y = panel_top + 72
    y = _draw_info_row(draw, y, "building", "Qabul qilingan OTM", str(university), x=145, width=785)
    y = _draw_info_row(draw, y, "book", "Yo‘nalish", str(direction), x=145, width=785)

    left_x, right_x = 165, 610
    _icon_circle(draw, (left_x, y + 4), "calendar")
    draw.text((left_x + 75, y - 28), "Ta’lim shakli:", font=_font(22), fill=MUTED)
    education_value, education_font = _fit_single_line(draw, education_form, 245, start_size=26, min_size=21)
    draw.text((left_x + 75, y + 5), education_value, font=education_font, fill=NAVY)
    draw.line((left_x + 75, y + 68, 475, y + 68), fill=LINE, width=2)
    draw.line((500, y - 30, 500, y + 82), fill=LINE, width=2)
    _icon_circle(draw, (right_x, y + 4), "wallet")
    draw.text((right_x + 75, y - 28), "Qabul turi:", font=_font(22), fill=MUTED)
    admission_value, admission_font = _fit_single_line(draw, status_text, 245, start_size=25, min_size=18)
    draw.text((right_x + 75, y + 5), admission_value, font=admission_font, fill=NAVY)
    draw.line((right_x + 75, y + 68, 930, y + 68), fill=LINE, width=2)

    footer = "mandat.uzbmb.uz | @mandat_applicant_ratingbot"
    footer_font = _font(22)
    box = draw.textbbox((0, 0), footer, font=footer_font)
    draw.text(((W - (box[2] - box[0])) // 2, 1165), footer, font=footer_font, fill=MUTED)

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG", optimize=True)
    return str(output)
