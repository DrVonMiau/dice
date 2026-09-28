"""tools/fix-names.py against tiny hand-written DATs."""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from test_romscan import SECTOR, make_gba, make_iso  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "fix_names", os.path.join(HERE, "..", "tools", "fix-names.py"))
fix_names = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix_names)


def dat_entry(name, rom, size, crc, serial=None):
    s = f'\tserial "{serial}"\n' if serial else ""
    return (f'game (\n\tname "{name}"\n{s}\trom ( name "{rom}" size {size} '
            f'crc {crc:08X} md5 0 sha1 0 )\n)\n')


class FixNamesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dats = os.path.join(self.tmp, "dats")
        self.roms = os.path.join(self.tmp, "roms")
        os.makedirs(self.dats)
        os.makedirs(os.path.join(self.roms, "PS2"))
        self.gba = make_gba("AGSE") + b"\x01" * 256
        with open(os.path.join(self.dats, "Nintendo - Game Boy Advance.dat"), "w") as fh:
            fh.write(dat_entry("Golden Sun (USA, Europe)", "Golden Sun (USA, Europe).gba",
                               len(self.gba), zlib.crc32(self.gba)))
        with open(os.path.join(self.dats, "Sony - PlayStation 2.dat"), "w") as fh:
            fh.write(dat_entry("Burnout 3 - Takedown (Europe, Australia) (En,Es,Nl,Sv)",
                               "x.iso", 1, 0, "SLES-52584"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _write(self, rel, data):
        path = os.path.join(self.roms, rel)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_preview_then_apply_then_undo(self):
        self._write("golden.gba", self.gba)
        self._write("golden.sav", b"save")
        shutil.copy(os.path.join(HERE, "fixtures", "ps2.chd"),
                    os.path.join(self.roms, "PS2", "burnout.chd"))
        self._write("hack.gba", make_gba("XXXX") + b"\x02" * 99)
        plan = fix_names.build_plan(self.roms, self.dats)
        moves = sorted(os.path.basename(op[2]) for op in plan.ops)
        self.assertEqual(moves, [
            "Burnout 3 - Takedown (Europe, Australia) (En,Es,Nl,Sv).chd",
            "Golden Sun (USA, Europe).gba", "Golden Sun (USA, Europe).sav"])
        self.assertEqual([os.path.basename(p) for p, _ in plan.unknown], ["hack.gba"])
        before = sorted(os.listdir(self.roms))
        undo = fix_names.apply(plan)
        self.assertTrue(os.path.exists(os.path.join(self.roms, "Golden Sun (USA, Europe).sav")))
        os.system(f"sh '{undo}'")
        os.remove(undo)
        self.assertEqual(sorted(os.listdir(self.roms)), before)

    def test_cue_tracks_follow(self):
        iso = make_iso({"SYSTEM.CNF": b"BOOT2 = cdrom0:\\SLES_525.84;1\n"})
        raw = b"".join(b"\x00" * 24 + iso[i:i + SECTOR] + b"\x00" * 280
                       for i in range(0, len(iso), SECTOR))
        self._write("PS2/b.bin", raw)
        self._write("PS2/b.cue", b'FILE "b.bin" BINARY\n  TRACK 01 MODE2/2352\n')
        plan = fix_names.build_plan(self.roms, self.dats)
        fix_names.apply(plan)
        name = "Burnout 3 - Takedown (Europe, Australia) (En,Es,Nl,Sv)"
        with open(os.path.join(self.roms, "PS2", name + ".cue")) as fh:
            self.assertIn(f'FILE "{name}.bin"', fh.read())
        self.assertTrue(os.path.exists(os.path.join(self.roms, "PS2", name + ".bin")))


if __name__ == "__main__":
    unittest.main()
