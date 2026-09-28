"""Document-portal path translation (the portal itself is stubbed)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import portal  # noqa: E402


def fake_lookup(ids):
    return {"83265121": "/home/daniel/Roms"} if "83265121" in ids else {}


class PortalTests(unittest.TestCase):
    def test_translates_nested_path(self):
        path = "/run/user/1000/doc/83265121/Roms/GBA/Wario Land 4 (USA, Europe).zip"
        self.assertEqual(portal.host_path(path, fake_lookup),
                         "/home/daniel/Roms/GBA/Wario Land 4 (USA, Europe).zip")

    def test_translates_exported_folder_itself(self):
        self.assertEqual(portal.host_path("/run/user/1000/doc/83265121/Roms", fake_lookup),
                         "/home/daniel/Roms")

    def test_leaves_other_paths_alone(self):
        self.assertEqual(portal.host_path("/home/daniel/Roms/a.gba", fake_lookup),
                         "/home/daniel/Roms/a.gba")

    def test_unknown_doc_id_is_unchanged(self):
        path = "/run/user/1000/doc/deadbeef/Roms/a.gba"
        self.assertEqual(portal.host_path(path, fake_lookup), path)


if __name__ == "__main__":
    unittest.main()
