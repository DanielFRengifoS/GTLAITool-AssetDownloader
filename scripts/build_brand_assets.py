"""Typeset the logo around the generated mark and export Windows icon sizes.

The mark is preserved. Typography is rendered locally for exact, clean lettering.
Run: python scripts/build_brand_assets.py [--font-dir C:/Windows/Fonts]
"""
import argparse
import base64
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def build(font_dir: Path):
    assets = Path(__file__).resolve().parents[1] / "assets"
    mark_path = assets / "GTL_AI_Asset_Downloader.png"
    bold = ImageFont.truetype(str(font_dir / "segoeuib.ttf"), 100)
    regular = ImageFont.truetype(str(font_dir / "segoeui.ttf"), 31)
    with Image.open(mark_path) as source:
        mark = source.convert("RGBA")
    mark.save(assets / "GTL_AI_Asset_Downloader.ico", format="ICO",
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    logo = Image.new("RGBA", (820, 360))
    logo.alpha_composite(mark.resize((300, 300), Image.Resampling.LANCZOS), (30, 30))
    draw = ImageDraw.Draw(logo)
    draw.text((380, 59), "GTL AI", font=bold, fill="#EEF5F7")
    x = 387.0
    for letter in "ASSET DOWNLOADER":
        draw.text((x, 202), letter, font=regular, fill="#65E2C4")
        x += draw.textlength(letter, font=regular) + 5
    logo.save(assets / "GTL_AI_logo.png", optimize=True)
    encoded = base64.b64encode(mark_path.read_bytes()).decode("ascii")
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="820" height="360" viewBox="0 0 820 360" role="img" aria-label="GTL AI Asset Downloader">
  <title>GTL AI Asset Downloader</title>
  <image x="30" y="30" width="300" height="300" href="data:image/png;base64,{encoded}"/>
  <text x="380" y="165" fill="#EEF5F7" font-family="Segoe UI, Arial, sans-serif" font-size="100" font-weight="700">GTL AI</text>
  <text x="387" y="238" fill="#65E2C4" font-family="Segoe UI, Arial, sans-serif" font-size="31" letter-spacing="5">ASSET DOWNLOADER</text>
</svg>
'''
    (assets / "GTL_AI_logo.svg").write_text(svg, encoding="utf-8")
    print(f"Brand assets written to {assets}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font-dir", type=Path, default=Path("C:/Windows/Fonts"))
    build(parser.parse_args().font_dir)
