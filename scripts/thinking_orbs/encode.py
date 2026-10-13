"""Encode upstream renderer PNGs as native-client animated WebP assets."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
output = ROOT / 'assets/thinking-orbs'
for folder in sorted((ROOT / 'work/orb-frames').iterdir()):
    if folder.name.endswith('-composing'):
        continue
    images = [Image.open(p).convert('RGBA') for p in sorted(folder.glob('*.png'))]
    target = output / (folder.name + '.webp')
    images[0].save(target, save_all=True, append_images=images[1:], duration=42,
                   loop=0, lossless=True, method=4)
    for img in images:
        img.close()
    print(target.name, target.stat().st_size)
for theme, color in [('dark', '#FF94AA'), ('light', '#B83250')]:
    image = Image.new('RGBA', (192, 192))
    draw = ImageDraw.Draw(image)
    draw.ellipse((36,36,156,156), outline=color, width=4)
    draw.line((96,65,96,104), fill=color, width=5)
    draw.ellipse((93,119,99,125),fill=color)
    image.save(output / (theme + '-error.webp'), lossless=True)
