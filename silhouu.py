"""
silhouette.py
Handles:
  1. Calling the remove.bg API to cut a player out of a photo.
  2. Turning that cutout into a glowing white silhouette.
  3. Compositing it onto a stadium background with title / credit banners,
     to produce a "GUESS THE CRICKETER" style card.
"""

import io
import os
import requests
from PIL import Image, ImageFilter, ImageDraw, ImageFont

REMOVE_BG_API_URL = "https://api.remove.bg/v1.0/removebg"


class RemoveBgError(Exception):
    pass


def remove_background(image_bytes: bytes, api_key: str) -> Image.Image:
    """Send image bytes to remove.bg and return an RGBA cutout (transparent bg)."""
    response = requests.post(
        REMOVE_BG_API_URL,
        files={"image_file": image_bytes},
        data={"size": "auto"},
        headers={"X-Api-Key": api_key},
        timeout=30,
    )
    if response.status_code != 200:
        raise RemoveBgError(f"remove.bg error {response.status_code}: {response.text}")
    return Image.open(io.BytesIO(response.content)).convert("RGBA")


def make_glow_silhouette(cutout: Image.Image, glow_blur: int = 22) -> Image.Image:
    """Turn an RGBA cutout into a solid white silhouette with a soft glow halo."""
    pad = glow_blur * 2
    padded_size = (cutout.width + pad * 2, cutout.height + pad * 2)

    padded_cutout = Image.new("RGBA", padded_size, (0, 0, 0, 0))
    padded_cutout.paste(cutout, (pad, pad), cutout)

    alpha = padded_cutout.split()[-1]
    solid_white = Image.new("RGBA", padded_size, (238, 235, 233, 255))
    solid_white.putalpha(alpha)

    glow = solid_white.filter(ImageFilter.GaussianBlur(glow_blur))

    canvas = Image.new("RGBA", padded_size, (0, 0, 0, 0))
    canvas = Image.alpha_composite(canvas, glow)
    canvas = Image.alpha_composite(canvas, glow)  # double pass = stronger halo
    canvas = Image.alpha_composite(canvas, solid_white)
    return canvas


def _autocrop(img: Image.Image) -> Image.Image:
    """Crop transparent padding around the subject so scaling/placement is predictable."""
    bbox = img.getbbox()
    return img.crop(bbox) if bbox else img


def _load_font(size: int, bold: bool = True):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    # IMPORTANT: ImageFont.load_default() is a fixed-size bitmap font on
    # older Pillow and silently ignores `size` — this is why bumping
    # font_size ratios elsewhere in this file had no visible effect if
    # none of the paths above exist on this host. Pillow >= 9.2 lets you
    # pass a size to load_default(); use it when available so text still
    # scales even without a real TTF on disk.
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def generate_stadium_background(size=(735, 941), horizon_ratio: float = 0.62) -> Image.Image:
    """
    Procedurally draw a night-stadium-style background (gradient sky, crowd
    stands, floodlights, pitch) so the bot never needs a manually-supplied
    background photo to work.
    """
    W, H = size
    img = Image.new("RGBA", size, (0, 0, 0, 255))
    px = img.load()

    top_color = (10, 14, 32)
    horizon_color = (46, 58, 92)
    horizon_y = int(H * horizon_ratio)
    for y in range(horizon_y):
        t = y / max(horizon_y - 1, 1)
        r = int(top_color[0] + (horizon_color[0] - top_color[0]) * t)
        g = int(top_color[1] + (horizon_color[1] - top_color[1]) * t)
        b = int(top_color[2] + (horizon_color[2] - top_color[2]) * t)
        for x in range(W):
            px[x, y] = (r, g, b, 255)

    draw = ImageDraw.Draw(img)

    grass_dark = (28, 74, 40)
    grass_light = (36, 92, 50)
    draw.rectangle([0, horizon_y, W, H], fill=grass_dark)
    stripe_h = 22
    for i, y in enumerate(range(horizon_y, H, stripe_h)):
        if i % 2 == 0:
            draw.rectangle([0, y, W, min(y + stripe_h, H)], fill=grass_light)

    stand_h = int(H * 0.10)
    draw.rectangle([0, horizon_y - stand_h, W, horizon_y], fill=(15, 18, 30))
    import random
    rnd = random.Random(42)
    for _ in range(500):
        x = rnd.randint(0, W - 1)
        y = rnd.randint(horizon_y - stand_h, horizon_y - 4)
        shade = rnd.randint(35, 70)
        draw.point((x, y), fill=(shade, shade, shade + 10, 255))

    glow_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow_layer)
    for lx in (int(W * 0.08), int(W * 0.92)):
        top_y = int(H * 0.08)
        gdraw.rectangle([lx - 3, top_y, lx + 3, horizon_y - stand_h], fill=(180, 180, 190, 255))
        gdraw.ellipse([lx - 40, top_y - 40, lx + 40, top_y + 40], fill=(255, 250, 210, 180))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(6))
    img = Image.alpha_composite(img, glow_layer)

    return img


