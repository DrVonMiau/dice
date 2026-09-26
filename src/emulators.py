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
