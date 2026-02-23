"""
Render tweet-style text to PNG using macOS native CoreText + AppKit.
Supports: Helvetica Neue font, Apple Color Emoji, optional profile header
with circular avatar, display name, blue verified badge, and @handle.
Usage: python render_text.py <json_config_path>
Prints the image height to stdout.
"""
import sys
import json
import math
import objc
import Cocoa


def hex_to_nscolor(hex_str: str) -> Cocoa.NSColor:
    h = hex_str.lstrip("#")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return Cocoa.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)


def draw_circle_clip(x, y, size):
    """Draw a circular clipping path."""
    path = Cocoa.NSBezierPath.bezierPathWithOvalInRect_(
        Cocoa.NSMakeRect(x, y, size, size)
    )
    return path


def draw_verified_badge(x, y, size):
    """Draw Twitter/X blue verified badge."""
    Cocoa.NSGraphicsContext.currentContext().saveGraphicsState()

    hex_to_nscolor("1D9BF0").setFill()
    badge_path = Cocoa.NSBezierPath.bezierPathWithOvalInRect_(
        Cocoa.NSMakeRect(x, y, size, size)
    )
    badge_path.fill()

    Cocoa.NSColor.whiteColor().setStroke()
    Cocoa.NSColor.whiteColor().setFill()

    cx, cy = x + size / 2, y + size / 2
    s = size * 0.22

    check = Cocoa.NSBezierPath.alloc().init()
    check.setLineWidth_(size * 0.12)
    check.setLineCapStyle_(Cocoa.NSLineCapStyleRound)
    check.setLineJoinStyle_(Cocoa.NSLineJoinStyleRound)
    check.moveToPoint_(Cocoa.NSMakePoint(cx - s * 1.1, cy - s * 0.1))
    check.lineToPoint_(Cocoa.NSMakePoint(cx - s * 0.2, cy - s * 0.9))
    check.lineToPoint_(Cocoa.NSMakePoint(cx + s * 1.3, cy + s * 0.9))
    check.stroke()

    Cocoa.NSGraphicsContext.currentContext().restoreGraphicsState()


