import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1] / "assets"


def cover(name, background, accent, kind):
    image = Image.new("RGB", (1000, 560), background)
    draw = ImageDraw.Draw(image)
    for i in range(14):
        x = 440 + i * 44
        draw.line((x, 0, x - 290, 560), fill=accent, width=2)
    if kind == "cinema":
        for radius in range(210, 25, -23):
            draw.ellipse(
                (660 - radius, 280 - radius, 660 + radius, 280 + radius), outline=accent, width=5
            )
        draw.polygon([(617, 201), (617, 359), (748, 280)], fill="#f7e9cf")
        title, subtitle = "ОРБИТА", "КИНО / 01"
    elif kind == "concert":
        for i in range(36):
            h = 30 + int(190 * abs(math.sin(i * 0.38)))
            x = 400 + 15 * i
            draw.rounded_rectangle((x, 280 - h, x + 8, 280 + h), 4, fill=accent)
        title, subtitle = "ПОСЛЕ ЗАКАТА", "МУЗЫКА / 02"
    else:
        for x, y, r in [(650, 220, 145), (780, 300, 120), (570, 350, 85)]:
            draw.ellipse((x - r, y - r, x + r, y + r), outline=accent, width=8)
        title, subtitle = "ГОРОД БУДУЩЕГО", "ЛЕКЦИИ / 03"
    font_path = Path("C:/Windows/Fonts/segoeuib.ttf")
    font = (
        ImageFont.truetype(str(font_path), 57)
        if font_path.exists()
        else ImageFont.load_default(size=57)
    )
    small = (
        ImageFont.truetype(str(font_path), 22)
        if font_path.exists()
        else ImageFont.load_default(size=22)
    )
    draw.text((105, 55), "EVENTSEAT", font=small, fill="#ffffff")
    draw.text((105, 397), title, font=font, fill="#ffffff")
    draw.text((109, 479), subtitle, font=small, fill=accent)
    image.save(ROOT / f"{name}.png", optimize=True)


if __name__ == "__main__":
    ROOT.mkdir(exist_ok=True)
    cover("cinema", "#152f46", "#4ebfb1", "cinema")
    cover("concert", "#302644", "#c898f0", "concert")
    cover("lecture", "#43301f", "#edb876", "lecture")
    icon = Image.new("RGBA", (256, 256), "#152f46")
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((55, 50, 201, 164), 24, fill="#4ebfb1")
    draw.rounded_rectangle((40, 130, 216, 201), 18, fill="#4ebfb1")
    draw.rectangle((60, 193, 79, 221), fill="#4ebfb1")
    draw.rectangle((177, 193, 196, 221), fill="#4ebfb1")
    draw.line((66, 158, 190, 158), fill="#152f46", width=9)
    icon.save(
        ROOT / "eventseat.ico",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
