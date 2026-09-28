#!/usr/bin/env python3
"""Rename ROMs to their official No-Intro / Redump names.

    python3 tools/fix-names.py ~/Games/Roms            # preview only
    python3 tools/fix-names.py ~/Games/Roms --apply    # rename

The new name comes from what each file *is*, not from its current name:

  * Cartridges (GBA, DS — plain or zipped) by checksum: the file's CRC32 is
    looked up in No-Intro's database. A zip is renamed as a whole; what's
    inside it is never touched.
  * Discs (PS1, PS2, PSP — ISO, CSO, CHD, BIN/CUE) by serial: Dice's own
    reader gets the serial from the disc (e.g. SLES-52584) and Redump's
    database names it. Where one serial covers several releases, the disc's
    size decides, or the file is skipped. For BIN/CUE the tracks are renamed
    too and the .cue is rewritten to match.

Files that don't match — hacks, translations, bad dumps — are reported and left
alone. Saves, states and cover images named like a ROM move with it. --apply
writes fix-names-undo-<time>.sh into the folder, which reverses everything.
The databases are the DAT files the libretro project mirrors on GitHub, cached
in ~/.cache/dice/dats (see src/datfiles.py). Only standard-library Python is needed.
"""
import argparse
import os
import re
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import chd, datfiles, romscan  # noqa: E402

DISC_EXT = {".iso", ".cso", ".chd", ".cue"}
DISCS = {k: v[1] for k, v in datfiles.SYSTEMS.items() if v[0] == "redump"}
COMPANION_EXT = {".sav", ".srm", ".cheats", ".png", ".jpg", ".jpeg", ".webp",
                 ".mcr", ".mcd"} | {f".ss{i}" for i in range(10)}
COVER_DIRS = ("covers", "Covers", "boxart", "Boxart", "images", "media/covers",
              "media/box2dfront", "Named_Boxarts")


def cartridge(path):
    """(system, crc, size) for a GBA/DS ROM or a zip holding one, else None."""
    found = datfiles.cartridge_crc(path)
    if found is None:
        return None
    platform, crc, size = found
    return datfiles.SYSTEMS[platform][1], crc, size


def disc_size(path):
    """The uncompressed image size, to tell apart releases sharing a serial."""
    ext = path.suffix.lower()
    try:
        if ext == ".chd":
            image = chd.Chd(str(path))
            image.close()
            return image.logical
        if ext == ".cso":
            source = romscan.CsoSource(str(path))
            source.close()
            return source.total
        if ext == ".cue":
            files, _mode = romscan.cue_tracks(str(path))
            return sum(os.path.getsize(f) for f in files)
        return path.stat().st_size
    except (OSError, ValueError, chd.ChdError):
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


# ------------------------------------------------------------------ plan --

class Plan:
    def __init__(self, root):
        self.root = root
        self.ops = []          # ("mv", src, dst) or ("cue", old, new, old_text, new_text)
        self.unknown = []      # (path, why)
        self.skipped = []      # (path, why)
        self.claimed = set()

    def free(self, target):
        return not target.exists() and target not in self.claimed

    def move(self, src, dst):
        self.claimed.add(dst)
        self.ops.append(("mv", src, dst))

    def rename_with_companions(self, path, new_name):
        target = path.with_name(new_name)
        if target == path:
            return
        if not self.free(target):
            self.skipped.append((path, f"“{new_name}” already exists (duplicate?)"))
            return
        self.move(path, target)
        for extra in companions(path):
            dest = extra.with_name(target.stem + extra.suffix)
            if self.free(dest):
                self.move(extra, dest)


def _cue_plan(plan, cue, game):
    """Rename a .cue, its tracks, and rewrite its FILE lines to match."""
    text = cue.read_text(encoding="utf-8", errors="replace")
    files, _mode = romscan.cue_tracks(str(cue))
    tracks = [Path(f) for f in files]
    if not tracks or any(not t.exists() or t.parent != cue.parent for t in tracks):
        plan.skipped.append((cue, "its tracks are missing or in another folder"))
        return
    if len(tracks) == 1:
        names = [f"{game}.bin"]
    else:
        width = 2 if len(tracks) >= 10 else 1
        names = [f"{game} (Track {i:0{width}d}).bin" for i in range(1, len(tracks) + 1)]
    new_cue = cue.with_name(f"{game}.cue")
    targets = [t.with_name(n) for t, n in zip(tracks, names)]
    if new_cue == cue and targets == tracks:
        return
    for old, new in zip(tracks, targets):
        if new != old and not plan.free(new):
            plan.skipped.append((cue, f"“{new.name}” already exists"))
            return
    if new_cue != cue and not plan.free(new_cue):
        plan.skipped.append((cue, f"“{new_cue.name}” already exists"))
        return
    new_text = text
    for old, new in zip(tracks, targets):
        new_text = re.sub(r'(FILE\s+)"?' + re.escape(old.name) + r'"?',
                          lambda m, n=new.name: f'{m.group(1)}"{n}"', new_text)
    for old, new in zip(tracks, targets):
        if new != old:
            plan.move(old, new)
    plan.claimed.add(new_cue)
    plan.ops.append(("cue", cue, new_cue, text, new_text))
    for extra in companions(cue):
        dest = extra.with_name(new_cue.stem + extra.suffix)
        if plan.free(dest):
            plan.move(extra, dest)


