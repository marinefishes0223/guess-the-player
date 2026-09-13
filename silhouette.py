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
import math
import random
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
    Procedurally draw a night-stadium-style background: gradient sky with
    stars, a curved tiered crowd bowl with jersey-colour speckle, floodlight
    towers with lens-flare glow, an LED advertising fascia at pitch level,
    and a textured pitch with a boundary rope + crease lines.

    Everything here is drawn onto the *background* layer, which is composited
    BEFORE the player silhouette is pasted on top in compose_quiz_card — so
    none of it will ever cover the player's body regardless of how busy it
    gets. Keep new elements confined to this function so that guarantee holds.
    """
    W, H = size
    rnd = random.Random(42)

    img = Image.new("RGBA", size, (0, 0, 0, 255))
    px = img.load()

    # --- Sky gradient (deep navy -> warm horizon glow from the floodlights) ---
    top_color = (7, 10, 24)
    mid_color = (22, 28, 56)
    horizon_color = (64, 66, 88)
    horizon_y = int(H * horizon_ratio)
    for y in range(horizon_y):
        t = y / max(horizon_y - 1, 1)
        if t < 0.6:
            tt = t / 0.6
            r = top_color[0] + (mid_color[0] - top_color[0]) * tt
            g = top_color[1] + (mid_color[1] - top_color[1]) * tt
            b = top_color[2] + (mid_color[2] - top_color[2]) * tt
        else:
            tt = (t - 0.6) / 0.4
            r = mid_color[0] + (horizon_color[0] - mid_color[0]) * tt
            g = mid_color[1] + (horizon_color[1] - mid_color[1]) * tt
            b = mid_color[2] + (horizon_color[2] - mid_color[2]) * tt
        row_color = (int(r), int(g), int(b), 255)
        for x in range(W):
            px[x, y] = row_color

    # Scattered stars, faded out near the horizon glow
    for _ in range(120):
        x = rnd.randint(0, W - 1)
        y = rnd.randint(0, int(horizon_y * 0.75))
        b = rnd.randint(120, 255)
        fade = 1 - (y / (horizon_y * 0.75))
        a = int(b * (0.3 + 0.7 * fade))
        px[x, y] = (b, b, min(255, b + 10), a if False else 255)

    draw = ImageDraw.Draw(img, "RGBA")

    # --- Tiered crowd bowl (curved, not just a flat band), filled with
    # tiny cartoon spectators rather than plain colour speckle ---
    stand_h = int(H * 0.17)
    stand_top = horizon_y - stand_h
    tiers = 4
    jersey_colors = [
        (200, 40, 40), (40, 90, 200), (230, 200, 40),
        (40, 160, 90), (230, 230, 230), (150, 60, 180),
        (230, 120, 30), (60, 190, 210),
    ]
    skin_tones = [
        (255, 219, 172), (240, 184, 135), (198, 134, 66),
        (141, 85, 36), (255, 205, 148), (120, 72, 46),
    ]
    for tier in range(tiers):
        t0 = stand_top + int(stand_h * tier / tiers)
        t1 = stand_top + int(stand_h * (tier + 1) / tiers)
        tier_h = t1 - t0
        # Each tier sags slightly in the middle to suggest a curved bowl
        curve = int(stand_h * 0.06) * (tiers - tier)
        base_shade = 16 + tier * 5
        for x in range(0, W, 3):
            sag = int(curve * math.sin(math.pi * x / W))
            draw.rectangle([x, t0 + sag, x + 3, t1 + sag],
                           fill=(base_shade, base_shade, base_shade + 12, 255))

        # Tiny cartoon people: a round head sitting on a wider shoulder
        # blob, arranged in one or more seat rows per tier — wider than
        # tall, so it reads as a person, not a pencil or a bottle on a
        # shelf. Multiple rows fill the tier's height instead of leaving
        # empty space above a single row.
        depth = 0.75 + 0.35 * (tier / max(tiers - 1, 1))
        head_d = max(3, round(4.2 * depth))
        shoulder_w = round(head_d * 1.7)
        shoulder_h = round(head_d * 1.0)
        figure_h = round(head_d * 0.6 + shoulder_h)  # head overlaps shoulders
        row_pitch = figure_h + 2
        rows = max(1, tier_h // row_pitch)
        for row in range(rows):
            row_baseline = t1 - int(tier_h * 0.08) - row * row_pitch
            x = rnd.randint(1, 4) + (shoulder_w // 2 if row % 2 else 0)
            while x < W - shoulder_w:
                sag = int(curve * math.sin(math.pi * x / W))
                jitter = rnd.randint(-1, 1)
                bottom_y = row_baseline + sag + jitter
                top_y = bottom_y - figure_h
                if rnd.random() < 0.72:
                    body = rnd.choice(jersey_colors)
                    skin = rnd.choice(skin_tones)
                    shoulder_top = bottom_y - shoulder_h
                    draw.ellipse(
                        [x, shoulder_top, x + shoulder_w, shoulder_top + shoulder_h + head_d // 2],
                        fill=(*body, 255),
                    )
                    head_x0 = x + (shoulder_w - head_d) // 2
                    draw.ellipse(
                        [head_x0, top_y, head_x0 + head_d, top_y + head_d],
                        fill=(*skin, 255),
                    )
                    # occasional raised arm, like someone celebrating/waving
                    if rnd.random() < 0.1:
                        side = -1 if rnd.random() < 0.5 else 1
                        hand_x = x + shoulder_w // 2 + side * (shoulder_w // 2 + 3)
                        draw.line(
                            [(x + shoulder_w // 2, shoulder_top + 1), (hand_x, top_y - 2)],
                            fill=(*body, 255), width=1,
                        )
                # else: leave as an empty/dark seat for realism
                x += shoulder_w + rnd.randint(1, 3)



    # Thin tier-separator lines to give the crowd bowl some structure
    for tier in range(1, tiers):
        ty = stand_top + int(stand_h * tier / tiers)
        for x in range(0, W, 3):
            curve = int(stand_h * 0.06) * (tiers - tier)
            sag = int(curve * math.sin(math.pi * x / W))
            draw.point((x, ty + sag), fill=(10, 11, 20, 255))

    # Roof edge above the stands: a subtle, gently-curved line (not a hard
    # "eyebrow") that just reads as the underside of the stadium canopy
    roof_y = stand_top - int(H * 0.012)
    roof_pts = []
    for x in range(0, W + 1, 10):
        sag = int(H * 0.005 * math.sin(math.pi * x / W))
        roof_pts.append((x, roof_y - sag))
    for i in range(len(roof_pts) - 1):
        draw.line([roof_pts[i], roof_pts[i + 1]], fill=(12, 13, 24, 220), width=4)

    # --- LED advertising fascia at the base of the stands (pitch-side) ---
    fascia_h = int(H * 0.022)
    fascia_top = horizon_y - fascia_h
    ad_colors = [(20, 130, 200), (200, 40, 90), (240, 170, 20), (30, 160, 90)]
    seg_w = int(W / 9)
    for i in range(9):
        x0 = i * seg_w
        color = ad_colors[i % len(ad_colors)]
        draw.rectangle([x0, fascia_top, x0 + seg_w - 2, horizon_y], fill=(*color, 255))
    fascia_glow = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(fascia_glow).rectangle([0, fascia_top, W, horizon_y], fill=(255, 255, 255, 40))
    img = Image.alpha_composite(img, fascia_glow.filter(ImageFilter.GaussianBlur(10)))
    draw = ImageDraw.Draw(img, "RGBA")

    # --- Floodlight towers + lens-flare glow (kept near the edges, clear of centre) ---
    glow_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow_layer)
    for lx in (int(W * 0.07), int(W * 0.93)):
        top_y = int(H * 0.06)
        pole_bot = stand_top
        gdraw.rectangle([lx - 3, top_y + 30, lx + 3, pole_bot], fill=(150, 150, 160, 255))
        # cross braces
        for frac in (0.35, 0.6, 0.85):
            by = top_y + 30 + int((pole_bot - top_y - 30) * frac)
            gdraw.line([(lx - 16, by), (lx + 16, by)], fill=(120, 120, 130, 200), width=2)
        # soft halo only in this layer — the crisp rig panel is drawn later
        # on top so blurring the halo doesn't wash out the light grid
        gdraw.ellipse([lx - 55, top_y - 55, lx + 55, top_y + 35], fill=(255, 245, 200, 110))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(8))
    img = Image.alpha_composite(img, glow_layer)
    draw = ImageDraw.Draw(img, "RGBA")

    # Crisp floodlight rig panels, drawn after the blur so the bulb grid
    # stays sharp and readable instead of dissolving into a grey blob
    for lx in (int(W * 0.07), int(W * 0.93)):
        top_y = int(H * 0.06)
        rig_w, rig_h = 50, 32
        draw.rectangle([lx - rig_w // 2, top_y - rig_h, lx + rig_w // 2, top_y + 4],
                        fill=(35, 35, 38, 255), outline=(60, 60, 64, 255), width=1)
        for row in range(3):
            for col in range(5):
                bx = lx - rig_w // 2 + 7 + col * 9
                by = top_y - rig_h + 7 + row * 9
                draw.ellipse([bx - 3, by - 3, bx + 3, by + 3], fill=(255, 252, 220, 255))

    # --- Pitch / outfield ---
    grass_dark = (24, 68, 36)
    grass_light = (31, 86, 46)
    draw.rectangle([0, horizon_y, W, H], fill=grass_dark)
    stripe_h = 26
    for i, y in enumerate(range(horizon_y, H, stripe_h)):
        if i % 2 == 0:
            draw.rectangle([0, y, W, min(y + stripe_h, H)], fill=grass_light)

    # 30-yard fielding circle, centred on the pitch — realistic cricket-
    # ground detail that reads clearly without competing with the player
    center_x = W // 2
    circle_ry = int(H * 0.34)
    circle_rx = int(W * 0.62)
    circle_cy = H + int(circle_ry * 0.55)
    draw.arc(
        [center_x - circle_rx, circle_cy - circle_ry, center_x + circle_rx, circle_cy + circle_ry],
        start=195, end=345, fill=(255, 255, 255, 90), width=3,
    )

    # Boundary rope, just below the horizon line
    rope_y = horizon_y + int(H * 0.01)
    draw.line([(0, rope_y), (W, rope_y)], fill=(235, 235, 235, 200), width=4)

    # Subtle pitch strip + crease lines near the bottom, off-centre so the
    # player (pasted later, centred) reads as standing beside/on it rather
    # than the art fighting for the same space.
    pitch_w = int(W * 0.16)
    pitch_x0 = int(W * 0.5 - pitch_w / 2)
    draw.rectangle([pitch_x0, H - int(H * 0.05), pitch_x0 + pitch_w, H],
                   fill=(196, 172, 118, 255))
    crease_y = H - int(H * 0.045)
    draw.line([(pitch_x0, crease_y), (pitch_x0 + pitch_w, crease_y)], fill=(255, 255, 255, 220), width=2)

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

    # The silhouette is composited AFTER every background element above,
    # so nothing drawn in generate_stadium_background can ever cover it.
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


if __name__ == "__main__":
    # Quick manual preview of just the background, and of a full card using
    # a plain rectangle as a stand-in "player" cutout (no remove.bg call).
    bg = generate_stadium_background()
    bg.convert("RGB").save("stadium_preview.png", "PNG")

    stand_in = Image.new("RGBA", (300, 700), (0, 0, 0, 0))
    ImageDraw.Draw(stand_in).rounded_rectangle([60, 0, 240, 700], radius=60, fill=(20, 20, 20, 255))
    buf = io.BytesIO()
    stand_in.save(buf, "PNG")

    compose_quiz_card(
        cutout_bytes=buf.getvalue(),
        skip_removebg=True,
        output_path="quiz_card_preview.png",
    )
    print("Wrote stadium_preview.png and quiz_card_preview.png")