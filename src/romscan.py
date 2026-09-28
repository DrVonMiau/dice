"""Work out what a ROM file is: its platform, a display title, and whatever
metadata can be read cheaply from the file itself.

Nothing here touches GTK, so it runs on the scanner thread and is unit-tested
on its own (tests/test_romscan.py).

  * GBA cartridges carry a header with a game code (region lives in its last
    letter). Zipped cartridges are read in place.
  * PSP and PS2 discs are both ".iso", so the ISO9660 file system is read to
    tell them apart: a PSP disc has PSP_GAME/PARAM.SFO (title, serial and an
    ICON0.PNG cover), a PS2 disc has SYSTEM.CNF with a BOOT2 line (serial).
    Compressed CSO images and BIN/CUE (2352-byte sectors) are read through
    the same code via small "source" adapters.
  * CHD can't be read without libchdr, so it falls back to the folder name
    (".../PS2/game.chd") and then to PS2.

The title shown in the app comes from the file name, cleaned of No-Intro /
Redump tags — users already curate those names, and a disc's own title field
is often upper-case or truncated.
"""
import os
import re
import struct
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from . import chd, platforms

SECTOR = 2048


@dataclass
class GameInfo:
    platform: str
    title: str
    serial: str = ""
    internal_title: str = ""
    region: str = ""
    format: str = ""
    size: int = 0
    icon: bytes = b""     # embedded cover art (PNG bytes), if the file has one


# ---------------------------------------------------------------- titles ----

_TAG_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_ARTICLE_RE = re.compile(r"^(.*?), (The|A|An)(\s+-\s+.*|:.*)?$")

_REGION_WORDS = {
    "usa": "USA", "us": "USA", "u": "USA", "america": "USA",
    "europe": "Europe", "eu": "Europe", "e": "Europe", "pal": "Europe",
    "japan": "Japan", "jp": "Japan", "j": "Japan", "ntsc-j": "Japan",
    "world": "World", "korea": "Korea", "asia": "Asia",
    "germany": "Germany", "france": "France", "spain": "Spain",
    "italy": "Italy", "uk": "UK", "australia": "Australia",
    "brazil": "Brazil", "china": "China",
}


def clean_title(stem):
    """'Legend of Zelda, The - The Minish Cap (USA) [!]' ->
    'The Legend of Zelda - The Minish Cap'."""
    title = _TAG_RE.sub("", stem).strip()
    if " " not in title:
        title = title.replace("_", " ").replace(".", " ")
    title = re.sub(r"\s+", " ", title).strip() or stem
    match = _ARTICLE_RE.match(title)
    if match:
        title = f"{match.group(2)} {match.group(1)}{match.group(3) or ''}"
    return title


_DISC_RE = re.compile(r"\((?:disc|disk|cd)\s*(\d+)(?:\s*of\s*\d+)?\)", re.I)


def disc_number(stem):
    """2 for 'Final Fantasy VII (USA) (Disc 2)', else None."""
    match = _DISC_RE.search(stem)
    return int(match.group(1)) if match else None


def m3u_members(path):
    """The disc images an .m3u playlist lists (absolute paths), in order."""
    base = os.path.dirname(path)
    members = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    members.append(os.path.normpath(os.path.join(base, line)))
    except OSError:
        pass
    return members


def region_from_name(stem):
    """The first region named in the file's tags, e.g. '(USA, Europe)'."""
    for group in re.findall(r"\(([^)]*)\)", stem):
        words = [w for w in re.split(r"[,/\s]+", group) if w]
        for word in words:
            # Single letters ("U", "E") only count as a whole GoodTools tag,
            # so "(Rev A)" or "(Disc 1)" never read as a region.
            if len(word) == 1 and len(words) > 1:
                continue
            region = _REGION_WORDS.get(word.lower())
            if region:
                return region
    return ""


_SERIAL_REGIONS = (
    (("SLUS", "SCUS", "ULUS", "UCUS", "NPUH", "NPUG", "PAPX"), "USA"),
    (("SLES", "SCES", "ULES", "UCES", "NPEH", "NPEG", "SCED"), "Europe"),
    (("SLPS", "SLPM", "SCPS", "SCPM", "SLAJ", "ULJS", "ULJM", "UCJS",
      "NPJH", "NPJG"), "Japan"),
    (("SLKA", "SCKA", "ULKS", "UCKS", "NPHH"), "Korea"),
    (("ULAS", "UCAS", "SCAJ"), "Asia"),
)

