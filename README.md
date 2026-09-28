<p align="center">
  <img src="data/icons/hicolor/512x512/apps/io.github.drvonmiau.Dice.png" width="120" alt="Dice icon">
</p>

<h1 align="center">Dice</h1>

<p align="center">
  A calm library for your emulator games on GNOME —<br>
  point it at your ROM folder, pick a game, press Play.
</p>

<p align="center">
  <img src="data/screenshots/library.png" width="820" alt="Dice showing a library of GBA, DS, PS1 and PS2 games with a game open in the side panel">
</p>

Dice keeps the ROMs you already own in one tidy shelf. Add a folder and it
finds every game inside — sub-folders included — works out which system each
one is for, reads what it can from the file itself, and launches it in the
right emulator. It only ever *reads* your folders.

<p align="center">
  <img src="data/screenshots/dark.png" width="820" alt="Favourites in the dark theme">
</p>

> [!IMPORTANT]
> Dice doesn't include, download or link to any games. It organises and
> launches backups of games you own.

## Features

**Your library**
- **A tab per platform** — All, then GBA, NDS, 3DS, PS1, PSP, PS2 (only the ones you have), then Favourites and Recent
- **Multi-disc games are one card**: an `.m3u` playlist, or files named "(Disc 1)", "(Disc 2)"… — start any disc from the side panel
- **Rename or move files freely**: a game keeps its favourite, play history and cover
- **Find Duplicates** lists games you have more than once — including different
  spellings like "Kirby & The Amazing Mirror" and "Kirby And The Amazing Mirror" —
  and tells identical copies from different releases
- **Gamepad navigation** — D-pad to browse, A to play, B to go back, Y to favourite, LB/RB to switch tabs
- **Knows what's inside**: GBA and DS cartridge headers and 3DS product
  codes (game code, region), PSP
  discs (`PARAM.SFO` title and serial) and PS2 discs (`SYSTEM.CNF` serial) —
  including **CSO**, **CHD**, **BIN/CUE** and **zipped GBA/DS** files. PS1, PSP and PS2 discs
  are told apart by their contents, not their folder
- **Search** by title, serial, region or file name; **sort** by title,
  platform, recently played, recently added or size

**Box art**
- Images named like the ROM, next to it or in a `covers/`, `boxart/`,
  `media/covers/` or `Named_Boxarts/` folder
- The icon **inside PSP discs**
- **Downloaded** from the libretro thumbnail archive for anything else —
  matched by the game's serial or checksum, then by file name, then by title,
  so oddly named files still find their box. **Find Missing Covers** in the
  menu retries everything still without art (automatic downloads can be
  turned off)
- Or pick any image yourself — a hand-picked cover always wins

**Playing**
- **Finds mGBA, melonDS/DeSmuME, Azahar/Lime3DS, DuckStation, PPSSPP and PCSX2** automatically (Flatpak or native), or
  uses any command you set per platform in Preferences
- Tracks **last played** and **play time**
- Double-click a cover or press <kbd>Enter</kbd> to play
- If an emulator installed as a Flatpak can't see your ROM folder, Dice offers
  to grant it access (or to share just that one game)
- Wrong platform guess? Pick the right one from the side panel's menu

## Tidy file names

`tools/fix-names.py` renames ROMs to their official No-Intro / Redump names —
cartridges (GBA, DS) by checksum, discs (PS1, PS2, PSP; ISO, CSO, CHD,
BIN/CUE) by the serial Dice reads from the disc. It previews by default,
moves saves and covers along, and writes an undo script. Dice keeps a renamed
game's favourites and history.

```sh
python3 tools/fix-names.py ~/Games/Roms            # preview
python3 tools/fix-names.py ~/Games/Roms --apply    # rename
```

## Adding a platform

Platforms live in one registry, [`src/platforms.py`](src/platforms.py): its
extensions, folder-name hints, libretro name and emulators. Tabs are built
from it, so a platform whose files can be recognised by extension (SNES, N64,
Game Boy…) is a single entry — NDS and 3DS were added exactly that way. Disc systems that share `.iso`/`.chd` with
others (PS1 was one; GameCube…) also want a content check in
[`src/romscan.py`](src/romscan.py).

## Install

**[Install Dice →](https://drvonmiau.github.io/dice/)** — one click in GNOME
Software, or from a terminal:

```sh
flatpak remote-add --user --if-not-exists dice https://drvonmiau.github.io/dice/index.flatpakrepo
flatpak install --user dice io.github.drvonmiau.Dice
```

Updates then arrive through GNOME Software or `flatpak update` like any other
app. You only need [Flatpak](https://flatpak.org/setup/), which most Linux
distributions already have; the first install may offer to pull in the GNOME
runtime — say yes. A single-file `.flatpak` bundle is also attached to every
[release](https://github.com/DrVonMiau/dice/releases).

Dice launches emulators installed on your computer, so install the ones you
need too, e.g. from Flathub: `io.mgba.mGBA`, `net.kuribo64.melonDS`,
`org.azahar_emu.Azahar`, `org.ppsspp.PPSSPP`, `net.pcsx2.PCSX2`.

## Building from source

Open the project in **GNOME Builder** and press Run, or:

```sh
flatpak-builder --user --install --force-clean _flatpak io.github.drvonmiau.Dice.json
flatpak run io.github.drvonmiau.Dice
```

Tests (no GTK needed):

```sh
python3 -m unittest discover -s tests
```

## Part of a family

Dice is one of four sibling apps that share a design language — the same
calm, offline-first idea recast for different libraries:

- 🎵 [**Lyre**](https://github.com/DrVonMiau/lyre) — your music
- 🖼️ [**Easel**](https://github.com/DrVonMiau/easel) — your photos
- 📖 [**Quill**](https://github.com/DrVonMiau/quill) — your reading
- 🎲 **Dice** — your games *(you are here)*

## Built with

GTK4 · libadwaita · PyGObject, packaged as a Flatpak on the GNOME runtime.

## License

Dice is free software, released under the [GNU GPL 3.0 or later](COPYING).
