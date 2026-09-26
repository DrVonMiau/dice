"""Library/scanner tests: what survives a rescan, and what gets pruned."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from test_romscan import PNG, make_gba, make_iso, make_sfo  # noqa: E402

from src import library as lib  # noqa: E402


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        # Point the library's storage at the temp dir.
        lib.DATA_DIR = base / "data"
        lib.COVERS_DIR = lib.DATA_DIR / "covers"
        lib.DB_PATH = lib.DATA_DIR / "library.db"
        self.con = lib.connect()
        self.root = str(base / "roms")
        self._write("GBA/Golden Sun (USA).gba", make_gba("AGSE"))
        self._write("GBA/covers/Golden Sun (USA).png", PNG)
        self._write("PSP/Lumines (USA).iso", make_iso({
            "PSP_GAME/PARAM.SFO": make_sfo({"DISC_ID": "ULUS10046", "TITLE": "Lumines"}),
            "PSP_GAME/ICON0.PNG": PNG,
        }))
        self._write("notes/readme.txt", b"not a rom")
        lib.add_folder(self.con, self.root)

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def _write(self, rel, data):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def _game(self, platform):
        return next(r for r in lib.all_games(self.con) if r["platform"] == platform)

    def test_scan_finds_games_and_art(self):
        self.assertEqual(lib.scan_all(self.con), 2)
        gba = self._game("gba")
        self.assertEqual(gba["cover_source"], "sidecar")
        self.assertTrue(gba["cover_path"].endswith("covers/Golden Sun (USA).png"))
        psp = self._game("psp")
        self.assertEqual(psp["cover_source"], "embedded")
        self.assertTrue(os.path.exists(psp["cover_path"]))

    def test_rescan_keeps_user_state(self):
        lib.scan_all(self.con)
        gba = self._game("gba")
        lib.set_favorite(self.con, gba["id"], True)
        lib.record_launch(self.con, gba["id"])
        picked = self._write("picked.png", PNG)
        user_cover = lib.set_user_cover(self.con, gba["id"], picked)
        # Touch the ROM so the rescan re-reads it.
        os.utime(gba["path"], (1, 1))
        lib.scan_all(self.con)
        gba = self._game("gba")
        self.assertEqual(gba["favorite"], 1)
        self.assertEqual(gba["play_count"], 1)
        self.assertEqual(gba["cover_path"], user_cover)
        lib.clear_user_cover(self.con, gba["id"])
        self.assertEqual(self._game("gba")["cover_source"], "sidecar")
        self.assertFalse(os.path.exists(user_cover))

    def test_deleted_files_are_pruned(self):
        lib.scan_all(self.con)
        psp = self._game("psp")
        os.remove(psp["path"])
        lib.scan_all(self.con)
        self.assertEqual([r["platform"] for r in lib.all_games(self.con)], ["gba"])
        self.assertFalse(os.path.exists(psp["cover_path"]))

    def test_rebase_folder_keeps_state(self):
        lib.scan_all(self.con)
        gba = self._game("gba")
        lib.set_favorite(self.con, gba["id"], True)
        new_root = self.root + "-real"
        os.rename(self.root, new_root)
        lib.rebase_folder(self.con, self.root, new_root)
        self.assertEqual(lib.all_folders(self.con), [new_root])
        gba = self._game("gba")
        self.assertTrue(gba["path"].startswith(new_root + "/"))
        self.assertEqual(gba["favorite"], 1)
        self.assertTrue(os.path.exists(gba["cover_path"]))
        # A rescan at the new location finds the same games, no duplicates.
        self.assertEqual(lib.scan_all(self.con), 2)
        self.assertEqual(len(lib.all_games(self.con)), 2)

    def test_rescan_reidentifies_after_detection_changes(self):
        lib.scan_all(self.con)
        psp = self._game("psp")
        lib.set_favorite(self.con, psp["id"], True)
        # Simulate a row filed by an older scanner under the wrong platform.
        self.con.execute("UPDATE games SET platform='ps2', scan_version=0 WHERE id=?",
                         (psp["id"],))
        self.con.commit()
        lib.scan_all(self.con)
        row = lib.get_game(self.con, psp["id"])
        self.assertEqual(row["platform"], "psp")
        self.assertEqual(row["favorite"], 1)
        self.assertEqual(row["scan_version"], lib.SCAN_VERSION)

    def test_remove_folder_forgets_games(self):
        lib.scan_all(self.con)
        lib.remove_folder(self.con, self.root)
        self.assertEqual(lib.all_games(self.con), [])


if __name__ == "__main__":
    unittest.main()
