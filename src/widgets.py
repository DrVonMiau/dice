"""Reusable widgets: the box-art Cover, a GridView that reports its width, and
the bounded thumbnail loader behind them.

The loader and the self-drawn texture approach come from Easel, where they
were hardened against the GNOME runtime: images are decoded with
Gdk.Texture.new_from_filename on the main thread (gdk-pixbuf's glycin
subprocess fails in the sandbox), a few per idle cycle, into a bounded LRU
cache and a small on-disk PNG cache.
"""
import hashlib
import math
import os
import sys
import time
from collections import OrderedDict

from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk, Pango

STRIPE_STEP = 7
STRIPE_WIDTH = 2.4
# Box art is portrait: covers are drawn 3:4 (width:height).
COVER_RATIO = 4 / 3

_LOAD_FAIL_LOGGED = 0


def _log_load_failure(path, exc):
    global _LOAD_FAIL_LOGGED
    if _LOAD_FAIL_LOGGED >= 8:
        return
    _LOAD_FAIL_LOGGED += 1
    print(f"dice: could not load image {path!r}: {type(exc).__name__}: {exc}",
          file=sys.stderr)


# ------------------------------------------------------ thumbnail loader ----

_THUMB_CACHE = OrderedDict()
_THUMB_CACHE_MAX = 600
_THUMB_MAX_DIM = 640
_LOAD_BUDGET = 0.010
_FAST_BUDGET = 0.024
_load_stack = []
_load_idle_id = 0


