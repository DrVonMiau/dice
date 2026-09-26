"""Unit tests for ROM detection. Run from the repo root:

    python3 -m unittest discover -s tests

Fixtures are synthesised on the fly (tiny ISO9660 images, a GBA header, a CSO
wrapper), so no copyrighted data is needed.
"""
import os
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import platforms, romscan  # noqa: E402

SECTOR = 2048


def _dir_record(name, extent, size, is_dir):
    raw_name = name.encode("ascii")
    length = 33 + len(raw_name)
    length += length % 2
    rec = bytearray(length)
    rec[0] = length
    rec[2:6] = struct.pack("<I", extent)
    rec[6:10] = struct.pack(">I", extent)
    rec[10:14] = struct.pack("<I", size)
    rec[14:18] = struct.pack(">I", size)
    rec[25] = 2 if is_dir else 0
    rec[32] = len(raw_name)
    rec[33:33 + len(raw_name)] = raw_name
    return bytes(rec)


def make_iso(files, volume_id="TESTDISC"):
    """A minimal ISO9660 image holding `files` ({'DIR/NAME.EXT': bytes}),
    at most one directory level deep."""
    dirs = {}
    for path, data in files.items():
        parent, _, name = path.rpartition("/")
        dirs.setdefault(parent, {})[name] = data
    next_sector = 18
    dir_sectors = {"": next_sector}
    for d in dirs:
        if d:
            next_sector += 1
            dir_sectors[d] = next_sector
    next_sector += 1
    file_sectors = {}
    for path, data in files.items():
        file_sectors[path] = next_sector
        next_sector += max(1, (len(data) + SECTOR - 1) // SECTOR)
    image = bytearray(next_sector * SECTOR)

    def dir_block(d, self_sector):
        block = _dir_record("\x00", self_sector, SECTOR, True)
        block += _dir_record("\x01", dir_sectors[""], SECTOR, True)
        if d == "":
            for sub in dirs:
                if sub:
                    block += _dir_record(sub, dir_sectors[sub], SECTOR, True)
        for name, data in dirs.get(d, {}).items():
            full = f"{d}/{name}" if d else name
            block += _dir_record(name + ";1", file_sectors[full], len(data), False)
        return block

    for d, sector in dir_sectors.items():
        block = dir_block(d, sector)
        image[sector * SECTOR:sector * SECTOR + len(block)] = block
    for path, sector in file_sectors.items():
        data = files[path]
        image[sector * SECTOR:sector * SECTOR + len(data)] = data

    pvd = bytearray(SECTOR)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[6] = 1
    pvd[40:72] = volume_id.ljust(32).encode("ascii")
    root = _dir_record("\x00", dir_sectors[""], SECTOR, True)
    pvd[156:156 + len(root)] = root
    image[16 * SECTOR:17 * SECTOR] = pvd
    return bytes(image)


def make_sfo(entries):
    keys = b""
    data = b""
    index = b""
    for key, value in entries.items():
        raw = value.encode("utf-8") + b"\x00"
        raw += b"\x00" * ((4 - len(raw) % 4) % 4)
        index += struct.pack("<HHIII", len(keys), 0x0204, len(value) + 1, len(raw), len(data))
        keys += key.encode("ascii") + b"\x00"
        data += raw
    keys += b"\x00" * ((4 - len(keys) % 4) % 4)
    key_start = 20 + len(index)
    data_start = key_start + len(keys)
    header = b"\x00PSF" + struct.pack("<IIII", 0x0101, key_start, data_start, len(entries))
    return header + index + keys + data


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def make_gba(code="BPEE", title="POKEMON EMER"):
    header = bytearray(0x200)
    header[0xA0:0xAC] = title.encode("ascii").ljust(12, b"\x00")
    header[0xAC:0xB0] = code.encode("ascii")
    header[0xB0:0xB2] = b"01"
    header[0xB2] = 0x96
    return bytes(header)


def make_cso(data, block=SECTOR):
    count = (len(data) + block - 1) // block
    blocks = []
    for i in range(count):
        chunk = data[i * block:(i + 1) * block]
        comp = zlib.compressobj(9, zlib.DEFLATED, -15)
        packed = comp.compress(chunk) + comp.flush()
        # Store incompressible blocks plain, as real encoders do.
        blocks.append((packed, False) if len(packed) < len(chunk) else (chunk, True))
    offset = 24 + 4 * (count + 1)
    index = []
    body = b""
    for packed, plain in blocks:
        index.append((offset + len(body)) | (0x80000000 if plain else 0))
        body += packed
    index.append(offset + len(body))
    header = b"CISO" + struct.pack("<IQIBB2x", 24, len(data), block, 1, 0)
    return header + struct.pack(f"<{count + 1}I", *index) + body


class TitleTests(unittest.TestCase):
    def test_clean_title_strips_tags_and_moves_article(self):
        self.assertEqual(
            romscan.clean_title("Legend of Zelda, The - The Minish Cap (USA) [!]"),
            "The Legend of Zelda - The Minish Cap")

    def test_clean_title_underscores(self):
        self.assertEqual(romscan.clean_title("golden_sun"), "golden sun")

    def test_region_from_name(self):
        self.assertEqual(romscan.region_from_name("Game (Europe) (En,Fr)"), "Europe")
        self.assertEqual(romscan.region_from_name("Game (USA, Europe)"), "USA")
        self.assertEqual(romscan.region_from_name("Game"), "")

    def test_region_from_serial(self):
        self.assertEqual(romscan.region_from_serial("SLUS-20946"), "USA")
        self.assertEqual(romscan.region_from_serial("ULJM-05500"), "Japan")


class IdentifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, data):
        path = os.path.join(self.dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_gba_header(self):
        path = self._write("Pokemon - Emerald Version (USA, Europe).gba", make_gba())
        info = romscan.identify(path)
        self.assertEqual(info.platform, "gba")
        self.assertEqual(info.title, "Pokemon - Emerald Version")
        self.assertEqual(info.serial, "AGB-BPEE")
        self.assertEqual(info.region, "USA")
        self.assertEqual(info.format, "GBA")

    def test_zipped_gba(self):
        path = os.path.join(self.dir, "Golden Sun (Japan).zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("Golden Sun (Japan).gba", make_gba("AGSJ", "GOLDEN SUN"))
        info = romscan.identify(path)
        self.assertEqual(info.platform, "gba")
        self.assertEqual(info.serial, "AGB-AGSJ")
        self.assertEqual(info.format, "ZIP")

    def test_zip_without_rom_is_ignored(self):
        path = os.path.join(self.dir, "notes.zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("readme.txt", "hi")
        self.assertIsNone(romscan.identify(path))

    def _psp_iso(self):
        return make_iso({
            "UMD_DATA.BIN": b"ULUS-10041|0000000000000001|0001|G",
            "PSP_GAME/PARAM.SFO": make_sfo({"DISC_ID": "ULUS10041", "TITLE": "Lumines"}),
            "PSP_GAME/ICON0.PNG": PNG,
        })

    def test_psp_iso(self):
        path = self._write("Lumines (USA).iso", self._psp_iso())
        info = romscan.identify(path)
        self.assertEqual(info.platform, "psp")
        self.assertEqual(info.serial, "ULUS-10041")
        self.assertEqual(info.internal_title, "Lumines")
        self.assertTrue(info.icon.startswith(b"\x89PNG"))

    def test_psp_cso(self):
        path = self._write("Lumines.cso", make_cso(self._psp_iso()))
        info = romscan.identify(path)
        self.assertEqual(info.platform, "psp")
        self.assertEqual(info.serial, "ULUS-10041")
        self.assertEqual(info.region, "USA")
        self.assertEqual(info.format, "CSO")

    def test_ps2_iso(self):
        cnf = b"BOOT2 = cdrom0:\\SLUS_209.46;1\r\nVER = 1.00\r\n"
        path = self._write("Shadow of the Colossus.iso", make_iso({"SYSTEM.CNF": cnf}))
        info = romscan.identify(path)
        self.assertEqual(info.platform, "ps2")
        self.assertEqual(info.serial, "SLUS-20946")
        self.assertEqual(info.region, "USA")

    def test_ps2_bin_cue(self):
        iso = make_iso({"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLES_123.45;1\n"})
        raw = bytearray()
        for i in range(0, len(iso), SECTOR):
            raw += b"\x00" * 24 + iso[i:i + SECTOR] + b"\x00" * 280
        self._write("Game (Europe).bin", bytes(raw))
        cue = self._write("Game (Europe).cue",
                          b'FILE "Game (Europe).bin" BINARY\n  TRACK 01 MODE2/2352\n'
                          b'    INDEX 01 00:00:00\n')
        info = romscan.identify(cue)
        self.assertEqual(info.platform, "ps2")
        self.assertEqual(info.serial, "SLES-12345")
        self.assertEqual(info.size, len(raw))

    def test_chd_uses_folder_hint(self):
        path = self._write("PSP/Game.chd", b"MComprHD")
        info = romscan.identify(path, ("ROMs", "PSP"))
        self.assertEqual(info.platform, "psp")
        path = self._write("misc/Other.chd", b"MComprHD")
        self.assertEqual(romscan.identify(path, ("misc",)).platform, "ps2")

    def test_unreadable_iso_without_hint_is_skipped(self):
        path = self._write("random.iso", b"\x00" * 40000)
        self.assertIsNone(romscan.identify(path))
        self.assertEqual(romscan.identify(path, ("PS2",)).platform, "ps2")

    def test_unknown_extension(self):
        path = self._write("readme.txt", b"hello")
        self.assertIsNone(romscan.identify(path))


class PlatformTests(unittest.TestCase):
    def test_folder_hint_innermost_wins(self):
        self.assertEqual(platforms.from_folder_hint(("PSP", "PS2")).key, "ps2")
        self.assertIsNone(platforms.from_folder_hint(("Games",)))


if __name__ == "__main__":
    unittest.main()
