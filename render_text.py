"""
Render tweet-style text to PNG using Pillow (cross-platform).
Supports: configurable font, optional profile header with circular avatar,
display name, blue verified badge, and @handle.
Usage: python render_text.py <json_config_path>
Prints the image height to stdout.
"""
import sys
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNSText.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
]

BOLD_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNSText-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
]


def _load_font(candidates, size):
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def load_font(size, bold=False):
    return _load_font(BOLD_FONT_CANDIDATES if bold else FONT_CANDIDATES, size)


def hex_to_rgb(hex_str):
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def wrap_text_to_lines(draw, text, font, max_width):
    """Word-wrap text so each line fits within max_width pixels."""
    lines = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        words = paragraph.split()
        current = ""
        for word in words:
            test = f"{current} {word}".strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def draw_verified_badge(draw, x, y, size):
    """Draw Twitter/X blue verified badge (circle + checkmark)."""
    badge_color = hex_to_rgb("1D9BF0")
    draw.ellipse([x, y, x + size, y + size], fill=badge_color)

    cx, cy = x + size / 2, y + size / 2
    s = size * 0.22
    points = [
        (cx - s * 1.1, cy + s * 0.1),
        (cx - s * 0.2, cy + s * 0.9),
        (cx + s * 1.3, cy - s * 0.9),
    ]
    draw.line(points, fill="white", width=max(2, int(size * 0.12)))


def paste_circular_avatar(img, avatar_path, x, y, size):
    """Paste a circularly-cropped avatar onto img."""
    try:
        avatar = Image.open(avatar_path).convert("RGBA")
        avatar = avatar.resize((size, size), Image.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
        avatar.putalpha(mask)
        img.paste(avatar, (x, y), avatar)
    except Exception:
        draw_placeholder_avatar(ImageDraw.Draw(img), x, y, size)


def draw_placeholder_avatar(draw, x, y, size):
    draw.ellipse([x, y, x + size, y + size], fill=hex_to_rgb("333333"))


def render(config):
    text = config["text"]
    font_size = config["font_size"]
    max_width = config["max_width"]
    output_path = config["output_path"]
    bg_hex = config.get("bg_hex", "000000")
    profile = config.get("profile")

    body_font = load_font(font_size)
    bold_font = load_font(int(font_size * 1.1), bold=True)
    handle_font = load_font(int(font_size * 0.85))

    padding_x = 48
    padding_y = 48
    text_width = max_width - padding_x * 2

    tmp_img = Image.new("RGB", (1, 1))
    tmp_draw = ImageDraw.Draw(tmp_img)

    lines = wrap_text_to_lines(tmp_draw, text, body_font, text_width)
    line_spacing = int(font_size * 0.45)
    line_height = font_size + line_spacing
    text_block_h = len(lines) * line_height

    avatar_size = 140
    profile_section_h = 0
    if profile and profile.get("display_name"):
        profile_section_h = avatar_size + 36

    img_w = int(max_width)
    img_h = int(text_block_h + padding_y * 2 + profile_section_h + 10)

    bg = hex_to_rgb(bg_hex)
    img = Image.new("RGBA", (img_w, img_h), bg + (255,))
    draw = ImageDraw.Draw(img)

    cur_y = padding_y

    if profile and profile.get("display_name"):
        avatar_x = padding_x
        avatar_y = cur_y

        avatar_path = profile.get("avatar_path")
        if avatar_path:
            paste_circular_avatar(img, avatar_path, avatar_x, avatar_y, avatar_size)
            draw = ImageDraw.Draw(img)
        else:
            draw_placeholder_avatar(draw, avatar_x, avatar_y, avatar_size)

        name_x = avatar_x + avatar_size + 20
        display_name = profile["display_name"]
        handle = profile.get("handle", "")

        name_bbox = draw.textbbox((0, 0), display_name, font=bold_font)
        name_w = name_bbox[2] - name_bbox[0]
        name_h = name_bbox[3] - name_bbox[1]

        if handle:
            name_y = avatar_y + avatar_size // 2 - name_h - 4
        else:
            name_y = avatar_y + (avatar_size - name_h) // 2

        draw.text((name_x, name_y), display_name, fill="white", font=bold_font)

        badge_size = int(name_h * 0.85)
        badge_x = name_x + name_w + 8
        badge_y = name_y + (name_h - badge_size) // 2
        draw_verified_badge(draw, badge_x, badge_y, badge_size)

        if handle:
            handle_text = handle if handle.startswith("@") else f"@{handle}"
            handle_y = name_y + name_h + 4
            draw.text(
                (name_x, handle_y),
                handle_text,
                fill=hex_to_rgb("71767B"),
                font=handle_font,
            )

        cur_y += profile_section_h

    for i, line in enumerate(lines):
        y = cur_y + i * line_height
        draw.text((padding_x, y), line, fill="white", font=body_font)

    img.convert("RGB").save(output_path, "PNG")
    print(img_h)


if __name__ == "__main__":
    config_path = sys.argv[1]
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    render(config)
