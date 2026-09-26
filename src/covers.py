"""Online cover art from the libretro thumbnail archive.

libretro publishes box art for No-Intro / Redump-named games at
  https://thumbnails.libretro.com/<System>/Named_Boxarts/<Game Name>.png
so a ROM whose file name follows those conventions — the norm for dumped
libraries — can be matched by name alone, with no account or API key. Art
next to the ROM, art inside the disc and covers the user picked always win;
this is only a fallback for games with nothing else (see library.py).
"""
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import platforms

BASE_URL = "https://thumbnails.libretro.com"
USER_AGENT = "Dice/0.1 (+https://github.com/DrVonMiau/roms)"
# libretro replaces these characters with '_' in thumbnail file names.
_UNSAFE = re.compile(r'[&*/:`<>?\\|"]')


def candidate_names(rom_path):
    """File-name spellings to try, most specific first."""
    p = Path(rom_path)
    stem = p.parent.name if p.suffix.lower() == ".pbp" else p.stem
    names = [stem]
    no_flags = re.sub(r"\s*\[[^\]]*\]", "", stem).strip()   # drop [!], [b1] …
    if no_flags and no_flags not in names:
        names.append(no_flags)
    return [_UNSAFE.sub("_", n) for n in names]


def fetch(platform_key, rom_path, timeout=8):
    """Download box art for a game. Returns PNG bytes or None."""
    platform = platforms.get(platform_key)
    if platform is None or not platform.libretro_system:
        return None
    system = urllib.parse.quote(platform.libretro_system)
    for name in candidate_names(rom_path):
        url = f"{BASE_URL}/{system}/Named_Boxarts/{urllib.parse.quote(name)}.png"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read()
        except (urllib.error.URLError, OSError, ValueError):
            continue
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return data
    return None
