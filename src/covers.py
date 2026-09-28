"""Box art from the libretro thumbnail archive.

libretro publishes box art named after each game's official No-Intro /
Redump name:
  https://thumbnails.libretro.com/<System>/Named_Boxarts/<Game Name>.png
mirrored on GitHub (libretro-thumbnails/<System>). No account or API key is
needed. A file named anything else never matches that exactly, so Dice tries,
in order:

  1. the official name, worked out from what the game *is*: its serial (discs)
     or checksum (cartridges), looked up in the No-Intro / Redump databases;
  2. the file name as it is (and without [!]-style flags);
  3. official names whose title matches the game's, preferring its region.

Art next to the ROM, art inside the disc and covers the user picked always
win; this is only for games with nothing else (see library.py).
"""
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import datfiles, platforms, romscan

BASE_URLS = (
    "https://thumbnails.libretro.com/{system}/Named_Boxarts/{name}.png",
    "https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/Named_Boxarts/{name}.png",
)
USER_AGENT = "Dice/0.1 (+https://github.com/DrVonMiau/dice)"
# libretro replaces these characters with '_' in thumbnail file names.
_UNSAFE = re.compile(r'[&*/:`<>?\\|"]')
MAX_FUZZY = 3
_DISC_TAG = re.compile(r"\s*\((?:disc|disk|cd)\s*\d+[^)]*\)", re.I)


def thumbnail_name(name):
    return _UNSAFE.sub("_", name)


def normalise(title):
    """'The Legend of Zelda: Minish Cap' and 'Legend of Zelda, The - Minish
    Cap' both → 'legend of zelda minish cap' (see romscan.title_key)."""
    return romscan.title_key(title)


def _region_rank(name, region):
    """Lower is better: the game's own region, then world/USA/Europe."""
    tags = " ".join(re.findall(r"\(([^)]*)\)", name)).lower()
    order = [region.lower()] if region else []
    order += ["world", "usa", "europe", "japan"]
    for rank, want in enumerate(order):
        if want and want in tags:
            return rank
    return len(order)


def fuzzy_names(title, region, names):
    """Official names with the same normalised title, best first. Betas,
    demos and prototypes come last."""
    want = normalise(title)
    if not want:
        return []
    matches = [n for n in names if normalise(n) == want]
    junk = re.compile(r"\((?:beta|demo|proto|sample|kiosk|[^)]*virtual console)[^)]*\)", re.I)
    matches.sort(key=lambda n: (bool(junk.search(n)), _region_rank(n, region), len(n)))
    return matches[:MAX_FUZZY]


def candidate_names(rom_path, platform=None, title="", region="", serial="",
                    size=None, dbs=None):
    """Thumbnail names to try, most certain first, without duplicates."""
    p = Path(rom_path)
    names = []
    if dbs is not None and platform in datfiles.SYSTEMS:
        try:
            official = datfiles.official_name(dbs, rom_path, platform, serial, size)
        except Exception:
            official = None
        if official:
            names.append(official)
    stem = p.parent.name if p.suffix.lower() == ".pbp" else p.stem
    names.append(stem)
    no_flags = re.sub(r"\s*\[[^\]]*\]", "", stem).strip()
    if no_flags:
        names.append(no_flags)
    if dbs is not None and platform in datfiles.SYSTEMS and title:
        try:
            names += fuzzy_names(title, region, dbs.names(platform))
        except Exception:
            pass
    # Multi-disc games have one box: "Final Fantasy VII (USA) (Disc 1)" is
    # filed as "Final Fantasy VII (USA)".
    expanded = []
    for name in names:
        expanded.append(name)
        whole = _DISC_TAG.sub("", name).strip()
        if whole != name:
            expanded.append(whole)
    seen, out = set(), []
    for name in expanded:
        safe = thumbnail_name(name)
        if safe not in seen:
            seen.add(safe)
            out.append(safe)
    return out


def _download(url, timeout):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return data if data[:8] == b"\x89PNG\r\n\x1a\n" else None


def fetch(platform_key, rom_path, title="", region="", serial="", size=None,
          dbs=None, timeout=8):
    """Download box art for a game. Returns PNG bytes or None."""
    platform = platforms.get(platform_key)
    if platform is None or not platform.libretro_system:
        return None
    system = platform.libretro_system
    repo = system.replace(" ", "_")
    dead = set()     # hosts that failed to connect: don't retry them per name
    for name in candidate_names(rom_path, platform_key, title, region, serial, size, dbs):
        for template in BASE_URLS:
            host = template.split("/")[2]
            if host in dead:
                continue
            url = template.format(system=urllib.parse.quote(system),
                                  repo=urllib.parse.quote(repo),
                                  name=urllib.parse.quote(name))
            data = _download(url, timeout)
            if data:
                return data
            if not _host_reachable(host, timeout):
                dead.add(host)
    return None


_REACHABLE = {}


def _host_reachable(host, timeout):
    """Whether a host answers at all (a 404 still counts). Remembered for the
    session so an unreachable mirror costs one timeout, not one per name."""
    if host not in _REACHABLE:
        request = urllib.request.Request(f"https://{host}/", method="HEAD",
                                         headers={"User-Agent": USER_AGENT})
        try:
            urllib.request.urlopen(request, timeout=timeout).close()
            _REACHABLE[host] = True
        except urllib.error.HTTPError:
            _REACHABLE[host] = True
        except (urllib.error.URLError, OSError, ValueError):
            _REACHABLE[host] = False
    return _REACHABLE[host]
