"""Local game library: SQLite storage + folder scanner.

The shape follows Lyre and Easel: the user adds folders, the scanner walks them
and keeps one row per ROM file, and anything the user did (favourites, a
hand-picked cover, play history) survives rescans because rows are updated in
place rather than replaced. Dice only ever *reads* the ROM folders.
"""
import hashlib
import os
import sqlite3
import time
from pathlib import Path

from . import platforms, romscan

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "dice"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "dice"
# Covers Dice owns: art the user picked, art pulled out of a disc image and
# art downloaded online. Kept under data (not cache) so a picked cover is
# never silently lost when caches are cleared.
COVERS_DIR = DATA_DIR / "covers"
DB_PATH = DATA_DIR / "library.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS folders(id INTEGER PRIMARY KEY, path TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS games(
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE,
  platform TEXT NOT NULL,
  title TEXT NOT NULL,
  serial TEXT DEFAULT '',
  internal_title TEXT DEFAULT '',
  region TEXT DEFAULT '',
  format TEXT DEFAULT '',
  size INTEGER DEFAULT 0,
  mtime REAL DEFAULT 0,
  added_at REAL DEFAULT 0,
  last_played REAL DEFAULT 0,
  play_count INTEGER DEFAULT 0,
  play_seconds INTEGER DEFAULT 0,
  favorite INTEGER DEFAULT 0,
  -- cover_source: 'user' (picked in Dice), 'sidecar' (an image next to the
  -- ROM), 'embedded' (pulled from the disc), 'online' (downloaded) or ''.
  cover_path TEXT DEFAULT '',
  cover_source TEXT DEFAULT '',
  cover_checked INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_games_platform ON games(platform);
