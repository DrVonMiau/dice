"""Finding and launching emulators.

Each platform has a launch *command template* (e.g. "flatpak run
org.ppsspp.PPSSPP {rom}"). The user can set it in Preferences; when they
haven't, Dice auto-detects the first emulator from the platform registry that
is installed on the host.

Inside the Flatpak sandbox the emulators live on the host, so commands run
through `flatpak-spawn --host` (the manifest grants org.freedesktop.Flatpak).
"""
import os
import shlex
import shutil
import subprocess

from . import platforms

IN_FLATPAK = os.path.exists("/.flatpak-info")


def host_argv(argv):
    return ["flatpak-spawn", "--host"] + argv if IN_FLATPAK else argv


def _run_ok(argv, timeout=4):
    try:
        return subprocess.run(host_argv(argv), stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def is_installed(emulator):
    if emulator.kind == "flatpak":
        return _run_ok(["flatpak", "info", emulator.ref])
    if not IN_FLATPAK:
        return shutil.which(emulator.ref) is not None
    return _run_ok(["sh", "-c", f"command -v {shlex.quote(emulator.ref)}"])


def detect(platform_key):
    """The first installed emulator for a platform, or None. Blocking (it may
    spawn host processes), so call it off the main thread."""
    platform = platforms.get(platform_key)
    if platform is None:
        return None
    for emulator in platform.emulators:
        if is_installed(emulator):
            return emulator
    return None


def build_argv(template, rom_path):
    """Split a command template and substitute the ROM path. {rom} stands for
    the path as one argument; if the template has no {rom}, the path is
    appended."""
    parts = shlex.split(template)
    if not parts:
        return None
    if any("{rom}" in part for part in parts):
        return [part.replace("{rom}", rom_path) for part in parts]
    return parts + [rom_path]


# ------------------------------------------------ Flatpak emulator access --
#
# An emulator installed as a Flatpak has its own sandbox: it can only open
# ROMs under folders its permissions cover. Its own file dialog gets around
# that through the portal, but a path handed over on the command line
# doesn't, so the emulator reports the file as missing. Dice checks the
# emulator's filesystem permissions before launching and, with the user's
# consent, adds the ROM folder to them (`flatpak override --user`).

_XDG_DEFAULTS = {
    "xdg-desktop": "Desktop", "xdg-documents": "Documents", "xdg-download": "Downloads",
    "xdg-music": "Music", "xdg-pictures": "Pictures", "xdg-public-share": "Public",
    "xdg-templates": "Templates", "xdg-videos": "Videos",
}


def flatpak_app(argv):
    """The app id in a `flatpak run [options] APP …` command, else None."""
    if len(argv) < 3 or os.path.basename(argv[0]) != "flatpak" or argv[1] != "run":
        return None
    for arg in argv[2:]:
        if not arg.startswith("-"):
            return arg
    return None


def _host_output(argv, timeout=5):
    try:
        result = subprocess.run(host_argv(argv), capture_output=True, text=True,
                                timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def flatpak_filesystems(app_id):
    """The app's effective filesystem permissions (overrides included), or
    None if they can't be read."""
    out = _host_output(["flatpak", "info", "--show-permissions", app_id])
    if out is None:
        return None
    for line in out.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "filesystems":
            return [v for v in value.strip().split(";") if v]
    return []


def _xdg_dir(name, home):
    base, _, sub = name.partition("/")
    folder = _XDG_DEFAULTS.get(base)
    if folder is None:
        return None
    try:
        from gi.repository import GLib
        special = {
            "xdg-desktop": GLib.UserDirectory.DIRECTORY_DESKTOP,
            "xdg-documents": GLib.UserDirectory.DIRECTORY_DOCUMENTS,
            "xdg-download": GLib.UserDirectory.DIRECTORY_DOWNLOAD,
            "xdg-music": GLib.UserDirectory.DIRECTORY_MUSIC,
            "xdg-pictures": GLib.UserDirectory.DIRECTORY_PICTURES,
            "xdg-public-share": GLib.UserDirectory.DIRECTORY_PUBLIC_SHARE,
            "xdg-templates": GLib.UserDirectory.DIRECTORY_TEMPLATES,
            "xdg-videos": GLib.UserDirectory.DIRECTORY_VIDEOS,
        }[base]
        path = GLib.get_user_special_dir(special)
    except Exception:
        path = None
    path = path or os.path.join(home, folder)
    return os.path.join(path, sub) if sub else path


def _grant_root(entry, home):
    """The folder a filesystems= entry opens up, or None ('host' → '/')."""
    if entry.startswith("!"):
        return None
    name = entry.split(":")[0].rstrip("/")
    if name in ("host", "host-os"):
        return "/" if name == "host" else None
    if name == "home" or name == "~":
        return home
    if name.startswith("~/"):
        return os.path.join(home, name[2:])
    if name.startswith("xdg-"):
        return _xdg_dir(name, home)
    if name.startswith("/"):
        return name
    return None


def can_access(filesystems, path, home=None):
    """Whether a sandbox with these filesystem permissions can see `path`."""
    home = home or os.path.expanduser("~")
    path = os.path.normpath(path)
    for entry in filesystems:
        root = _grant_root(entry, home)
        if root is None:
            continue
        root = os.path.normpath(root)
        if root == "/" or path == root or path.startswith(root + "/"):
            return True
    return False


def grant_access(app_id, folder):
    """Let a Flatpak app read and write `folder` (emulators often keep saves
    next to the game). Reversible with `flatpak override --user --reset APP`."""
    return _host_output(["flatpak", "override", "--user",
                         f"--filesystem={folder}", app_id]) is not None


def with_file_forwarding(argv, rom_path):
    """`flatpak run --file-forwarding APP … @@ ROM @@`: Flatpak shares just
    that file with the app through the document portal. The fallback when
    Dice couldn't resolve a real path; sibling files (saves, .bin tracks)
    aren't shared this way."""
    app_index = argv.index(flatpak_app(argv))
    out = argv[:app_index] + ["--file-forwarding"]
    for part in argv[app_index:]:
        out += ["@@", part, "@@"] if part == rom_path else [part]
    return out