_NINTENDO_REGIONS = {"E": "USA", "P": "Europe", "J": "Japan", "D": "Germany",
                "F": "France", "S": "Spain", "I": "Italy", "K": "Korea",
                "C": "China", "X": "Europe", "Y": "Europe"}


def region_from_serial(serial):
    prefix = serial[:4].upper()
    for prefixes, region in _SERIAL_REGIONS:
        if prefix in prefixes:
            return region
    return ""


# --------------------------------------------------------------- sources ----

class FileSource:
    """Random access to a plain disc image."""

    def __init__(self, path):
        self._fh = open(path, "rb")

    def read(self, offset, size):
        self._fh.seek(offset)
        return self._fh.read(size)

    def close(self):
        self._fh.close()


class RawSectorSource(FileSource):
    """A BIN track with raw 2352-byte sectors: only 2048 bytes of each are
    user data, starting `data_offset` bytes in (16 for MODE1, 24 for MODE2)."""

    def __init__(self, path, raw_size=2352, data_offset=24):
        super().__init__(path)
        self._raw = raw_size
        self._off = data_offset

    def read(self, offset, size):
        out = bytearray()
        while size > 0:
            lba, within = divmod(offset, SECTOR)
            take = min(size, SECTOR - within)
            self._fh.seek(lba * self._raw + self._off + within)
            chunk = self._fh.read(take)
            if not chunk:
                break
            out += chunk
            offset += len(chunk)
            size -= len(chunk)
        return bytes(out)


class CsoSource(FileSource):
    """A CISO-compressed image (common for PSP): a block index followed by
    raw-deflate blocks, the top bit of an index entry meaning 'stored'."""

    def __init__(self, path):
        super().__init__(path)
        header = self._fh.read(24)
        if len(header) < 24 or header[:4] != b"CISO":
            self.close()
            raise ValueError("not a CSO file")
        _hsize, total, block, _ver, align = struct.unpack("<IQIBB", header[4:22])
        if block <= 0:
            self.close()
            raise ValueError("bad CSO block size")
        self.total = total
        self._block = block
        self._align = align
        count = (total + block - 1) // block + 1
        self._fh.seek(24)
        self._index = struct.unpack(f"<{count}I", self._fh.read(4 * count))

    def _read_block(self, n):
        entry, nxt = self._index[n], self._index[n + 1]
        plain = entry & 0x80000000
        start = (entry & 0x7FFFFFFF) << self._align
        end = (nxt & 0x7FFFFFFF) << self._align
        self._fh.seek(start)
        data = self._fh.read(end - start)
        if plain:
            return data[:self._block]
        return zlib.decompressobj(-15).decompress(data)[:self._block]

    def read(self, offset, size):
        out = bytearray()
        while size > 0 and offset < self.total:
            n, within = divmod(offset, self._block)
            if n + 1 >= len(self._index):
                break
            chunk = self._read_block(n)[within:within + size]
            if not chunk:
                break
            out += chunk
            offset += len(chunk)
            size -= len(chunk)
        return bytes(out)


# --------------------------------------------------------------- ISO9660 ----

