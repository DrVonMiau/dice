#!/usr/bin/env python3
"""Rename GBA and DS ROMs to their official No-Intro names, by checksum.

Each file's CRC32 is looked up in the No-Intro database (the DAT files the
libretro project mirrors on GitHub), so the new name comes from what the file
*is*, not from its current name. Anything that doesn't match — a bad dump, a
hack, a translation — is reported and left alone.

    python3 fix-names.py ~/Downloads/roms            # preview only
    python3 fix-names.py ~/Downloads/roms --apply    # rename

Plain .gba/.nds files and .zip archives holding one ROM are handled. A zip is
renamed as a whole; what's inside it is never touched. Save files and states
that share the ROM's name (Game.sav, Game.ss1 …) and cover images next to it
or in a covers/ folder are renamed with it, so nothing gets orphaned.
--apply also writes a fix-names-undo-<time>.sh in the folder to reverse every rename.
Only standard-library Python is needed.
"""
import argparse
import os
import re
import shlex
import sys
import time
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

DAT_URL = ("https://raw.githubusercontent.com/libretro/libretro-database/"
           "master/metadat/no-intro/{}.dat")
SYSTEMS = {".gba": "Nintendo - Game Boy Advance", ".nds": "Nintendo - Nintendo DS"}
COMPANION_EXT = {".sav", ".srm", ".cheats", ".png", ".jpg", ".jpeg", ".webp"} | {
    f".ss{i}" for i in range(10)}
COVER_DIRS = ("covers", "Covers", "boxart", "Boxart", "images", "media/covers",
              "media/box2dfront", "Named_Boxarts")
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "dice" / "dats"

_GAME_RE = re.compile(r'^game \(\s*\n\s*name "((?:[^"\\]|\\.)*)"(.*?)^\)', re.M | re.S)
_ROM_RE = re.compile(r'rom \( name "((?:[^"\\]|\\.)*)" size (\d+) crc ([0-9A-Fa-f]{8})')


def load_dat(system, dat_dir=None):
    """{crc: (game name, rom file name, size)} for one system."""
    if dat_dir:
        path = Path(dat_dir) / f"{system}.dat"
    else:
        CACHE.mkdir(parents=True, exist_ok=True)
        path = CACHE / f"{system}.dat"
        if not path.exists():
            url = DAT_URL.format(urllib.parse.quote(system))
            print(f"Downloading the {system} database…", file=sys.stderr)
            with urllib.request.urlopen(url, timeout=60) as response:
                path.write_bytes(response.read())
    text = path.read_text(encoding="utf-8", errors="replace")
    table = {}
    for game in _GAME_RE.finditer(text):
        for rom in _ROM_RE.finditer(game.group(2)):
            table.setdefault(rom.group(3).upper(),
                             (game.group(1), rom.group(1), int(rom.group(2))))
    return table


def file_crc(path):
    crc = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            crc = zlib.crc32(chunk, crc)
    return f"{crc & 0xFFFFFFFF:08X}"


def identify(path):
    """(extension of the ROM, crc, size) or None. Zips report the CRC their
    directory already stores, so nothing is decompressed."""
    ext = path.suffix.lower()
    if ext in SYSTEMS:
        return ext, file_crc(path), path.stat().st_size
    if ext == ".zip":
        try:
            with zipfile.ZipFile(path) as zf:
                roms = [i for i in zf.infolist() if Path(i.filename).suffix.lower() in SYSTEMS]
        except (zipfile.BadZipFile, OSError):
            return None
        if len(roms) != 1:
            return None
        info = roms[0]
        return Path(info.filename).suffix.lower(), f"{info.CRC:08X}", info.file_size
    return None


def safe_name(name):
    return name.replace("/", "_").replace("\\", "_")


def companions(path):
    """Files named after the ROM that should follow it: saves, states, covers."""
    out = []
    for folder in [path.parent] + [path.parent / d for d in COVER_DIRS]:
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            if (entry.is_file() and entry != path and entry.stem == path.stem
                    and entry.suffix.lower() in COMPANION_EXT):
                out.append(entry)
    return out


def plan(root, dat_dir=None):
    tables = {}
    moves, unknown, skipped = [], [], []
    claimed = set()
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file()):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        found = identify(path)
        if found is None:
            continue
        rom_ext, crc, size = found
        system = SYSTEMS[rom_ext]
        if system not in tables:
            tables[system] = load_dat(system, dat_dir)
        match = tables[system].get(crc)
        if match is None or match[2] != size:
            unknown.append((path, crc))
            continue
        game, _rom_name, _size = match
        # The game name (not the ROM's file name, which occasionally differs)
        # is also what online box-art lookups match on.
        new_name = safe_name(game) + path.suffix.lower()
        target = path.with_name(new_name)
        if target == path:
            continue
        if target.exists() or target in claimed:
            skipped.append((path, f"{new_name} already exists (duplicate?)"))
            continue
        claimed.add(target)
        moves.append((path, target))
        new_stem = target.stem
        for extra in companions(path):
            dest = extra.with_name(new_stem + extra.suffix)
            if not dest.exists() and dest not in claimed:
                claimed.add(dest)
                moves.append((extra, dest))
    return moves, unknown, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder")
    parser.add_argument("--apply", action="store_true", help="rename (default: preview)")
    parser.add_argument("--dat-dir", help="use DAT files from this folder instead of downloading")
    args = parser.parse_args()
    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"Not a folder: {root}")

    moves, unknown, skipped = plan(root, args.dat_dir)
    for old, new in moves:
        print(f"  {old.relative_to(root)}\n→ {new.relative_to(root)}\n")
    for path, reason in skipped:
        print(f"SKIP  {path.relative_to(root)}: {reason}")
    for path, crc in unknown:
        print(f"??    {path.relative_to(root)}  (CRC {crc} not in No-Intro — hack, "
              "translation or bad dump; left as is)")
    print(f"\n{len(moves)} rename(s), {len(skipped)} skipped, {len(unknown)} unrecognised.")

    if not moves:
        return
    if not args.apply:
        print("Preview only — run again with --apply to rename.")
        return
    undo = root / time.strftime("fix-names-undo-%Y%m%d-%H%M%S.sh")
    with open(undo, "w", encoding="utf-8") as log:
        log.write("#!/bin/sh\n# Reverses one fix-names.py --apply run (run from anywhere).\n")
        for old, new in moves:
            os.rename(old, new)
            log.write(f"mv -n -- {shlex.quote(str(new))} {shlex.quote(str(old))}\n")
            log.flush()
    print(f"Done. To undo: sh {shlex.quote(str(undo))}")
    print("Then press Ctrl+R in Dice so the library picks up the new names.")


if __name__ == "__main__":
    main()