def generate_flat_background(size=(735, 941), color=(238, 235, 233, 255)) -> Image.Image:
    """A plain flat-color background, useful for isolating whether weird
    output comes from the stadium generator or from the silhouette/composite
    step itself."""
    return Image.new("RGBA", size, color)


def compose_quiz_card(
    background_path: str = None,
    cutout_bytes: bytes = None,
    api_key: str = None,
    output_path: str = "quiz_card.png",
    title_main: str = "GUESS THE",
    title_highlight: str = "CRICKETER",
    credit: str = "BY :- CricDex",
    highlight_color=(80, 255, 60),
    skip_removebg: bool = False,
    background_image: Image.Image = None,
) -> str:
    """
    Build the full card:
      background stadium photo -> glowing player silhouette ->
      top title bar -> bottom credit bar.

    Background resolution order:
      1. `background_image` (a PIL Image), if given.
      2. `background_path`, if given and the file exists.
      3. A procedurally generated stadium background (no file needed).

    If skip_removebg=True, cutout_bytes is treated as an already-transparent
    PNG (useful for testing without hitting the API).
    """
    if background_image is not None:
        bg = background_image.convert("RGBA")
    elif background_path and os.path.exists(background_path):
        bg = Image.open(background_path).convert("RGBA")
    else:
        bg = generate_stadium_background()
    W, H = bg.size

    if skip_removebg:
        cutout = Image.open(io.BytesIO(cutout_bytes)).convert("RGBA")
    else:
        cutout = remove_background(cutout_bytes, api_key)

    cutout = _autocrop(cutout)
    silhouette = make_glow_silhouette(cutout)

    title_bar_h = int(H * 0.09)
    footer_h = int(H * 0.07)
    margin_top = int(H * 0.035)
    margin_bottom = int(H * 0.02)

    safe_top = title_bar_h + margin_top
    safe_bottom = H - footer_h - margin_bottom
    safe_h = safe_bottom - safe_top
    safe_w = int(W * 0.85)

    scale_h = safe_h / silhouette.height
    scale_w = safe_w / silhouette.width
    scale = min(scale_h, scale_w)

    target_w = max(1, int(silhouette.width * scale))
    target_h = max(1, int(silhouette.height * scale))

    # Horizontal-only stretch: widens the player without changing height.
    # 1.0 = normal aspect ratio, 1.2 = 20% wider (same height), etc.
    # Pushed too far this will visibly distort/stretch the silhouette.
    width_stretch = 1.2
    target_w = max(1, int(target_w * width_stretch))

    silhouette = silhouette.resize((target_w, target_h), Image.LANCZOS)

    canvas = bg.copy()

    feet_y = safe_bottom
    paste_x = (W - target_w) // 2
    paste_y = feet_y - target_h
    paste_y = max(paste_y, safe_top)

    canvas.alpha_composite(silhouette, (paste_x, paste_y))
    # NOTE: stumps removed per request (previously drawn here with draw_stumps)

    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, W, title_bar_h], fill=(10, 10, 10, 235))

    font_size = int(title_bar_h * 0.55)  # was 0.85 — smaller title text, adjust ratio to taste
    font = _load_font(font_size)

    x_cursor = int(W * 0.03)
    y_text = int(title_bar_h * 0.07)
    draw.text((x_cursor, y_text), title_main, font=font, fill=(255, 255, 255, 255))
    main_w = draw.textlength(title_main + " ", font=font)
    draw.text((x_cursor + main_w, y_text), title_highlight, font=font, fill=highlight_color)

    draw.rectangle([0, H - footer_h, W, H], fill=(10, 10, 10, 235))
    draw.rectangle([0, H - footer_h, int(W * 0.01) + 6, H], fill=highlight_color)
    footer_font = _load_font(int(footer_h * 0.8))
    draw.text(
        (int(W * 0.03), H - footer_h + int(footer_h * 0.09)),
        credit,
        font=footer_font,
        fill=(255, 255, 255, 255),
    )

    canvas.convert("RGB").save(output_path, "PNG")
    return output_path