class Iso9660:
    """Just enough ISO9660 to read a few small files by path."""

    def __init__(self, source):
        self.src = source
        pvd = source.read(16 * SECTOR, SECTOR)
        if len(pvd) < 190 or pvd[1:6] != b"CD001":
            raise ValueError("no ISO9660 volume descriptor")
        self.volume_id = pvd[40:72].decode("ascii", "replace").strip()
        self._root = self._record(pvd[156:190])

    @staticmethod
    def _record(raw):
        extent = struct.unpack("<I", raw[2:6])[0]
        size = struct.unpack("<I", raw[10:14])[0]
        flags = raw[25]
        name_len = raw[32]
        name = raw[33:33 + name_len].decode("ascii", "replace")
        return {"extent": extent, "size": size, "dir": bool(flags & 2),
                "name": name.split(";")[0].rstrip(".").upper()}

    def _list(self, rec):
        data = self.src.read(rec["extent"] * SECTOR, min(rec["size"], 256 * 1024))
        pos, entries = 0, []
        while pos < len(data):
            length = data[pos]
            if length == 0:
                # Records never span sectors; skip to the next one.
                pos = (pos // SECTOR + 1) * SECTOR
                continue
            raw = data[pos:pos + length]
            if len(raw) >= 34:
                entry = self._record(raw)
                if entry["name"] not in ("\x00", "\x01"):
                    entries.append(entry)
            pos += length
        return entries

    def find(self, path):
        rec = self._root
        for part in path.upper().split("/"):
            match = next((e for e in self._list(rec) if e["name"] == part), None)
            if match is None:
                return None
            rec = match
        return rec

    def read_file(self, path, limit=4 * 1024 * 1024):
        rec = self.find(path)
        if rec is None or rec["dir"]:
            return None
        return self.src.read(rec["extent"] * SECTOR, min(rec["size"], limit))


def parse_sfo(data):
    """PARAM.SFO key/value table -> dict (str and int values)."""
    if not data or len(data) < 20 or data[:4] != b"\x00PSF":
        return {}
    key_start, data_start, count = struct.unpack("<III", data[8:20])
    out = {}
    for i in range(count):
        base = 20 + i * 16
        if base + 16 > len(data):
            break
        key_off, fmt, length, _max, data_off = struct.unpack("<HHIII", data[base:base + 16])
        k0 = key_start + key_off
        key = data[k0:data.index(b"\x00", k0)].decode("ascii", "replace")
        raw = data[data_start + data_off:data_start + data_off + length]
        if fmt == 0x0404:
            out[key] = struct.unpack("<I", raw[:4])[0] if len(raw) >= 4 else 0
        else:
            out[key] = raw.split(b"\x00")[0].decode("utf-8", "replace").strip()
    return out


def _format_ps_serial(raw):
    """'SLUS_209.46;1' / 'ULUS10041' -> 'SLUS-20946' / 'ULUS-10041'."""
    s = re.sub(r"[^A-Z0-9]", "", raw.upper())
    if len(s) >= 9 and s[:4].isalpha():
        return f"{s[:4]}-{s[4:9]}"
    return raw.strip()


def ps2_serial_from_cnf(text):
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip().upper() == "BOOT2":
            name = value.strip().replace("\\", "/").split("/")[-1]
            return _format_ps_serial(name.split(";")[0])
    return None


def ps1_serial_from_cnf(text):
    """PS1 discs boot with 'BOOT = cdrom:\\SLUS_000.67;1' (PS2 uses BOOT2)."""
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip().upper() == "BOOT":
            name = value.strip().replace("\\", "/").split("/")[-1].split(":")[-1]
            return _format_ps_serial(name.split(";")[0])
    return None


def sniff_disc(source):
    """Identify a PSP, PS2 or PS1 ISO9660 image. Returns a dict or None."""
    try:
        iso = Iso9660(source)
    except (ValueError, struct.error, IndexError):
        return None
    sfo = iso.read_file("PSP_GAME/PARAM.SFO")
    if sfo:
        params = parse_sfo(sfo)
        return {
            "platform": "psp",
            "internal_title": params.get("TITLE", ""),
            "serial": _format_ps_serial(params.get("DISC_ID", "")) if params.get("DISC_ID") else "",
            "icon": iso.read_file("PSP_GAME/ICON0.PNG", limit=512 * 1024) or b"",
        }
    if iso.find("UMD_DATA.BIN"):
        umd = iso.read_file("UMD_DATA.BIN") or b""
        return {"platform": "psp",
                "serial": _format_ps_serial(umd.split(b"|")[0].decode("ascii", "replace"))}
    cnf = iso.read_file("SYSTEM.CNF", limit=4096)
    if cnf:
        serial = ps2_serial_from_cnf(cnf.decode("ascii", "replace"))
        if serial is not None:
            return {"platform": "ps2", "serial": serial,
                    "internal_title": iso.volume_id}
        serial = ps1_serial_from_cnf(cnf.decode("ascii", "replace"))
        if serial is not None:
            return {"platform": "ps1", "serial": serial,
                    "internal_title": iso.volume_id}
    if iso.find("PSX.EXE"):
        # Early PS1 discs have no SYSTEM.CNF and boot PSX.EXE directly.
        return {"platform": "ps1", "internal_title": iso.volume_id}
    return None


def chd_media(path):
    """'cd', 'dvd' or None, from a v5 CHD's metadata tags. CD images (PS1, and
    a few PS2 titles) carry CHT2/CHTR track tags; DVD images carry 'DVD '."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(0x40)
            if head[:8] != b"MComprHD" or struct.unpack(">I", head[12:16])[0] != 5:
                return None
            offset = struct.unpack(">Q", head[0x30:0x38])[0]
            for _ in range(64):
                if not offset:
                    break
                fh.seek(offset)
                entry = fh.read(16)
                if len(entry) < 16:
                    break
                tag = entry[:4]
                if tag in (b"CHT2", b"CHTR", b"CHCD", b"CHGD", b"CHGT"):
                    return "cd"
                if tag == b"DVD ":
                    return "dvd"
                offset = struct.unpack(">Q", entry[8:16])[0]
    except (OSError, struct.error):
        return None
    return None


# ------------------------------------------------------------ cartridges ----

def parse_gba_header(data):
    """Title and game code from a GBA cartridge header, or None."""
    if len(data) < 0xC0 or data[0xB2] != 0x96:
        return None
    title = data[0xA0:0xAC].split(b"\x00")[0].decode("ascii", "replace").strip()
    code = data[0xAC:0xB0].decode("ascii", "replace").strip("\x00 ")
    return {
        "platform": "gba",
        "internal_title": title,
        "serial": f"AGB-{code}" if code else "",
        "region": _NINTENDO_REGIONS.get(code[3:4], "") if len(code) == 4 else "",
    }


def parse_nds_header(data):
    """Title and game code from a DS cartridge header, or None."""
    if len(data) < 0x20:
        return None
    code = data[0x0C:0x10].decode("ascii", "replace")
    if not code.isalnum():
        return None
    title = data[0x00:0x0C].split(b"\x00")[0].decode("ascii", "replace").strip()
    return {
        "platform": "nds",
        "internal_title": title,
        "serial": f"NTR-{code}",
        "region": _NINTENDO_REGIONS.get(code[3], ""),
    }


def parse_3ds_header(read):
    """Product code (e.g. CTR-P-AXCE) from a 3DS image. `read(off, size)`
    reads the file. Handles NCSD cartridge dumps (.3ds/.cci), where the first
    NCCH partition's offset sits in the partition table, and bare NCCH (.cxi)."""
    head = read(0, 0x200)
    if len(head) < 0x200:
        return None
    if head[0x100:0x104] == b"NCSD":
        ncch = struct.unpack("<I", head[0x120:0x124])[0] * 0x200
        head = read(ncch, 0x200)
    if head[0x100:0x104] != b"NCCH":
        return None
    product = head[0x150:0x160].split(b"\x00")[0].decode("ascii", "replace").strip()
    code = product.split("-")[-1] if product else ""
    return {
        "platform": "3ds",
        "serial": product,
        "region": _NINTENDO_REGIONS.get(code[3:4], "") if len(code) == 4 else "",
    }


def _cartridge_info(key, read):
    """Header metadata for a cartridge platform, reading via `read(off, size)`."""
    try:
        if key == "gba":
            return parse_gba_header(read(0, 0x200))
        if key == "nds":
            return parse_nds_header(read(0, 0x200))
        if key == "3ds":
            return parse_3ds_header(read)
    except (struct.error, ValueError):
        return None
    return None


def _read_zip_cartridge(path):
    """(platform key, header bytes, inner name) for a zip holding a ROM."""
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            ext = os.path.splitext(info.filename)[1].lower()
            owners = platforms.by_extension(ext)
            if owners and not info.is_dir():
                with zf.open(info) as fh:
                    # Enough for every header parser (3DS reads at 0x4000+).
                    return owners[0].key, fh.read(0x8000), info.filename
    return None


# ------------------------------------------------------------------- CUE ----

def cue_tracks(path):
    """The data files a .cue sheet references, plus the first track's mode."""
    files, mode = [], None
    base = os.path.dirname(path)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                m = re.match(r'FILE\s+"?(.*?)"?\s+\w+$', line, re.I)
                if m:
                    files.append(os.path.join(base, m.group(1)))
                    continue
                m = re.match(r"TRACK\s+\d+\s+(\S+)", line, re.I)
                if m and mode is None:
                    mode = m.group(1).upper()
    except OSError:
        pass
    return files, mode


def _cue_source(path):
    files, mode = cue_tracks(path)
    if not files or not os.path.exists(files[0]):
        return None
    if mode and mode.startswith("MODE1/2352"):
        return RawSectorSource(files[0], 2352, 16)
    if mode and mode.startswith("MODE2/2352"):
        return RawSectorSource(files[0], 2352, 24)
    return FileSource(files[0])


# --------------------------------------------------------------- identify ---

def _file_size(path, ext):
    if ext == ".cue":
        files, _mode = cue_tracks(path)
        total = 0
        for f in files:
            try:
                total += os.path.getsize(f)
            except OSError:
                pass
        return total or os.path.getsize(path)
    return os.path.getsize(path)


def _sniff(path, ext):
    try:
        if ext == ".iso":
            source = FileSource(path)
        elif ext == ".cso":
            source = CsoSource(path)
        elif ext == ".cue":
            source = _cue_source(path)
        elif ext == ".chd":
            source = chd.ChdSource(path)
        else:
            return None
    except (OSError, ValueError, struct.error, chd.ChdError):
        return None
    if source is None:
        return None
    try:
        return sniff_disc(source)
    except (OSError, ValueError, struct.error, zlib.error, chd.ChdError):
        return None
    finally:
        source.close()


def _pbp(path):
    """An EBOOT.PBP: offsets to its PARAM.SFO and ICON0.PNG sit in the header."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(40)
            if len(head) < 40 or head[:4] != b"\x00PBP":
                return None
            offs = struct.unpack("<8I", head[8:40])
            fh.seek(offs[0])
            sfo = fh.read(max(0, offs[1] - offs[0]))
            fh.seek(offs[1])
            icon = fh.read(max(0, min(offs[2] - offs[1], 512 * 1024)))
    except (OSError, struct.error):
        return None
    params = parse_sfo(sfo)
    return {"platform": "psp",
            "internal_title": params.get("TITLE", ""),
            "serial": _format_ps_serial(params["DISC_ID"]) if params.get("DISC_ID") else "",
            "icon": icon if icon[:4] == b"\x89PNG" else b""}


def identify(path, folder_parts=()):
    """Return a GameInfo for `path`, or None if it isn't a ROM Dice knows.
    `folder_parts` are the folder names between the library root and the file
    (root included), used as a platform hint for formats that can't be sniffed.
    """
    p = Path(path)
    ext = p.suffix.lower()
    stem = p.stem
    if ext not in platforms.ALL_EXTENSIONS:
        return None
    hint = platforms.from_folder_hint(folder_parts)
    found = None

    if ext == ".zip":
        try:
            inner = _read_zip_cartridge(path)
        except (OSError, zipfile.BadZipFile):
            return None
        if inner is None:
            return None
        key, header, _name = inner
        found = _cartridge_info(key, lambda off, size: header[off:off + size]) \
            or {"platform": key}
        fmt = "ZIP"
    elif platforms.by_extension(ext) and ext not in (".pbp", ".cso"):
        key = platforms.by_extension(ext)[0].key
        try:
            with open(path, "rb") as fh:
                def read(off, size):
                    fh.seek(off)
                    return fh.read(size)
                found = _cartridge_info(key, read)
        except OSError:
            return None
        found = found or {"platform": key}
        fmt = ext.lstrip(".").upper()
    elif ext == ".m3u":
        # A multi-disc playlist is the game; its first disc says which one.
        members = [m for m in m3u_members(path) if os.path.exists(m)]
        if not members or os.path.splitext(members[0])[1].lower() == ".m3u":
            return None
        first = identify(members[0], folder_parts)
        if first is None:
            return None
        size = 0
        for member in members:
            try:
                size += _file_size(member, os.path.splitext(member)[1].lower())
            except OSError:
                pass
        return GameInfo(platform=first.platform, title=clean_title(stem),
                        serial=first.serial, internal_title=first.internal_title,
                        region=region_from_name(stem) or first.region, format="M3U",
                        size=size, icon=first.icon)
    elif ext == ".pbp":
        if stem.upper() != "EBOOT":
            return None
        found = _pbp(path)
        if found is None:
            return None
        # An EBOOT is always named EBOOT.PBP; its folder carries the name.
        stem = p.parent.name
        fmt = "PBP"
    else:
        fmt = ext.lstrip(".").upper()
        if ext in (".iso", ".cso", ".cue", ".chd"):
            found = _sniff(path, ext)
        if found is None:
            candidates = platforms.disc_candidates(ext) + platforms.by_extension(ext)
            if hint is not None and hint in candidates:
                found = {"platform": hint.key}
            elif ext == ".cso":
                found = {"platform": "psp"}   # CSO is effectively PSP-only
            elif ext == ".chd":
                # No folder hint: CD images are almost always PS1, DVDs PS2.
                media = chd_media(path)
                found = {"platform": "ps1" if media == "cd" else "ps2"}
            elif ext == ".cue":
                found = {"platform": "ps1"}   # a CD we couldn't read: PS1 is likelier
            else:
                # An ISO we couldn't read and no folder hint: not ours.
                return None

    try:
        size = _file_size(path, ext)
    except OSError:
        size = 0
    serial = found.get("serial", "")
    region = (region_from_name(stem) or found.get("region", "")
              or region_from_serial(serial))
    return GameInfo(
        platform=found["platform"],
        title=clean_title(stem),
        serial=serial,
        internal_title=found.get("internal_title", ""),
        region=region,
        format=fmt,
        size=size,
        icon=found.get("icon", b""),
    )