def _thumb_dim(size):
    want = min(max(int(size), 1) * 2, _THUMB_MAX_DIM)
    step = 64
    return min(_THUMB_MAX_DIM, ((want + step - 1) // step) * step)


def _thumb_cache_file(path, dim, mtime):
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    folder = os.path.join(base, "dice", "thumbs")
    os.makedirs(folder, exist_ok=True)
    digest = hashlib.sha1(f"{path}|{dim}|{mtime}".encode("utf-8", "surrogatepass")).hexdigest()
    return os.path.join(folder, digest + ".png")


def _scale_texture(texture, max_dim):
    tw, th = texture.get_width(), texture.get_height()
    if tw <= 0 or th <= 0:
        return None
    scale = min(max_dim / tw, max_dim / th, 1.0)
    if scale >= 1.0:
        return texture
    sw, sh = max(1, round(tw * scale)), max(1, round(th * scale))
    snapshot = Gtk.Snapshot()
    snapshot.append_scaled_texture(texture, Gsk.ScalingFilter.TRILINEAR,
                                   Graphene.Rect().init(0, 0, sw, sh))
    node = snapshot.to_node()
    if node is None:
        return None
    renderer = Gsk.CairoRenderer()
    try:
        renderer.realize(None)
        return renderer.render_texture(node, Graphene.Rect().init(0, 0, sw, sh))
    except Exception:
        return None
    finally:
        if renderer.is_realized():
            renderer.unrealize()


def _load_scaled(path, dim):
    """(texture, expensive) — from the baked PNG cache when possible."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None, False
    try:
        cache_file = _thumb_cache_file(path, dim, mtime)
    except OSError:
        cache_file = None
    if cache_file and os.path.exists(cache_file):
        try:
            return Gdk.Texture.new_from_filename(cache_file), False
        except Exception as exc:
            _log_load_failure(path, exc)
    try:
        src = Gdk.Texture.new_from_filename(path)
    except Exception as exc:
        _log_load_failure(path, exc)
        return None, True
    scaled = _scale_texture(src, dim)
    if scaled is not None and scaled is not src and cache_file:
        try:
            data = scaled.save_to_png_bytes()
            tmp = f"{cache_file}.{os.getpid()}.tmp"
            with open(tmp, "wb") as fh:
                fh.write(data.get_data())
            os.replace(tmp, cache_file)
        except Exception:
            pass
    return scaled, True


def _process_load_stack():
    global _load_idle_id
    start = time.monotonic()
    while _load_stack:
        path, dim, key, wants, callback = _load_stack.pop()   # LIFO: newest first
        if wants is not None and not wants():
            continue
        texture = _THUMB_CACHE.get(key)
        expensive = False
        if texture is None:
            texture, expensive = _load_scaled(path, dim)
            if texture is not None:
                _THUMB_CACHE[key] = texture
                while len(_THUMB_CACHE) > _THUMB_CACHE_MAX:
                    _THUMB_CACHE.popitem(last=False)
        callback(path, texture)
        elapsed = time.monotonic() - start
        if elapsed >= _FAST_BUDGET or (expensive and elapsed >= _LOAD_BUDGET):
            break
    if _load_stack:
        return True
    _load_idle_id = 0
    return False


def request_thumbnail(path, size, wants, callback):
    """A cached texture now, or None and callback(path, texture) later."""
    global _load_idle_id
    if not path:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    dim = _thumb_dim(size)
    key = (path, dim, mtime)
    cached = _THUMB_CACHE.get(key)
    if cached is not None:
        _THUMB_CACHE.move_to_end(key)
        return cached
    _load_stack.append((path, dim, key, wants, callback))
    if not _load_idle_id:
        _load_idle_id = GLib.idle_add(_process_load_stack)
    return None


def forget_thumbnail(path):
    """Drop cached textures for a path (after its cover file was replaced)."""
    for key in [k for k in _THUMB_CACHE if k[0] == path]:
        del _THUMB_CACHE[key]


# ----------------------------------------------------------------- Cover ----

class Cover(Gtk.Widget):
    """A 3:4 box-art cover. Real box art is cover-cropped; art whose shape is
    far from a box (a PSP disc icon, square GBA scans) is shown whole over a
    blurred, dimmed copy of itself so the card keeps its shape. With no art it
    draws the family's striped placeholder with the game title on top.

    The texture is painted in do_snapshot rather than via a Gtk.Picture child,
    which doesn't paint inside a custom widget in the GNOME runtime (Easel)."""

    __gtype_name__ = "DiceCover"

    def __init__(self, width=160):
        super().__init__()
        self._width = width
        self._fill = False
        self._texture = None
        self._req_token = None
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.add_css_class("cover")
        self._label = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER,
                                wrap_mode=Pango.WrapMode.WORD_CHAR,
                                lines=4, ellipsize=Pango.EllipsizeMode.END)
        self._label.add_css_class("cover-caption")
        self._label.set_parent(self)
        self.connect("destroy", self._on_destroy)

    def _on_destroy(self, *_args):
        if self._label.get_parent() is self:
            self._label.unparent()

    def set_fill(self, fill):
        """Fill mode: width may shrink to fit its grid column; the height is
        still fixed from set_size (GridView doesn't do height-for-width)."""
        if fill != self._fill:
            self._fill = fill
            self.queue_resize()

    def set_size(self, width):
        if width != self._width:
            self._width = width
            self.queue_resize()

    def _height(self):
        return round(self._width * COVER_RATIO)

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            self._label.measure(orientation, -1)
            return (0 if self._fill else self._width, self._width, -1, -1)
        self._label.measure(orientation, max(1, self._width - 24))
        return (self._height(), self._height(), -1, -1)

    def do_size_allocate(self, width, height, baseline):
        if not self._label.get_visible():
            return
        label_w = max(1, width - 24)
        _m, nat, _b1, _b2 = self._label.measure(Gtk.Orientation.VERTICAL, label_w)
        nat = min(nat, height)
        transform = Gsk.Transform.new().translate(
            Graphene.Point().init(12, (height - nat) / 2))
        self._label.allocate(label_w, nat, -1, transform)

    def set_game(self, title, cover_path):
        self._label.set_label(title or "")
        token = cover_path or None
        self._req_token = token
        if not cover_path:
            self._set_texture(None)
            return

        def on_ready(_path, texture, want=token):
            if self._req_token == want:
                self._set_texture(texture)
            return False

        cached = request_thumbnail(
            cover_path, max(self._width, self._height()),
            wants=lambda want=token: self._req_token == want, callback=on_ready)
        self._set_texture(cached)

    def _set_texture(self, texture):
        self._texture = texture
        self._label.set_visible(texture is None)
        self.queue_resize()
        self.queue_draw()

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        texture = self._texture
        if texture is not None and texture.get_width() > 0 and texture.get_height() > 0:
            tw, th = texture.get_width(), texture.get_height()
            box_ratio = width / height
            art_ratio = tw / th
            cover = max(width / tw, height / th)
            cw, ch = tw * cover, th * cover
            cover_rect = Graphene.Rect().init((width - cw) / 2, (height - ch) / 2, cw, ch)
            if abs(art_ratio - box_ratio) / box_ratio <= 0.18:
                snapshot.append_texture(texture, cover_rect)
                return
            # Mismatched shape: blurred backdrop, then the whole image.
            snapshot.push_blur(28)
            snapshot.append_texture(texture, cover_rect)
            snapshot.pop()
            dim = Gdk.RGBA()
            dim.parse("rgba(0,0,0,0.28)")
            snapshot.append_color(dim, Graphene.Rect().init(0, 0, width, height))
            fit = min(width / tw, height / th)
            fw, fh = tw * fit, th * fit
            snapshot.append_texture(
                texture, Graphene.Rect().init((width - fw) / 2, (height - fh) / 2, fw, fh))
            return
        rgba = self.get_color()
        snapshot.save()
        snapshot.translate(Graphene.Point().init(width / 2, height / 2))
        snapshot.rotate(45)
        diag = math.hypot(width, height)
        y = -diag
        while y < diag:
            snapshot.append_color(rgba, Graphene.Rect().init(-diag, y, diag * 2, STRIPE_WIDTH))
            y += STRIPE_STEP
        snapshot.restore()
        if self._label.get_visible():
            self.snapshot_child(self._label, snapshot)


class ReportingGridView(Gtk.GridView):
    """A GridView that reports its real allocated width, so the window can
    pick an exact column count and size every cover to its column (see Easel's
    EaselGridView for why GridView's own heuristic isn't enough)."""

    __gtype_name__ = "DiceGridView"
    _width_cb = None

    def set_width_cb(self, cb):
        self._width_cb = cb

    def do_size_allocate(self, width, height, baseline):
        Gtk.GridView.do_size_allocate(self, width, height, baseline)
        if self._width_cb is not None and width > 1:
            self._width_cb(width)
