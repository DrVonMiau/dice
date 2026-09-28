"""CHD reading against real chdman output (tests/fixtures, made with
`chdman createdvd` / `createcd` from synthetic PS1/PS2 discs)."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import chd, romscan  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    return os.path.join(FIXTURES, name)


class ChdTests(unittest.TestCase):
    def test_dvd_hunks_match_source_iso(self):
        with open(fixture("ps2.iso"), "rb") as fh:
            iso = fh.read()
        for name in ("ps2.chd", "ps2-zlib.chd"):
            image = chd.Chd(fixture(name))
            checked = 0
            for index in range(len(image._map)):
                try:
                    data = image.hunk(index)
                except chd.ChdError:
                    continue            # FLAC hunks aren't supported
                size = image.hunkbytes
                self.assertEqual(data, iso[index * size:(index + 1) * size].ljust(size, b"\x00"))
                checked += 1
            self.assertGreater(checked, 10, name)
            image.close()

    def test_media_types(self):
        for name, media in (("ps2.chd", "dvd"), ("ps1.chd", "cd"), ("ps1-cdzl.chd", "cd")):
            source = chd.ChdSource(fixture(name))
            self.assertEqual(source.media, media)
            source.close()

    def test_not_a_chd(self):
        with self.assertRaises(chd.ChdError):
            chd.Chd(fixture("ps2.iso"))


class ChdIdentifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _copy(self, name, as_name, folder=""):
        dest_dir = os.path.join(self.tmp.name, folder)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, as_name)
        shutil.copy(fixture(name), dest)
        return dest

    def test_ps2_chd_reads_serial_and_region(self):
        path = self._copy("ps2.chd", "Burnout 3 - Takedown (Europe Australia) (EnEsNlSv).chd",
                          "PS2")
        info = romscan.identify(path, ("Roms", "PS2"))
        self.assertEqual(info.platform, "ps2")
        self.assertEqual(info.serial, "SLES-52584")
        self.assertEqual(info.region, "Europe")

    def test_ps1_chd_found_even_in_ps2_folder(self):
        for name in ("ps1.chd", "ps1-cdzl.chd"):
            path = self._copy(name, "Spyro the Dragon (USA).chd", "PS2")
            info = romscan.identify(path, ("Roms", "PS2"))
            self.assertEqual(info.platform, "ps1", name)
            self.assertEqual(info.serial, "SCUS-94426")


if __name__ == "__main__":
    unittest.main()
