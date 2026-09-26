"""The platform registry — the one place a new system is added.

Each Platform says which file extensions belong to it, which folder names hint
at it (for ambiguous formats like .iso or .chd), what libretro calls it (for
cover-art lookups) and which emulators can run it. The window builds its tabs
from this list, so adding an entry here is all a new platform needs — plus, if
its files can't be told apart by extension alone, a sniffer in romscan.py.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Emulator:
    """One way to launch an emulator. `kind` is "flatpak" (an installed Flatpak
    app id) or "bin" (an executable on PATH); `command` is the launch template,
    where {rom} is replaced by the game's path (as a single argument)."""
    name: str
    kind: str
    ref: str
    command: str


@dataclass(frozen=True)
class Platform:
    key: str
    label: str            # short tab label, e.g. "PS2"
    name: str             # full name, e.g. "PlayStation 2"
    extensions: tuple     # extensions that are unambiguously this platform
    disc_extensions: tuple = ()  # ambiguous disc images, resolved by sniffing
    folder_hints: tuple = ()     # lower-case folder names that imply it
    libretro_system: str = ""    # libretro-thumbnails system name
    emulators: tuple = field(default_factory=tuple)


PLATFORMS = (
    Platform(
        key="gba",
        label="GBA",
        name="Game Boy Advance",
        extensions=(".gba", ".agb"),
        folder_hints=("gba", "gameboy advance", "game boy advance",
                      "gameboyadvance", "gameboy-advance"),
        libretro_system="Nintendo - Game Boy Advance",
        emulators=(
            Emulator("mGBA", "flatpak", "io.mgba.mGBA", "flatpak run io.mgba.mGBA {rom}"),
            Emulator("mGBA", "bin", "mgba-qt", "mgba-qt {rom}"),
            Emulator("mGBA", "bin", "mgba", "mgba {rom}"),
        ),
    ),
    Platform(
        key="nds",
        label="NDS",
        name="Nintendo DS",
        extensions=(".nds", ".dsi"),
        folder_hints=("nds", "ds", "nintendo ds", "nintendods"),
        libretro_system="Nintendo - Nintendo DS",
        emulators=(
            Emulator("melonDS", "flatpak", "net.kuribo64.melonDS", "flatpak run net.kuribo64.melonDS {rom}"),
            Emulator("melonDS", "bin", "melonDS", "melonDS {rom}"),
            Emulator("DeSmuME", "flatpak", "org.desmume.DeSmuME", "flatpak run org.desmume.DeSmuME {rom}"),
            Emulator("DeSmuME", "bin", "desmume", "desmume {rom}"),
        ),
    ),
    Platform(
        key="3ds",
        label="3DS",
        name="Nintendo 3DS",
        # .cia is an installer, not a bootable image, so it isn't listed.
        extensions=(".3ds", ".cci", ".cxi", ".3dsx"),
        folder_hints=("3ds", "n3ds", "nintendo 3ds", "nintendo3ds"),
        libretro_system="Nintendo - Nintendo 3DS",
        emulators=(
            Emulator("Azahar", "flatpak", "org.azahar_emu.Azahar", "flatpak run org.azahar_emu.Azahar {rom}"),
            Emulator("Azahar", "bin", "azahar", "azahar {rom}"),
            Emulator("Lime3DS", "flatpak", "io.github.lime3ds.Lime3DS", "flatpak run io.github.lime3ds.Lime3DS {rom}"),
            Emulator("Citra", "bin", "citra-qt", "citra-qt {rom}"),
        ),
    ),
    Platform(
        key="ps1",
        label="PS1",
        name="PlayStation",
        extensions=(),
        disc_extensions=(".cue", ".chd", ".iso"),
        folder_hints=("ps1", "psx", "psone", "ps one", "playstation", "playstation 1",
                      "playstation1"),
        libretro_system="Sony - PlayStation",
        emulators=(
            Emulator("DuckStation", "flatpak", "org.duckstation.DuckStation",
                     "flatpak run org.duckstation.DuckStation {rom}"),
            Emulator("DuckStation", "bin", "duckstation-qt", "duckstation-qt {rom}"),
            Emulator("DuckStation", "bin", "duckstation", "duckstation {rom}"),
            Emulator("Mednafen", "bin", "mednafen", "mednafen {rom}"),
        ),
    ),
    Platform(
        key="psp",
        label="PSP",
        name="PlayStation Portable",
        extensions=(".cso", ".pbp"),
        disc_extensions=(".iso", ".chd"),
        folder_hints=("psp", "playstation portable"),
        libretro_system="Sony - PlayStation Portable",
        emulators=(
            Emulator("PPSSPP", "flatpak", "org.ppsspp.PPSSPP", "flatpak run org.ppsspp.PPSSPP {rom}"),
            Emulator("PPSSPP", "bin", "PPSSPPSDL", "PPSSPPSDL {rom}"),
            Emulator("PPSSPP", "bin", "ppsspp", "ppsspp {rom}"),
        ),
    ),
    Platform(
        key="ps2",
        label="PS2",
        name="PlayStation 2",
        extensions=(),
        disc_extensions=(".iso", ".chd", ".cue"),
        folder_hints=("ps2", "playstation 2", "playstation2", "pcsx2"),
        libretro_system="Sony - PlayStation 2",
        emulators=(
            Emulator("PCSX2", "flatpak", "net.pcsx2.PCSX2", "flatpak run net.pcsx2.PCSX2 {rom}"),
            Emulator("PCSX2", "bin", "pcsx2-qt", "pcsx2-qt {rom}"),
            Emulator("PCSX2", "bin", "pcsx2", "pcsx2 {rom}"),
        ),
    ),
)

BY_KEY = {p.key: p for p in PLATFORMS}

# Every extension the scanner looks at, plain and disc. .zip is handled
# specially: it counts only when it holds a cartridge ROM (see romscan).
ALL_EXTENSIONS = frozenset(
    {ext for p in PLATFORMS for ext in p.extensions + p.disc_extensions} | {".zip"})


def get(key):
    return BY_KEY.get(key)


def label(key):
    platform = BY_KEY.get(key)
    return platform.label if platform else key.upper()


def by_extension(ext):
    """Platforms that own `ext` outright (no sniffing needed)."""
    return [p for p in PLATFORMS if ext in p.extensions]


def disc_candidates(ext):
    """Platforms whose disc images may use `ext`."""
    return [p for p in PLATFORMS if ext in p.disc_extensions]


def from_folder_hint(path_parts):
    """The platform a folder name points at, checking the innermost folder
    first (so ".../ROMs/PS2/Action/game.iso" resolves to PS2)."""
    for part in reversed(path_parts):
        name = part.lower().strip()
        for platform in PLATFORMS:
            if name in platform.folder_hints:
                return platform
    return None
