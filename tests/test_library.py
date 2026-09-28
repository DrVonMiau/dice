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


class DiscGroupingTests(unittest.TestCase):
    @staticmethod
    def row(i, path, fmt="CUE", platform="ps1", title="Final Fantasy VII"):
        return {"id": i, "path": path, "format": fmt, "platform": platform, "title": title}

    def test_disc_files_become_one_game(self):
        rows = [self.row(3, "/r/PS1/Final Fantasy VII (USA) (Disc 3).cue"),
                self.row(1, "/r/PS1/Final Fantasy VII (USA) (Disc 1).cue"),
                self.row(2, "/r/PS1/Final Fantasy VII (USA) (Disc 2).cue"),
                self.row(4, "/r/PS1/Spyro (USA).cue", title="Spyro")]
        shown, discs = lib.group_discs(rows)
        self.assertEqual(sorted(r["id"] for r in shown), [1, 4])
        self.assertEqual(discs[1], [(1, 1), (2, 2), (3, 3)])

    def test_playlist_hides_its_discs_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            m3u = os.path.join(tmp, "FF7.m3u")
            with open(m3u, "w") as fh:
                fh.write("FF7 (Disc 2).chd\nFF7 (Disc 1).chd\n")
            rows = [self.row(1, os.path.join(tmp, "FF7 (Disc 1).chd"), "CHD"),
                    self.row(2, os.path.join(tmp, "FF7 (Disc 2).chd"), "CHD"),
                    self.row(9, m3u, "M3U")]
            shown, discs = lib.group_discs(rows)
        self.assertEqual([r["id"] for r in shown], [9])
        self.assertEqual(discs[9], [(1, 2), (2, 1)])

    def test_single_disc_tag_is_left_alone(self):
        shown, discs = lib.group_discs([self.row(1, "/r/X (Disc 1).cue")])
        self.assertEqual((len(shown), discs), (1, {}))


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

    def test_renamed_file_keeps_its_history(self):
        lib.scan_all(self.con)
        gba = self._game("gba")
        lib.set_favorite(self.con, gba["id"], True)
        lib.record_launch(self.con, gba["id"])
        new_path = os.path.join(self.root, "GBA", "Golden Sun (USA, Europe).gba")
        os.rename(gba["path"], new_path)
        lib.scan_all(self.con)
        row = lib.get_game(self.con, gba["id"])
        self.assertEqual(row["path"], new_path)
        self.assertEqual(row["title"], "Golden Sun")
        self.assertEqual((row["favorite"], row["play_count"]), (1, 1))
        self.assertEqual(len(lib.all_games(self.con)), 2)

    def test_move_between_library_folders(self):
        other = self.root + "-2"
        os.makedirs(other)
        lib.add_folder(self.con, other)
        lib.scan_all(self.con)
        psp = self._game("psp")
        lib.set_favorite(self.con, psp["id"], True)
        os.rename(psp["path"], os.path.join(other, "Lumines.iso"))
        lib.scan_all(self.con)
        row = lib.get_game(self.con, psp["id"])
        self.assertTrue(row["path"].startswith(other + "/"))
        self.assertEqual(row["favorite"], 1)

    def test_duplicates_are_not_merged(self):
        lib.scan_all(self.con)
        gba = self._game("gba")
        with open(gba["path"], "rb") as fh:
            data = fh.read()
        # One copy disappears while two identical copies appear: ambiguous,
        # so both are new games and the old row goes.
        os.remove(gba["path"])
        self._write("GBA/copy one.gba", data)
        self._write("GBA/copy two.gba", data)
        lib.scan_all(self.con)
        self.assertIsNone(lib.get_game(self.con, gba["id"]))
        self.assertEqual(len([r for r in lib.all_games(self.con) if r["platform"] == "gba"]), 2)

    def test_remove_folder_forgets_games(self):
        lib.scan_all(self.con)
        lib.remove_folder(self.con, self.root)
        self.assertEqual(lib.all_games(self.con), [])


if __name__ == "__main__":
    unittest.main()
