#!/usr/bin/env python3
"""Generate Easter egg icons for the Chrome extension."""
import sys
from PIL import Image, ImageDraw

def make_egg_icon(size, path):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = size // 8
    cx, cy = size // 2, size // 2 + pad // 3
    rx = size // 2 - pad
    ry = int(rx * 1.25)
    top = cy - ry
    # Egg body
    draw.ellipse([pad, top, size - pad, cy + ry], fill=(255, 235, 200, 255))
    # Decorative stripe
    stripe_y = cy - ry // 6
    stripe_h = size // 10
    for x in range(pad + 4, size - pad - 4):
        dx = (x - cx) / rx
        if abs(dx) < 0.92:
            band_color = [(168, 130, 234, 220), (236, 72, 153, 200), (102, 126, 234, 220)]
            idx = (x // (size // 6)) % 3
            draw.rectangle([x, stripe_y, x + 1, stripe_y + stripe_h], fill=band_color[idx])
    # Sparkle dots
    for sx, sy in [(cx - rx//3, cy - ry//3), (cx + rx//4, cy - ry//2)]:
        r = max(1, size // 30)
        draw.ellipse([sx-r, sy-r, sx+r, sy+r], fill=(255, 255, 255, 200))
    img.save(path)
    print(f'Saved {path} ({size}x{size})')

if __name__ == "__main__":
    outdir = sys.argv[1] if len(sys.argv) > 1 else "icons"
    make_egg_icon(48, f'{outdir}/egg48.png')
    make_egg_icon(128, f'{outdir}/egg128.png')
