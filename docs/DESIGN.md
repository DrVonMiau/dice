# Dice — design notes

Dice uses the design system documented in
[Easel's DESIGN.md](https://github.com/DrVonMiau/easel/blob/main/docs/DESIGN.md)
— same spacing, radius, type and elevation scales, same component classes
(`.tab-group`, `.tab-btn`, `.paper`, `.info-key`/`.info-value`, `.tile-fav`,
`.thumb-scale`, `.empty-cta`). Only what differs is listed here.

## Accent

| Token | Light | Dark | Used for |
|---|---|---|---|
| `@dice_green` | `#2b8563` | — | Interactive: Play, slider, selection ring, info keys, focus |
| `@dice_green_light` | — | `#5cc79a` | The same on dark surfaces (with dark text on filled buttons) |
| `@dice_gold` / `_light` | `#c1962b` | `#ddb964` | Favourites (shared with Lyre and Easel) |
| `@dice_text_soft` / `_dark` | `#587a6d` | `#9fcab8` | Secondary / mono-dim text |

Why emerald: the siblings own lavender (Lyre) and blue (Easel), and gold is
taken by favourites. Green reads as "go / play" — Play is the app's one
primary action — and nods to the phosphor screens of handhelds. White on
`#2b8563` clears 4.5:1.

## Components added for Dice

| Component | CSS class | Notes |
|---|---|---|
| Box-art cover | `.cover` (`DiceCover`) | 3:4 portrait, radius 6. Art far from box-shaped (PSP disc icons, square GBA scans) is shown whole over a blurred copy of itself. With no art: stripes + the game title |
| Platform chip | `.platform-badge` | Mono 10px caps on a dark scrim, top-left of every cover |
| Play button | `.play-btn` | The primary button, full-width beside two 48px secondary actions (favourite, more) |
| Panel title | `.info-title` | Sans 19 / 600 |
| Clickable value | `.info-link` | Path and Emulator rows; accent + underline on hover |