def render(config: dict):
    text = config["text"]
    font_size = config["font_size"]
    max_width = config["max_width"]
    output_path = config["output_path"]
    bg_hex = config.get("bg_hex", "000000")
    profile = config.get("profile")

    body_font = Cocoa.NSFont.fontWithName_size_("HelveticaNeue", font_size)
    if body_font is None:
        body_font = Cocoa.NSFont.systemFontOfSize_(font_size)

    para = Cocoa.NSMutableParagraphStyle.alloc().init()
    para.setAlignment_(Cocoa.NSTextAlignmentLeft)
    para.setLineSpacing_(font_size * 0.45)

    body_attrs = {
        Cocoa.NSFontAttributeName: body_font,
        Cocoa.NSForegroundColorAttributeName: Cocoa.NSColor.whiteColor(),
        Cocoa.NSParagraphStyleAttributeName: para,
    }

    attr_str = Cocoa.NSAttributedString.alloc().initWithString_attributes_(text, body_attrs)

    padding_x = 48
    text_width = max_width - (padding_x * 2)
    bounding = attr_str.boundingRectWithSize_options_(
        Cocoa.NSMakeSize(text_width, 10000),
        Cocoa.NSStringDrawingUsesLineFragmentOrigin | Cocoa.NSStringDrawingUsesFontLeading,
    )

    padding_y = 48
    profile_section_h = 0
    avatar_size = 140

    if profile and profile.get("display_name"):
        profile_section_h = avatar_size + 36

    img_w = int(max_width)
    img_h = int(bounding.size.height + padding_y * 2 + profile_section_h + 10)

    rep = Cocoa.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, img_w, img_h, 8, 4, True, False, Cocoa.NSDeviceRGBColorSpace, 0, 0
    )

    ctx = Cocoa.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    Cocoa.NSGraphicsContext.saveGraphicsState()
    Cocoa.NSGraphicsContext.setCurrentContext_(ctx)

    bg_color = hex_to_nscolor(bg_hex)
    bg_color.setFill()
    Cocoa.NSRectFill(Cocoa.NSMakeRect(0, 0, img_w, img_h))

    # NSBitmapImageRep uses flipped=NO: origin is bottom-left
    # We draw from bottom up: body text first, then profile on top

    body_y = padding_y
    draw_rect = Cocoa.NSMakeRect(padding_x, body_y, text_width, bounding.size.height + 10)
    attr_str.drawInRect_(draw_rect)

    if profile and profile.get("display_name"):
        profile_base_y = body_y + bounding.size.height + 20

        avatar_x = padding_x
        avatar_y = profile_base_y

        avatar_path = profile.get("avatar_path")
        if avatar_path:
            avatar_img = Cocoa.NSImage.alloc().initWithContentsOfFile_(avatar_path)
            if avatar_img:
                Cocoa.NSGraphicsContext.currentContext().saveGraphicsState()
                clip = draw_circle_clip(avatar_x, avatar_y, avatar_size)
                clip.addClip()
                avatar_img.drawInRect_fromRect_operation_fraction_(
                    Cocoa.NSMakeRect(avatar_x, avatar_y, avatar_size, avatar_size),
                    Cocoa.NSZeroRect,
                    Cocoa.NSCompositingOperationSourceOver,
                    1.0,
                )
                Cocoa.NSGraphicsContext.currentContext().restoreGraphicsState()
            else:
                _draw_placeholder_avatar(avatar_x, avatar_y, avatar_size)
        else:
            _draw_placeholder_avatar(avatar_x, avatar_y, avatar_size)

        name_x = avatar_x + avatar_size + 20
        display_name = profile["display_name"]
        handle = profile.get("handle", "")

        name_font = Cocoa.NSFont.fontWithName_size_("HelveticaNeue-Bold", font_size * 1.1)
        if name_font is None:
            name_font = Cocoa.NSFont.boldSystemFontOfSize_(font_size * 1.1)

        name_attrs = {
            Cocoa.NSFontAttributeName: name_font,
            Cocoa.NSForegroundColorAttributeName: Cocoa.NSColor.whiteColor(),
        }
        name_str = Cocoa.NSAttributedString.alloc().initWithString_attributes_(display_name, name_attrs)
        name_size = name_str.size()

        name_y = avatar_y + avatar_size - name_size.height - 4
        if handle:
            name_y = avatar_y + avatar_size / 2 + 1

        name_str.drawAtPoint_(Cocoa.NSMakePoint(name_x, name_y))

        badge_size = name_size.height * 0.85
        badge_x = name_x + name_size.width + 6
        badge_y = name_y + (name_size.height - badge_size) / 2
        draw_verified_badge(badge_x, badge_y, badge_size)

        if handle:
            handle_font = Cocoa.NSFont.fontWithName_size_("HelveticaNeue", font_size * 0.85)
            if handle_font is None:
                handle_font = Cocoa.NSFont.systemFontOfSize_(font_size * 0.85)
            handle_attrs = {
                Cocoa.NSFontAttributeName: handle_font,
                Cocoa.NSForegroundColorAttributeName: hex_to_nscolor("71767B"),
            }
            handle_text = handle if handle.startswith("@") else f"@{handle}"
            handle_str = Cocoa.NSAttributedString.alloc().initWithString_attributes_(handle_text, handle_attrs)
            handle_y = name_y - handle_str.size().height - 2
            handle_str.drawAtPoint_(Cocoa.NSMakePoint(name_x, handle_y))

    Cocoa.NSGraphicsContext.restoreGraphicsState()

    png_data = rep.representationUsingType_properties_(Cocoa.NSBitmapImageFileTypePNG, {})
    png_data.writeToFile_atomically_(output_path, True)

    print(img_h)


def _draw_placeholder_avatar(x, y, size):
    """Draw a gray circle as avatar placeholder."""
    hex_to_nscolor("333333").setFill()
    circle = Cocoa.NSBezierPath.bezierPathWithOvalInRect_(
        Cocoa.NSMakeRect(x, y, size, size)
    )
    circle.fill()

    icon_font = Cocoa.NSFont.fontWithName_size_("HelveticaNeue", size * 0.5)
    if icon_font is None:
        icon_font = Cocoa.NSFont.systemFontOfSize_(size * 0.5)
    icon_attrs = {
        Cocoa.NSFontAttributeName: icon_font,
        Cocoa.NSForegroundColorAttributeName: hex_to_nscolor("888888"),
    }
    icon = Cocoa.NSAttributedString.alloc().initWithString_attributes_("\U0001F464", icon_attrs)
    icon_size = icon.size()
    icon.drawAtPoint_(Cocoa.NSMakePoint(
        x + (size - icon_size.width) / 2,
        y + (size - icon_size.height) / 2,
    ))


if __name__ == "__main__":
    config_path = sys.argv[1]
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    render(config)