"""

COVER_EXT = (".png", ".jpg", ".jpeg", ".webp")
# Folders (relative to the ROM's own folder) where front-end tools commonly
# keep box art named after the ROM: Dice picks those up for free.
COVER_DIRS = ("", "covers", "Covers", "boxart", "Boxart", "images",
              "media/covers", "media/box2dfront", "Named_Boxarts")


# Bump when detection changes, so a rescan re-identifies files it would
# otherwise skip as unchanged (e.g. PS1 discs once filed as PS2).
SCAN_VERSION = 4


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        con.execute("ALTER TABLE games ADD COLUMN scan_version INTEGER DEFAULT 0")
        con.commit()
    except sqlite3.OperationalError:
        pass
    # 1 when the user picked the platform by hand: rescans then leave it be.
    try:
        con.execute("ALTER TABLE games ADD COLUMN platform_locked INTEGER DEFAULT 0")
        con.commit()
    except sqlite3.OperationalError:
        pass
    return con


# ------------------------------------------------------------- folders ----

def add_folder(con, path):
    con.execute("INSERT OR IGNORE INTO folders(path) VALUES (?)", (path,))
    con.commit()


def all_folders(con):
    return [r["path"] for r in con.execute("SELECT path FROM folders ORDER BY path")]


def remove_folder(con, path):
    """Forget a folder and every game scanned from it. Files stay on disk."""
    con.execute("DELETE FROM folders WHERE path=?", (path,))
    con.execute("DELETE FROM games WHERE path LIKE ?", (path.rstrip("/") + "/%",))
    con.commit()
    _prune_owned_covers(con)


def rebase_folder(con, old, new):
    """Move a folder and its games to a new path prefix, keeping favourites,
    play history and covers (used when a portal path is resolved)."""
    old_p, new_p = old.rstrip("/"), new.rstrip("/")
    if con.execute("SELECT 1 FROM folders WHERE path=?", (new_p,)).fetchone():
        con.execute("DELETE FROM folders WHERE path=?", (old,))
    else:
        con.execute("UPDATE folders SET path=? WHERE path=?", (new_p, old))
    con.execute("UPDATE games SET cover_path = ? || substr(cover_path, ?) "
                "WHERE cover_path LIKE ?", (new_p, len(old_p) + 1, old_p + "/%"))
    con.execute("UPDATE OR IGNORE games SET path = ? || substr(path, ?) WHERE path LIKE ?",
                (new_p, len(old_p) + 1, old_p + "/%"))
    con.execute("DELETE FROM games WHERE path LIKE ?", (old_p + "/%",))
    con.commit()


def wipe_library(con):
    con.execute("DELETE FROM folders")
    con.execute("DELETE FROM games")
    con.commit()
    _prune_owned_covers(con)


# --------------------------------------------------------------- games ----

def all_games(con):
    return con.execute("SELECT * FROM games ORDER BY title COLLATE NOCASE").fetchall()


def get_game(con, game_id):
    return con.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()


def set_platform(con, game_id, platform):
    """Pin a game to a platform chosen by the user (None: back to detection)."""
    if platform is None:
        con.execute("UPDATE games SET platform_locked=0, scan_version=0 WHERE id=?",
                    (game_id,))
    else:
        con.execute("UPDATE games SET platform=?, platform_locked=1 WHERE id=?",
                    (platform, game_id))
    con.commit()


def set_favorite(con, game_id, favorite):
    con.execute("UPDATE games SET favorite=? WHERE id=?", (1 if favorite else 0, game_id))
    con.commit()


def record_launch(con, game_id):
    con.execute("UPDATE games SET last_played=?, play_count=play_count+1 WHERE id=?",
                (time.time(), game_id))
    con.commit()


def add_play_time(con, game_id, seconds):
    con.execute("UPDATE games SET play_seconds=play_seconds+? WHERE id=?",
                (int(seconds), game_id))
    con.commit()


def _owned_cover_file(game_path, kind, ext=".png"):
    digest = hashlib.sha1(game_path.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return COVERS_DIR / f"{digest}-{kind}{ext}"


def set_user_cover(con, game_id, image_path):
    """Copy a picked image into Dice's covers folder and make it the cover.
    A picked cover always wins over sidecar, embedded or downloaded art."""
    row = get_game(con, game_id)
    if row is None:
        return None
    ext = os.path.splitext(image_path)[1].lower() or ".png"
    dest = _owned_cover_file(row["path"], f"user-{int(time.time())}", ext)
    with open(image_path, "rb") as src, open(dest, "wb") as out:
        out.write(src.read())
    old = row["cover_path"] if row["cover_source"] == "user" else ""
    con.execute("UPDATE games SET cover_path=?, cover_source='user' WHERE id=?",
                (str(dest), game_id))
    con.commit()
    if old and old.startswith(str(COVERS_DIR)):
        try:
            os.remove(old)
        except OSError:
            pass
    return str(dest)


def clear_user_cover(con, game_id):
    """Drop a picked cover and fall straight back to whatever art the game
    has on its own (sidecar, embedded, or a fresh online lookup)."""
    row = get_game(con, game_id)
    if row is None or row["cover_source"] != "user":
        return
    con.execute("UPDATE games SET cover_path='', cover_source='', cover_checked=0 "
                "WHERE id=?", (game_id,))
    con.commit()
    try:
        os.remove(row["cover_path"])
    except OSError:
        pass
    refresh_game(con, game_id)


def _folder_parts(con, path):
    for root in all_folders(con):
        prefix = root.rstrip("/") + "/"
        if path.startswith(prefix):
            rel = Path(os.path.dirname(path[len(prefix):]))
            return (os.path.basename(root.rstrip("/")),) + tuple(
                x for x in rel.parts if x not in (".", ""))
    return tuple(Path(path).parent.parts[-2:])


def refresh_game(con, game_id):
    """Re-read one game's file (metadata and found art)."""
    row = get_game(con, game_id)
    if row is None:
        return
    info = romscan.identify(row["path"], _folder_parts(con, row["path"]))
    if info is None:
        return
    try:
        mtime = os.path.getmtime(row["path"])
    except OSError:
        return
    _store(con, row["path"], mtime, info, row)
    con.commit()


def set_online_cover(con, game_id, data):
    row = get_game(con, game_id)
    if row is None or row["cover_source"] in ("user", "sidecar"):
        return None
    dest = _owned_cover_file(row["path"], "online")
    with open(dest, "wb") as out:
        out.write(data)
    con.execute("UPDATE games SET cover_path=?, cover_source='online', cover_checked=1 "
                "WHERE id=?", (str(dest), game_id))
    con.commit()
    return str(dest)


def mark_cover_checked(con, game_id):
    con.execute("UPDATE games SET cover_checked=1 WHERE id=?", (game_id,))
    con.commit()


def games_needing_covers(con, retry=False):
    """Games without art that haven't been looked up yet. retry=True (the
    menu's Find Missing Covers) also retries earlier misses, and includes
    games showing only art pulled from the disc (a PSP icon), since real box
    art is better."""
    if retry:
        return con.execute(
            "SELECT * FROM games WHERE cover_path='' OR cover_source='embedded' "
            "ORDER BY title COLLATE NOCASE").fetchall()
    return con.execute(
        "SELECT * FROM games WHERE cover_path='' AND cover_checked=0").fetchall()


