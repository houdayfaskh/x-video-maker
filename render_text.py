"""
Render tweet-style text card to PNG using Pillow (cross-platform).
Closely matches the X / Twitter dark-mode visual style.
Uses the Inter font family (bundled in fonts/).
Usage: python render_text.py <json_config_path>
Prints the image height to stdout.
"""
import sys
import json
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FONTS_DIR = Path(__file__).resolve().parent / "fonts"

# ── X / Twitter dark-mode palette ──────────────────────────────────
TEXT_PRIMARY = (231, 233, 234)       # #E7E9EA
TEXT_SECONDARY = (113, 118, 123)     # #71767B
BADGE_BLUE = (29, 155, 240)         # #1D9BF0
WHITE = (255, 255, 255)


def _try_load(paths, size):
    for p in paths:
        try:
            return ImageFont.truetype(str(p), size)
        except (OSError, IOError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def font_regular(size):
    return _try_load([
        FONTS_DIR / "Inter-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ], size)


def font_medium(size):
    return _try_load([
        FONTS_DIR / "Inter-Medium.ttf",
        FONTS_DIR / "Inter-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ], size)


def font_bold(size):
    return _try_load([
        FONTS_DIR / "Inter-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ], size)


def hex_to_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# ── Text wrapping ──────────────────────────────────────────────────
def wrap_lines(draw, text, font, max_w):
    lines = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        words = para.split()
        cur = ""
        for w in words:
            test = f"{cur} {w}".strip()
            if draw.textlength(test, font=font) <= max_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
    return lines


# ── Verified badge (X-style) ──────────────────────────────────────
def draw_verified(draw, cx, cy, r):
    """Draw the X verified badge centred at (cx, cy) with radius r."""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BADGE_BLUE)

    s = r * 0.45
    pts = [
        (cx - s * 0.85, cy - s * 0.05),
        (cx - s * 0.25, cy + s * 0.65),
        (cx + s * 0.95, cy - s * 0.60),
    ]
    draw.line(pts, fill=WHITE, width=max(2, round(r * 0.28)), joint="curve")


# ── Avatar helpers ─────────────────────────────────────────────────
def paste_avatar(img, path, x, y, size):
    try:
        av = Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
        av.putalpha(mask)
        img.paste(av, (x, y), av)
    except Exception:
        placeholder(ImageDraw.Draw(img), x, y, size)


def placeholder(draw, x, y, size):
    draw.ellipse([x, y, x + size, y + size], fill=(51, 51, 51))
    inner = int(size * 0.55)
    off = (size - inner) // 2
    draw.ellipse(
        [x + off, y + off - size // 10, x + off + inner, y + off + inner - size // 10],
        fill=(90, 90, 90),
    )
    body_w = int(size * 0.7)
    body_h = int(size * 0.35)
    bx = x + (size - body_w) // 2
    by = y + size - body_h - size // 8
    draw.ellipse([bx, by, bx + body_w, by + body_h], fill=(90, 90, 90))


# ── Main render ────────────────────────────────────────────────────
def render(config):
    text = config["text"]
    font_size = config["font_size"]
    max_width = config["max_width"]
    output_path = config["output_path"]
    bg_hex = config.get("bg_hex", "000000")
    profile = config.get("profile")

    # X/Twitter proportions (all relative to 1008px card width ≈ 3× mobile)
    pad_x = 48
    pad_top = 44
    pad_bottom = 44
    text_w = max_width - pad_x * 2

    body_size = font_size
    body_font = font_regular(body_size)
    line_gap = round(body_size * 0.40)
    line_h = body_size + line_gap

    # measure body text
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines = wrap_lines(tmp, text, body_font, text_w)
    body_block_h = len(lines) * line_h

    # profile section
    avatar_sz = 120
    avatar_name_gap = 28
    profile_h = 0
    if profile and profile.get("display_name"):
        profile_h = avatar_sz + 28  # avatar + gap below profile row

    img_w = int(max_width)
    img_h = int(pad_top + profile_h + body_block_h + pad_bottom)

    bg = hex_to_rgb(bg_hex)
    img = Image.new("RGBA", (img_w, img_h), bg + (255,))
    draw = ImageDraw.Draw(img)

    y = pad_top

    # ── Profile row ────────────────────────────────────────────────
    if profile and profile.get("display_name"):
        av_x, av_y = pad_x, y

        if profile.get("avatar_path"):
            paste_avatar(img, profile["avatar_path"], av_x, av_y, avatar_sz)
            draw = ImageDraw.Draw(img)
        else:
            placeholder(draw, av_x, av_y, avatar_sz)

        name_x = av_x + avatar_sz + avatar_name_gap
        display_name = profile["display_name"]
        handle_str = profile.get("handle", "")

        name_font = font_bold(round(body_size * 1.05))
        handle_font = font_regular(round(body_size * 0.88))

        name_bb = draw.textbbox((0, 0), display_name, font=name_font)
        name_w = name_bb[2] - name_bb[0]
        name_h = name_bb[3] - name_bb[1]

        if handle_str:
            handle_text = handle_str if handle_str.startswith("@") else f"@{handle_str}"
            h_bb = draw.textbbox((0, 0), handle_text, font=handle_font)
            h_h = h_bb[3] - h_bb[1]
            total_text_h = name_h + 6 + h_h
            name_y = av_y + (avatar_sz - total_text_h) // 2
            draw.text((name_x, name_y), display_name, fill=TEXT_PRIMARY, font=name_font)

            # verified badge
            badge_r = round(name_h * 0.42)
            badge_cx = name_x + name_w + 8 + badge_r
            badge_cy = name_y + name_h // 2
            draw_verified(draw, badge_cx, badge_cy, badge_r)

            # handle
            draw.text(
                (name_x, name_y + name_h + 6),
                handle_text,
                fill=TEXT_SECONDARY,
                font=handle_font,
            )
        else:
            name_y = av_y + (avatar_sz - name_h) // 2
            draw.text((name_x, name_y), display_name, fill=TEXT_PRIMARY, font=name_font)
            badge_r = round(name_h * 0.42)
            badge_cx = name_x + name_w + 8 + badge_r
            badge_cy = name_y + name_h // 2
            draw_verified(draw, badge_cx, badge_cy, badge_r)

        y += profile_h

    # ── Body text ──────────────────────────────────────────────────
    for i, line in enumerate(lines):
        draw.text((pad_x, y + i * line_h), line, fill=TEXT_PRIMARY, font=body_font)

    img.save(output_path, "PNG")
    print(img_h)


if __name__ == "__main__":
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        render(json.load(f))