def build_plan(root, dat_dir=None):
    root = Path(root)
    plan = Plan(root)
    dbs = datfiles.Databases(dat_dir)
    paths = sorted(p for p in root.rglob("*") if p.is_file()
                   and not any(part.startswith(".") for part in p.relative_to(root).parts))
    for path in paths:
        ext = path.suffix.lower()
        cart = cartridge(path)
        if cart is not None:
            system, crc, size = cart
            match = dbs.by_crc(system).get(crc)
            if match is None or match[1] != size:
                plan.unknown.append((path, f"CRC {crc} isn't a known clean dump"))
            else:
                plan.rename_with_companions(path, safe_name(match[0]) + ext)
            continue
        if ext not in DISC_EXT:
            continue
        parts = (root.name,) + path.relative_to(root).parent.parts
        info = romscan.identify(str(path), parts)
        if info is None or info.platform not in DISCS:
            continue
        if not info.serial:
            plan.unknown.append((path, "no serial could be read from the disc"))
            continue
        candidates = dbs.by_serial(DISCS[info.platform]).get(info.serial.upper(), [])
        names = {c[0] for c in candidates}
        if len(names) > 1:
            size = disc_size(path)
            sized = {c[0] for c in candidates if size and c[1] == size}
            names = sized if len(sized) == 1 else names
        if not names:
            plan.unknown.append((path, f"serial {info.serial} isn't in Redump"))
        elif len(names) > 1:
            plan.skipped.append((path, f"serial {info.serial} matches several releases: "
                                       + "; ".join(sorted(names))))
        elif ext == ".cue":
            _cue_plan(plan, path, safe_name(names.pop()))
        else:
            plan.rename_with_companions(path, safe_name(names.pop()) + ext)
    return plan


# ------------------------------------------------------------------ apply --

def apply(plan):
    undo = plan.root / time.strftime("fix-names-undo-%Y%m%d-%H%M%S.sh")
    lines = []
    for op in plan.ops:
        if op[0] == "mv":
            _, src, dst = op
            os.rename(src, dst)
            lines.append(f"mv -n -- {shlex.quote(str(dst))} {shlex.quote(str(src))}\n")
        else:
            _, old, new, old_text, new_text = op
            new.write_text(new_text, encoding="utf-8")
            if new != old:
                old.unlink()
            lines.append(f"cat > {shlex.quote(str(old))} <<'DICE_CUE_EOF'\n"
                         f"{old_text.rstrip(chr(10))}\nDICE_CUE_EOF\n")
            if new != old:
                lines.append(f"rm -f -- {shlex.quote(str(new))}\n")
    with open(undo, "w", encoding="utf-8") as log:
        log.write("#!/bin/sh\n# Reverses one fix-names.py --apply run (run from anywhere).\n")
        # Newest first, so every step undoes on top of the state it left.
        log.writelines(reversed(lines))
    return undo


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder")
    parser.add_argument("--apply", action="store_true", help="rename (default: preview)")
    parser.add_argument("--dat-dir", help="use DAT files from this folder instead of downloading")
    args = parser.parse_args()
    root = Path(args.folder).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"Not a folder: {root}")

    plan = build_plan(root, args.dat_dir)
    renames = 0
    for op in plan.ops:
        src, dst = op[1], op[2]
        if src != dst:
            renames += 1
            print(f"  {src.relative_to(root)}\n→ {dst.relative_to(root)}\n")
    for path, why in plan.skipped:
        print(f"SKIP  {path.relative_to(root)}: {why}")
    for path, why in plan.unknown:
        print(f"??    {path.relative_to(root)}: {why} — left as is")
    print(f"\n{renames} rename(s), {len(plan.skipped)} skipped, "
          f"{len(plan.unknown)} unrecognised.")
    if not plan.ops:
        return
    if not args.apply:
        print("Preview only — run again with --apply to rename.")
        return
    undo = apply(plan)
    print(f"Done. To undo: sh {shlex.quote(str(undo))}")
    print("Then press Ctrl+R in Dice: renamed games keep their favourites and history.")


if __name__ == "__main__":
    main()