def _prune_owned_covers(con):
    """Delete cover files in COVERS_DIR that no game references any more."""
    used = {r["cover_path"] for r in con.execute("SELECT cover_path FROM games")}
    try:
        for entry in COVERS_DIR.iterdir():
            if str(entry) not in used:
                entry.unlink(missing_ok=True)
    except OSError:
        pass


# ------------------------------------------------------------ multi-disc ----

def group_discs(rows):
    """One card per multi-disc game. Returns (rows to show, {shown id:
    [(disc number, row id)]}).

    * An .m3u playlist is the game; the discs it lists are hidden (the
      emulator swaps discs from the playlist).
    * Otherwise files named "(Disc 1)", "(Disc 2)"… with the same platform
      and title in the same folder become one card, shown through its
      first disc; the side panel can start any disc."""
    hidden, discs = set(), {}
    by_path = {r["path"]: r for r in rows}
    for r in rows:
        if (r["format"] or "") == "M3U":
            member_rows = [by_path[m] for m in romscan.m3u_members(r["path"]) if m in by_path]
            hidden.update(x["id"] for x in member_rows)
            discs[r["id"]] = [(n, x["id"]) for n, x in enumerate(member_rows, 1)]
    groups = {}
    for r in rows:
        if r["id"] in hidden or (r["format"] or "") == "M3U":
            continue
        number = romscan.disc_number(os.path.basename(r["path"]))
        if number is not None:
            key = (r["platform"], r["title"].lower(), os.path.dirname(r["path"]))
            groups.setdefault(key, []).append((number, r))
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: m[0])
        first = members[0][1]
        discs[first["id"]] = [(n, r["id"]) for n, r in members]
        hidden.update(r["id"] for _n, r in members[1:])
    return [r for r in rows if r["id"] not in hidden], discs


# ------------------------------------------------------------ duplicates ----

def find_duplicates(rows):
    """Games that look like the same game more than once, per platform:
    titles that match once spelling, articles and '&'/'and' are set aside
    (see romscan.title_key). Returns [(rows, identical)] sorted by title,
    where `identical` means every copy has the same size and serial — the
    same dump twice — rather than, say, a USA and a Europe release.
    Pass the rows shown in the library (after group_discs), so the discs of
    one game don't count as duplicates of each other."""
    groups = {}
    for r in rows:
        key = (r["platform"], romscan.title_key(r["title"]))
        if key[1]:
            groups.setdefault(key, []).append(r)
    out = []
    for key in sorted(groups, key=lambda k: (k[1], k[0])):
        members = groups[key]
        if len(members) < 2:
            continue
        fingerprints = {(m["size"], m["serial"] or "") for m in members}
        out.append((sorted(members, key=lambda m: m["path"]), len(fingerprints) == 1))
    return out


# -------------------------------------------------------------- scanning ----

def find_sidecar_cover(rom_path):
    """An image named like the ROM, beside it or in a covers-style folder."""
    p = Path(rom_path)
    stems = [p.stem]
    if p.suffix.lower() == ".pbp":
        stems = [p.parent.name, "ICON0"]
    for sub in COVER_DIRS:
        base = p.parent / sub if sub else p.parent
        for stem in stems:
            for ext in COVER_EXT:
                candidate = base / f"{stem}{ext}"
                if candidate.is_file():
                    return str(candidate)
    return ""


def _iter_rom_files(root):
    """(path, folder parts) for candidate files under `root`, skipping hidden
    entries and the .bin tracks that belong to a .cue sheet."""
    root_name = os.path.basename(root.rstrip("/"))
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel = os.path.relpath(dirpath, root)
        parts = (root_name,) + (() if rel == "." else tuple(Path(rel).parts))
        for name in filenames:
            if name.startswith("."):
                continue
            if os.path.splitext(name)[1].lower() in platforms.ALL_EXTENSIONS:
                yield os.path.join(dirpath, name), parts


def scan_folder(con, root, progress=None):
    """Index every ROM under `root`. Returns the number of games found."""
    return _scan(con, [root], progress)


def scan_all(con, progress=None):
    return _scan(con, [r for r in all_folders(con) if os.path.isdir(r)], progress)


