---
name: easter-egg-chrome-extension
description: Build a Chrome extension that hides colored Easter eggs on every webpage. Eggs spawn at random intervals, can be clicked to collect, and each collection triggers a random page distortion effect that stacks permanently until refresh. Includes rare rabbit easter egg mechanic and persistent egg counter.
tags: [chrome-extension, easter, fun, javascript, css]
triggers:
  - easter egg chrome extension
  - easter egg hunt browser
  - page distortion extension
  - fun chrome plugin
---

# Easter Egg Hunt Chrome Extension

## Overview
A Chrome Manifest V3 extension that:
- Hides **colored eggs** (🥚 with CSS hue-rotate filters) at random positions on every page
- Eggs appear/disappear at random intervals (3-30s cycles)
- Clicking an egg: confetti burst + **random page distortion effect that STACKS and persists till refresh**
- **Rare rabbit** (~6% spawn rate): needs 5 clicks to catch (hops away each click), rewards +3 eggs and opens a special URL
- Persistent egg counter via `chrome.storage.local`
- Paw print cursor trail
- Popup UI showing count, manual spawn button, reset

## File Structure
```
easter-egg-extension/
├── manifest.json          # Manifest V3, permissions: activeTab, scripting, storage
├── content.js             # Main egg hunt engine (content script)
├── easter.css             # Animations, HUD, confetti, trail styles
├── background.js          # Minimal service worker
├── popup.html             # Extension popup UI
├── popup.js               # Popup logic (count display, spawn/reset buttons)
└── icons/
    ├── egg48.png          # Generated via PIL
    └── egg128.png
```

## Build Steps

1. **Create directory**: `mkdir -p ~/easter-egg-extension/icons`

2. **Write all source files** from templates:
   - `templates/manifest.json`
   - `templates/content.js`
   - `templates/easter.css`
   - `templates/background.js`
   - `templates/popup.html`
   - `templates/popup.js`

3. **Generate icons** with Python PIL:
```python
from PIL import Image, ImageDraw
def make_egg_icon(size, path):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = size // 8
    cx, cy = size // 2, size // 2 + pad // 3
    rx = size // 2 - pad
    ry = int(rx * 1.25)
    top = cy - ry
    draw.ellipse([pad, top, size - pad, cy + ry], fill=(255, 235, 200, 255))
    stripe_y = cy - ry // 6
    stripe_h = size // 10
    for x in range(pad + 4, size - pad - 4):
        dx = (x - cx) / rx
        if abs(dx) < 0.92:
            band_color = [(168, 130, 234, 220), (236, 72, 153, 200), (102, 126, 234, 220)]
            idx = (x // (size // 6)) % 3
            draw.rectangle([x, stripe_y, x + 1, stripe_y + stripe_h], fill=band_color[idx])
    for sx, sy in [(cx - rx//3, cy - ry//3), (cx + rx//4, cy - ry//2)]:
        r = max(1, size // 30)
        draw.ellipse([sx-r, sy-r, sx+r, sy+r], fill=(255, 255, 255, 200))
    img.save(path)

make_egg_icon(48, 'icons/egg48.png')
make_egg_icon(128, 'icons/egg128.png')
```

4. **Install in Chrome**:
   - Navigate to `chrome://extensions`
   - Enable "Developer mode" (top right toggle)
   - Click "Load unpacked" → select the extension directory
   - Refresh open tabs with Cmd+R / Ctrl+R

## Key Design Decisions

### Egg Coloring
Uses CSS `hue-rotate()` + `saturate()` + `brightness()` filters on the 🥚 emoji. 9 color presets: purple, blue, green, pink, red, yellow, sky, teal, orange.

### Stacking Effects
Each effect injects a NEW `<style>` tag with a unique ID (`easter-fx-N`). Animation keyframes also get unique names (`easter-shake-N`). This means effects never clobber each other — they pile up into cumulative chaos. Only a page refresh clears them.

### 20 Page Effects
Flip, earthquake, invert, shrink, tilt, blur, rainbow, Comic Sans, spin, bounce, film noir, stretch, mirror, wobble, max saturation, pixelate, drift, sepia, zoom, trippy.

### Rabbit Mechanic
- ~6% spawn chance (weight 3 vs egg weight 50)
- Needs 5 clicks; each click makes it hop to a random position
- Gets double the normal visible time before auto-vanishing
- Catching it: +3 eggs, confetti, opens configurable URL (default: Hermes Agent GitHub)

## Reload After Changes
`chrome://extensions` → click 🔄 on the extension card → Cmd+R on open tabs

## Pitfalls
- Content scripts don't auto-reload — must reload extension AND refresh tabs
- `chrome.storage.local` needs "storage" permission in manifest
- CSS filters on `html` element can conflict when multiple effects target it — using `!important` and injected stylesheets handles this
- Emoji rendering varies by OS — colored egg filters look best on macOS/Windows, may look different on Linux
