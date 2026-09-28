"""No-Intro and Redump game databases (the DAT files libretro mirrors on
GitHub), for official names.

They name a game from what it *is*: a cartridge by its CRC32, a disc by its
serial. Dice uses them to find box art (thumbnails are named after these
names) and tools/fix-names.py to rename files. Downloaded once and cached in
~/.cache/dice/dats (inside the Flatpak, Dice's own cache).
"""
import os
import re
import sys
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

DAT_URL = "https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/{}/{}.dat"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "dice" / "dats"

# Dice platform key -> (database, system name). Box-art systems share the name.
SYSTEMS = {
    "gba": ("no-intro", "Nintendo - Game Boy Advance"),
    "nds": ("no-intro", "Nintendo - Nintendo DS"),
    "3ds": ("no-intro", "Nintendo - Nintendo 3DS"),
    "ps1": ("redump", "Sony - PlayStation"),
    "ps2": ("redump", "Sony - PlayStation 2"),
    "psp": ("redump", "Sony - PlayStation Portable"),
}
CARTRIDGE_EXT = {".gba": "gba", ".nds": "nds"}

_GAME_RE = re.compile(r'^game \(\s*\n(.*?)^\)', re.M | re.S)
_NAME_RE = re.compile(r'^\s*name "((?:[^"\\]|\\.)*)"', re.M)
_SERIAL_RE = re.compile(r'^\s*serial "([^"]*)"', re.M)
_ROM_RE = re.compile(r'rom \( name "((?:[^"\\]|\\.)*)" size (\d+) crc ([0-9A-Fa-f]{8})')


def load_dat(system, source, dat_dir=None, quiet=False):
    """[(game name, serials, [(rom name, size, crc)])] from a DAT file."""
    if dat_dir:
        path = Path(dat_dir) / f"{system}.dat"
    else:
        CACHE.mkdir(parents=True, exist_ok=True)
        path = CACHE / f"{source}-{system}.dat"
        if not path.exists():
            if not quiet:
                print(f"Downloading the {system} database…", file=sys.stderr)
            url = DAT_URL.format(source, urllib.parse.quote(system))
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read()
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            os.replace(tmp, path)
    text = path.read_text(encoding="utf-8", errors="replace")
    games = []
    for block in _GAME_RE.finditer(text):
        body = block.group(1)
        name = _NAME_RE.search(body)
        if not name:
            continue
        serials = set()
        for value in _SERIAL_RE.findall(body):
            serials.update(s.upper() for s in re.split(r"[,\s]+", value) if s)
        roms = [(r[0], int(r[1]), r[2].upper()) for r in _ROM_RE.findall(body)]
        games.append((name.group(1), serials, roms))
    return games


class Databases:
    """Lazily loaded lookups, one per system. Failures (offline, missing DAT)
    read as empty tables."""

    def __init__(self, dat_dir=None, quiet=False):
        self.dat_dir = dat_dir
        self.quiet = quiet
        self._games = {}
        self._crc = {}
        self._serial = {}

    def games(self, system, source):
        if system not in self._games:
            try:
                self._games[system] = load_dat(system, source, self.dat_dir, self.quiet)
            except (OSError, ValueError):
                if self.dat_dir is not None:
                    raise
                self._games[system] = []
        return self._games[system]

    def by_crc(self, system):
        if system not in self._crc:
            table = {}
            for name, _serials, roms in self.games(system, "no-intro"):
                for _rom, size, crc in roms:
                    table.setdefault(crc, (name, size))
            self._crc[system] = table
        return self._crc[system]

    def by_serial(self, system):
        if system not in self._serial:
            table = {}
            for name, serials, roms in self.games(system, "redump"):
                entry = (name, sum(r[1] for r in roms))
                for serial in serials:
                    if entry not in table.setdefault(serial, []):
                        table[serial].append(entry)
            self._serial[system] = table
        return self._serial[system]

    def names(self, platform):
        """Every official game name for a Dice platform."""
        source, system = SYSTEMS[platform]
        return [g[0] for g in self.games(system, source)]


def file_crc(path):
    crc = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            crc = zlib.crc32(chunk, crc)
    return f"{crc & 0xFFFFFFFF:08X}"


def cartridge_crc(path):
    """(platform, crc, size) for a GBA/DS ROM or a zip holding one, else None.
    Zips report the CRC their directory already stores: nothing is unpacked."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext in CARTRIDGE_EXT:
        return CARTRIDGE_EXT[ext], file_crc(path), path.stat().st_size
    if ext == ".zip":
        try:
            with zipfile.ZipFile(path) as zf:
                roms = [i for i in zf.infolist()
                        if Path(i.filename).suffix.lower() in CARTRIDGE_EXT]
        except (zipfile.BadZipFile, OSError):
            return None
        if len(roms) == 1:
            info = roms[0]
            return (CARTRIDGE_EXT[Path(info.filename).suffix.lower()],
                    f"{info.CRC:08X}", info.file_size)
    return None


def official_name(dbs, path, platform, serial, size=None):
    """The No-Intro / Redump name for a game, or None when it can't be told
    for sure (unknown dump, or a serial shared by several releases)."""
    if platform in ("gba", "nds"):
        try:
            cart = cartridge_crc(path)
        except OSError:
            return None
        if cart is None:
            return None
        match = dbs.by_crc(SYSTEMS[cart[0]][1]).get(cart[1])
        return match[0] if match and match[1] == cart[2] else None
    if platform in ("ps1", "ps2", "psp") and serial:
        candidates = dbs.by_serial(SYSTEMS[platform][1]).get(serial.upper(), [])
        names = {c[0] for c in candidates}
        if len(names) > 1 and size:
            sized = {c[0] for c in candidates if c[1] == size}
            names = sized if len(sized) == 1 else names
        return names.pop() if len(names) == 1 else None
    return None
