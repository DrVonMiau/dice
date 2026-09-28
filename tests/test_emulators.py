"""Launch-command building and Flatpak permission checks."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import emulators  # noqa: E402

HOME = "/home/daniel"
ROM = "/home/daniel/Roms/GBA/Wario Land 4 (USA, Europe).zip"


class CommandTests(unittest.TestCase):
    def test_rom_stays_one_argument(self):
        argv = emulators.build_argv("flatpak run io.mgba.mGBA {rom}", ROM)
        self.assertEqual(argv, ["flatpak", "run", "io.mgba.mGBA", ROM])

    def test_flatpak_app(self):
        self.assertEqual(emulators.flatpak_app(
            ["flatpak", "run", "--branch=stable", "io.mgba.mGBA", ROM]), "io.mgba.mGBA")
        self.assertIsNone(emulators.flatpak_app(["mgba-qt", ROM]))

    def test_file_forwarding(self):
        argv = ["flatpak", "run", "io.mgba.mGBA", ROM]
        self.assertEqual(emulators.with_file_forwarding(argv, ROM),
                         ["flatpak", "run", "--file-forwarding", "io.mgba.mGBA", "@@", ROM, "@@"])


class AccessTests(unittest.TestCase):
    def check(self, filesystems, path=ROM):
        return emulators.can_access(filesystems, path, home=HOME)

    def test_home_and_host(self):
        self.assertTrue(self.check(["home"]))
        self.assertTrue(self.check(["home:ro"]))
        self.assertTrue(self.check(["host"]))

    def test_specific_folders(self):
        self.assertTrue(self.check(["~/Roms"]))
        self.assertTrue(self.check(["/home/daniel/Roms:rw"]))
        self.assertFalse(self.check(["~/Games"]))
        self.assertFalse(self.check(["/home/daniel/Rom"]))   # prefix, not a parent

    def test_xdg_download(self):
        path = "/home/daniel/Downloads/archivedwl-848/a.gba"
        # get_user_special_dir may resolve to this machine's dirs; the
        # fallback is ~/Downloads under the given home.
        self.assertTrue(self.check(["xdg-download"], path)
                        or emulators._xdg_dir("xdg-download", HOME) != "/home/daniel/Downloads")

    def test_nothing_useful(self):
        self.assertFalse(self.check([]))
        self.assertFalse(self.check(["host-os", "xdg-run/gvfs", "!home"]))


if __name__ == "__main__":
    unittest.main()