def _identity(platform, size, serial, fmt):
    """What makes a game recognisable after a rename or move: its platform,
    size and serial (or format, for ROMs without a serial)."""
    return (platform, size, serial) if serial else (platform, size, "", fmt)


def _scan(con, roots, progress=None):
    """Update changed files, add new ones and drop rows whose files are gone,
    across `roots` at once. A file that vanished and a new file with the same
    identity (see _identity) are the same game renamed or moved: its row
    follows it, keeping favourites, play history and a picked cover."""
    files = [f for root in roots for f in _iter_rom_files(root)]
    total = len(files)
    known = {}
    for root in roots:
        for r in con.execute("SELECT * FROM games WHERE path LIKE ?",
                             (root.rstrip("/") + "/%",)):
            known[r["path"]] = r
    seen = set()
    new = []            # (path, mtime, info) not in the library yet
    found = 0
    for i, (path, parts) in enumerate(files, start=1):
        if progress:
            progress(i, total)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        existing = known.get(path)
        if (existing is not None and existing["mtime"] == mtime
                and existing["scan_version"] == SCAN_VERSION):
            seen.add(path)
            found += 1
            # Art dropped next to an unchanged ROM still gets picked up.
            if existing["cover_source"] not in ("user", "sidecar"):
                cover = find_sidecar_cover(path)
                if cover:
                    con.execute("UPDATE games SET cover_path=?, cover_source='sidecar' "
                                "WHERE id=?", (cover, existing["id"]))
            continue
        info = romscan.identify(path, parts)
        if info is None:
            continue
        seen.add(path)
        found += 1
        if existing is None:
            new.append((path, mtime, info))
        else:
            _store(con, path, mtime, info, existing)

    gone = {p: r for p, r in known.items() if p not in seen}
    # Pair vanished rows with new files, but only when the identity is
    # unambiguous on both sides (two copies of one game stay separate).
    gone_by_id, new_by_id = {}, {}
    for row in gone.values():
        key = _identity(row["platform"], row["size"], row["serial"], row["format"])
        gone_by_id.setdefault(key, []).append(row)
    for item in new:
        info = item[2]
        key = _identity(info.platform, info.size, info.serial, info.format)
        new_by_id.setdefault(key, []).append(item)
    for key, items in new_by_id.items():
        rows = gone_by_id.get(key, [])
        if len(items) == 1 and len(rows) == 1:
            path, mtime, info = items[0]
            _store(con, path, mtime, info, rows[0])
            del gone[rows[0]["path"]]
        else:
            for path, mtime, info in items:
                _store(con, path, mtime, info, None)
    for path in gone:
        con.execute("DELETE FROM games WHERE path=?", (path,))
    con.commit()
    if gone:
        _prune_owned_covers(con)
    return found


def _store(con, path, mtime, info, existing):
    cover, source = "", ""
    keep_user = existing is not None and existing["cover_source"] == "user"
    if not keep_user:
        cover = find_sidecar_cover(path)
        source = "sidecar" if cover else ""
        if not cover and info.icon:
            dest = _owned_cover_file(path, "embedded")
            try:
                with open(dest, "wb") as out:
                    out.write(info.icon)
                cover, source = str(dest), "embedded"
            except OSError:
                pass
    values = dict(path=path, platform=info.platform, title=info.title, serial=info.serial,
                  internal_title=info.internal_title, region=info.region,
                  format=info.format, size=info.size, mtime=mtime,
                  scan_version=SCAN_VERSION)
    if existing is None:
        con.execute(
            "INSERT INTO games(path, platform, title, serial, internal_title, region, "
            "format, size, mtime, added_at, cover_path, cover_source, scan_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (path, info.platform, info.title, info.serial, info.internal_title,
             info.region, info.format, info.size, mtime, time.time(), cover, source,
             SCAN_VERSION))
        return
    if "platform_locked" in existing.keys() and existing["platform_locked"]:
        del values["platform"]
    sets = ", ".join(f"{k}=?" for k in values)
    params = list(values.values())
    if not keep_user:
        sets += ", cover_path=?, cover_source=?"
        params += [cover, source]
        # An online cover is kept unless better local art turned up.
        if existing["cover_source"] == "online" and not cover:
            sets = sets.replace(", cover_path=?, cover_source=?", "")
            params = params[:-2]
    con.execute(f"UPDATE games SET {sets} WHERE id=?", params + [existing["id"]])


