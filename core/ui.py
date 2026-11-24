"""Desktop palette and crisp, resolution-independent navigation glyphs."""
from PIL import Image, ImageDraw, ImageTk

BG = "#0B1118"
SIDEBAR = "#0E1721"
PANEL = "#121E2A"
PANEL_2 = "#1C2B3A"
BORDER = "#263747"
TEXT = "#EEF5F7"
MUTED = "#9BADC0"
ACCENT = "#65E2C4"
ACCENT_HOVER = "#91F0D9"
ACCENT_DARK = "#173B37"
GREEN = "#65E2C4"
RED = "#FF8F9B"
BLUE = "#88B9FF"
AMBER = "#F4C781"


def icon(name, color=MUTED, size=20, master=None):
    """Draw small UI symbols at 3x and downsample for clean Tk rendering."""
    scale = 3
    image = Image.new("RGBA", (size * scale, size * scale))
    draw = ImageDraw.Draw(image)
    def line(points, width=1.7):
        draw.line([(int(x * size * scale / 24), int(y * size * scale / 24)) for x, y in points],
                  fill=color, width=max(1, int(width * size * scale / 24)), joint="curve")
    def rectangle(box, radius=3):
        draw.rounded_rectangle([int(v * size * scale / 24) for v in box],
                               radius=int(radius * size * scale / 24), outline=color,
                               width=max(1, int(1.7 * size * scale / 24)))
    def ellipse(box):
        draw.ellipse([int(v * size * scale / 24) for v in box], outline=color,
                     width=max(1, int(1.7 * size * scale / 24)))
    if name in {"download", "Downloader"}:
        line([(12, 3), (12, 15)])
        line([(7, 10), (12, 15), (17, 10)])
        line([(4, 15), (4, 21), (20, 21), (20, 15)])
    elif name in {"History", "history"}:
        ellipse((4, 4, 21, 21))
        line([(12, 7), (12, 13), (16, 15)])
        line([(2, 3), (2, 9), (8, 9)])
    elif name in {"Settings", "settings"}:
        for x, y in [(7, 8), (17, 16)]:
            line([(x, 3), (x, 21)])
            ellipse((x - 3, y - 3, x + 3, y + 3))
    elif name in {"About", "info"}:
        ellipse((3, 3, 21, 21))
        line([(12, 10), (12, 17)])
        line([(12, 6), (12, 7)], width=2.5)
    elif name in {"Instagram", "instagram"}:
        rectangle((3, 3, 21, 21), 5)
        ellipse((8, 8, 16, 16))
        ellipse((17, 6, 18, 7))
    elif name == "Instagram Profile":
        ellipse((8, 3, 16, 11))
        line([(4, 21), (4, 18), (8, 14), (16, 14), (20, 18), (20, 21)])
    elif name == "Direct URLs":
        rectangle((3, 3, 21, 21))
        ellipse((7, 7, 10, 10))
        line([(4, 18), (10, 12), (14, 16), (17, 13), (21, 17)])
    elif name == "Manifest":
        line([(8, 4), (5, 4), (5, 10), (3, 12), (5, 14), (5, 20), (8, 20)])
        line([(16, 4), (19, 4), (19, 10), (21, 12), (19, 14), (19, 20), (16, 20)])
    elif name == "folder":
        line([(3, 20), (3, 5), (10, 5), (13, 8), (21, 8), (21, 20), (3, 20)])
    else:
        rectangle((4, 4, 20, 20))
    return ImageTk.PhotoImage(image.resize((size, size), Image.Resampling.LANCZOS), master=master)
