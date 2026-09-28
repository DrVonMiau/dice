"""Translate document-portal paths back to real host paths.

When a folder is picked through the file-chooser portal and Dice only has
read-only (or no) access to it, the portal hands back a path like
  /run/user/1000/doc/83265121/Roms
which exists only inside Dice's sandbox. An emulator on the host — or in its
own Flatpak — can't open that path. The Documents portal can tell us the real
location (GetHostPaths, portal version 5+), which is what Dice stores and what
it hands to emulators.
"""
import re

_DOC_RE = re.compile(r"^/run/(?:user/\d+|flatpak)/doc/([^/]+)/[^/]+(/.*)?$")


def split_doc_path(path):
    """(doc_id, remainder under the exported item) or None."""
    match = _DOC_RE.match(path or "")
    if not match:
        return None
    return match.group(1), match.group(2) or ""


def _lookup_host_paths(doc_ids):
    """{doc_id: host path} from the Documents portal ({} if unavailable)."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.freedesktop.portal.Documents", "/org/freedesktop/portal/documents",
            "org.freedesktop.portal.Documents", "GetHostPaths",
            GLib.Variant("(as)", (list(doc_ids),)), GLib.VariantType("(a{say})"),
            Gio.DBusCallFlags.NONE, 3000, None)
    except Exception:
        return {}
    out = {}
    for doc_id, raw in reply.unpack()[0].items():
        data = bytes(raw).rstrip(b"\x00")
        if data:
            out[doc_id] = data.decode("utf-8", "surrogateescape")
    return out


def host_path(path, lookup=_lookup_host_paths):
    """The real host path for `path`; `path` unchanged if it isn't a portal
    path or the portal can't resolve it."""
    parts = split_doc_path(path)
    if parts is None:
        return path
    doc_id, rest = parts
    host = lookup([doc_id]).get(doc_id)
    return host + rest if host else path
