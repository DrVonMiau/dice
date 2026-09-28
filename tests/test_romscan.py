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

    def test_catalogue_numbers_are_dropped(self):
        self.assertEqual(romscan.clean_title(
            "1514 - Legend of Zelda - Phantom Hourglass, The (E)(EXiMiUS)"),
            "The Legend of Zelda - Phantom Hourglass")
        self.assertEqual(romscan.clean_title("007 - Everything or Nothing (Japan)"),
                         "007 - Everything or Nothing")

    def test_title_key(self):
        self.assertEqual(romscan.title_key("Kirby And The Amazing Mirror"),
                         romscan.title_key("Kirby & the amazing mirror"))
        self.assertEqual(romscan.title_key("Legend of Zelda, The - Minish Cap (USA)"),
                         romscan.title_key("The Legend of Zelda: Minish Cap"))

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


def make_nds(code="ASME", title="SUPERMARIO64"):
    header = bytearray(0x200)
    header[0x00:0x0C] = title.encode("ascii").ljust(12, b"\x00")
    header[0x0C:0x10] = code.encode("ascii")
    return bytes(header)


def make_3ds(product="CTR-P-AREE"):
    """An NCSD cartridge image whose first NCCH partition sits at 0x4000."""
    image = bytearray(0x4200)
    image[0x100:0x104] = b"NCSD"
    image[0x120:0x124] = struct.pack("<I", 0x4000 // 0x200)
    image[0x4100:0x4104] = b"NCCH"
    image[0x4150:0x4150 + len(product)] = product.encode("ascii")
    return bytes(image)


class NintendoHandheldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_nds(self):
        info = romscan.identify(self._write("Super Mario 64 DS (USA).nds", make_nds()))
        self.assertEqual(info.platform, "nds")
        self.assertEqual(info.serial, "NTR-ASME")
        self.assertEqual(info.region, "USA")

    def test_zipped_nds(self):
        path = os.path.join(self.tmp.name, "Mario Kart DS (Europe).zip")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("Mario Kart DS (Europe).nds", make_nds("AMCP", "MARIOKARTDS"))
        info = romscan.identify(path)
        self.assertEqual(info.platform, "nds")
        self.assertEqual(info.serial, "NTR-AMCP")

    def test_3ds_ncsd(self):
        info = romscan.identify(self._write("Mario Kart 7.3ds", make_3ds("CTR-P-AMKE")))
        self.assertEqual(info.platform, "3ds")
        self.assertEqual(info.serial, "CTR-P-AMKE")
        self.assertEqual(info.region, "USA")
        self.assertEqual(info.format, "3DS")

    def test_3dsx_without_header_still_counts(self):
        info = romscan.identify(self._write("homebrew.3dsx", b"3DSX" + b"\x00" * 64))
        self.assertEqual(info.platform, "3ds")


def make_chd(tag):
    """A v5 CHD header whose metadata chain holds one entry with `tag`."""
    head = bytearray(0x7C)
    head[0:8] = b"MComprHD"
    head[8:12] = struct.pack(">I", 0x7C)
    head[12:16] = struct.pack(">I", 5)
    head[0x30:0x38] = struct.pack(">Q", 0x80)
    entry = tag + b"\x01" + (8).to_bytes(3, "big") + struct.pack(">Q", 0) + b"\x00" * 8
    return bytes(head) + b"\x00" * 4 + entry + b"TYPE:X\x00\x00"


class PlayStationOneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_ps1_bin_cue_in_a_ps2_style_library(self):
        iso = make_iso({"SYSTEM.CNF": b"BOOT = cdrom:\\SCUS_944.26;1\r\nTCB = 4\r\n"})
        raw = bytearray()
        for i in range(0, len(iso), SECTOR):
            raw += b"\x00" * 24 + iso[i:i + SECTOR] + b"\x00" * 280
        self._write("Spyro the Dragon (USA).bin", bytes(raw))
        cue = self._write("Spyro the Dragon (USA).cue",
                          b'FILE "Spyro the Dragon (USA).bin" BINARY\n  TRACK 01 MODE2/2352\n')
        info = romscan.identify(cue)
        self.assertEqual(info.platform, "ps1")
        self.assertEqual(info.serial, "SCUS-94426")
        self.assertEqual(info.region, "USA")

    def test_ps1_boot_without_backslash(self):
        path = self._write("a.iso", make_iso({"SYSTEM.CNF": b"BOOT=cdrom:SLES_012.34;1\n"}))
        info = romscan.identify(path)
        self.assertEqual(info.platform, "ps1")
        self.assertEqual(info.serial, "SLES-01234")

    def test_early_ps1_disc_with_psx_exe(self):
        path = self._write("old.iso", make_iso({"PSX.EXE": b"PS-X EXE"}))
        self.assertEqual(romscan.identify(path).platform, "ps1")

    def test_ps2_still_ps2(self):
        path = self._write("b.iso", make_iso({"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLUS_209.46;1\n"}))
        self.assertEqual(romscan.identify(path).platform, "ps2")

    def test_chd_media_type_without_hint(self):
        cd = self._write("Crash (USA).chd", make_chd(b"CHT2"))
        dvd = self._write("Okami (USA).chd", make_chd(b"DVD "))
        self.assertEqual(romscan.identify(cd, ("Roms",)).platform, "ps1")
        self.assertEqual(romscan.identify(dvd, ("Roms",)).platform, "ps2")

    def test_chd_folder_hint_wins(self):
        cd = self._write("Game.chd", make_chd(b"CHT2"))
        self.assertEqual(romscan.identify(cd, ("PS2",)).platform, "ps2")
        self.assertEqual(romscan.identify(cd, ("PSX",)).platform, "ps1")


class MultiDiscTests(unittest.TestCase):
    def test_disc_number(self):
        self.assertEqual(romscan.disc_number("FF VII (USA) (Disc 2)"), 2)
        self.assertEqual(romscan.disc_number("Game (Disc 1 of 3)"), 1)
        self.assertEqual(romscan.disc_number("Game (CD2)"), 2)
        self.assertIsNone(romscan.disc_number("Discworld (Europe)"))

    def test_m3u_takes_platform_from_first_disc(self):
        with tempfile.TemporaryDirectory() as tmp:
            iso = make_iso({"SYSTEM.CNF": b"BOOT = cdrom:\\SCUS_941.63;1\n"})
            for n in (1, 2):
                with open(os.path.join(tmp, f"FF7 (Disc {n}).iso"), "wb") as fh:
                    fh.write(iso)
            m3u = os.path.join(tmp, "Final Fantasy VII (USA).m3u")
            with open(m3u, "w") as fh:
                fh.write("# playlist\nFF7 (Disc 1).iso\nFF7 (Disc 2).iso\n")
            info = romscan.identify(m3u)
        self.assertEqual((info.platform, info.format, info.title),
                         ("ps1", "M3U", "Final Fantasy VII"))
        self.assertEqual(info.size, 2 * len(iso))

    def test_unrelated_m3u_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            m3u = os.path.join(tmp, "music.m3u")
            with open(m3u, "w") as fh:
                fh.write("song.mp3\n")
            self.assertIsNone(romscan.identify(m3u))


class PlatformTests(unittest.TestCase):
    def test_folder_hint_innermost_wins(self):
        self.assertEqual(platforms.from_folder_hint(("PSP", "PS2")).key, "ps2")
        self.assertIsNone(platforms.from_folder_hint(("Games",)))


if __name__ == "__main__":
    unittest.main()
