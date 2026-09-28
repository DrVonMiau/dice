# Dice — design notes

Dice uses the design system documented in
[Easel's DESIGN.md](https://github.com/DrVonMiau/easel/blob/main/docs/DESIGN.md)
— same spacing, radius, type and elevation scales, same component classes
(`.tab-group`, `.tab-btn`, `.paper`, `.info-key`/`.info-value`, `.tile-fav`,
`.thumb-scale`, `.empty-cta`). Only what differs is listed here.

## Accent

| Token | Light | Dark | Used for |
|---|---|---|---|
| `@dice_coral` | `#c74e40` | — | Interactive: Play, slider, selection ring, info keys, focus |
| `@dice_coral_light` | — | `#f98375` | The same on dark surfaces (with dark text `#2b1310` on filled buttons) |
| `@dice_gold` / `_light` | `#c1962b` | `#ddb964` | Favourites (shared with Lyre and Easel) |
| `@dice_text_soft` / `_dark` | `#9f6660` | `#dfb0aa` | Secondary / mono-dim text |

Why coral: it's the tile colour of Dice's icon (`#f98375`), and it stays well
apart from the siblings' lavender (Lyre) and blue (Easel) and from the gold
of favourites. The icon's coral is too light to carry white text (2.5:1), so
the light theme uses a deeper shade of the same hue: white on `#c74e40` is
4.6:1, as is `#c74e40` text on the white paper. On dark surfaces the icon's own
coral reads at 5.5:1 or better, and dark text on it at 7:1.

## Components added for Dice

| Component | CSS class | Notes |
|---|---|---|
| Box-art cover | `.cover` (`DiceCover`) | 3:4 portrait, radius 6. Art far from box-shaped (PSP disc icons, square GBA scans) is shown whole over a blurred copy of itself. With no art: stripes + the game title |
| Platform chip | `.platform-badge` | Mono 10px caps on a dark scrim, top-left of every cover |
| Play button | `.play-btn` | The primary button, full-width beside two 48px secondary actions (favourite, more) |
| Panel title | `.info-title` | Sans 19 / 600 |
| Clickable value | `.info-link` | Path and Emulator rows; accent + underline on hover |
