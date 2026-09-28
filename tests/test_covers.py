"""Cover-name matching (offline: the databases are stubbed)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import covers  # noqa: E402


class StubDatabases:
    NAMES = [
        "Golden Sun (USA, Europe)", "Golden Sun (Italy)", "Golden Sun (Japan)",
        "Golden Sun - The Lost Age (USA, Europe)",
        "Legend of Zelda, The - The Minish Cap (Europe) (En,Fr,De,Es,It)",
        "Legend of Zelda, The - The Minish Cap (USA)",
        "Mario Kart DS (Europe) (En,Fr,De,Es,It) (Wii U Virtual Console)",
        "Mario Kart DS (Europe) (En,Fr,De,Es,It)",
        "Mario Kart DS (USA) (Beta)",
        "Final Fantasy VII (USA) (Disc 1)", "Final Fantasy VII (USA) (Disc 2)",
    ]

    def names(self, platform):
        return self.NAMES

    def by_serial(self, system):
        return {"SCUS-94163": [("Final Fantasy VII (USA) (Disc 1)", 1),
                               ("Final Fantasy VII (USA) (Disc 2)", 2)]}

    def by_crc(self, system):
        return {}


class CoverNameTests(unittest.TestCase):
    def test_normalise(self):
        self.assertEqual(covers.normalise("Legend of Zelda, The - The Minish Cap (USA)"),
                         covers.normalise("The Legend of Zelda - The Minish Cap"))
        self.assertEqual(covers.normalise("Kirby & The Amazing Mirror"),
                         "kirby and the amazing mirror")

    def test_fuzzy_prefers_region_and_skips_sequels(self):
        names = covers.fuzzy_names("Golden Sun", "Japan", StubDatabases.NAMES)
        self.assertEqual(names[0], "Golden Sun (Japan)")
        self.assertNotIn("Golden Sun - The Lost Age (USA, Europe)", names)
        zelda = covers.fuzzy_names("The Legend of Zelda - The Minish Cap", "USA",
                                   StubDatabases.NAMES)
        self.assertEqual(zelda[0], "Legend of Zelda, The - The Minish Cap (USA)")

    def test_rereleases_and_betas_come_last(self):
        names = covers.fuzzy_names("Mario Kart DS", "Europe", StubDatabases.NAMES)
        self.assertEqual(names[0], "Mario Kart DS (Europe) (En,Fr,De,Es,It)")

    def test_candidates_include_whole_game_for_discs(self):
        names = covers.candidate_names("/r/ff7 d1.cue", "ps1", "Final Fantasy VII", "USA",
                                       "SCUS-94163", None, StubDatabases())
        self.assertIn("Final Fantasy VII (USA)", names)
        self.assertEqual(names[0], "ff7 d1")   # serial is ambiguous: file name first

    def test_unsafe_characters(self):
        self.assertEqual(covers.thumbnail_name("Ratchet & Clank: Going Commando"),
                         "Ratchet _ Clank_ Going Commando")


if __name__ == "__main__":
    unittest.main()
