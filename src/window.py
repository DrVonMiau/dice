"""Dice's main window.

The visual design carries over from Lyre and Easel: a tinted desktop, a
"paper" card holding the library, segmented pill tabs and a custom titlebar.
Here the paper holds a shelf of box-art cards and the slide-in side panel
shows the selected game: its cover, a Play button and the details read from
the ROM (platform, region, serial, size, format, play history, path).

Tabs are built from the platform registry (platforms.py): All first, then one
tab per platform that has games, then Favourites.
"""
import os
import threading
import time
from datetime import datetime

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from . import covers, emulators, platforms, portal
from . import library as lib
from .models import Game
from .widgets import Cover, ReportingGridView, forget_thumbnail  # noqa: F401 (registers DiceGridView)

APP_ID = "io.github.drvonmiau.Dice"

THEME_SCHEMES = {
    "light": Adw.ColorScheme.FORCE_LIGHT,
    "dark": Adw.ColorScheme.FORCE_DARK,
    "system": Adw.ColorScheme.DEFAULT,
}
SORTS = ("title", "platform", "played", "added", "size")

SPACE_S, SPACE_M, SPACE_L = 8, 16, 24
POINTER_CURSOR = Gdk.Cursor.new_from_name("pointer")


def _fmt_size(nbytes):
    if not nbytes:
        return "—"
    size = float(nbytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return "—"


def _fmt_when(ts):
    if not ts:
        return "Never"
    then = datetime.fromtimestamp(ts)
    days = (datetime.now().date() - then.date()).days
    if days == 0:
        return f"Today, {then:%H:%M}"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    return then.strftime("%-d %b %Y")


def _fmt_duration(seconds):
    if seconds < 60:
        return "—" if not seconds else "< 1 min"
    hours, rem = divmod(int(seconds) // 60, 60)
    return f"{hours} h {rem} min" if hours else f"{rem} min"


def _sort_title(title):
    t = title.lower()
    for article in ("the ", "a ", "an "):
        if t.startswith(article):
            return t[len(article):]
    return t


@Gtk.Template(resource_path="/io/github/drvonmiau/Dice/window.ui")
class DiceWindow(Adw.ApplicationWindow):
    __gtype_name__ = "DiceWindow"

    toast_overlay = Gtk.Template.Child()
    titlebar_box = Gtk.Template.Child()
    titlebar_spacer = Gtk.Template.Child()
    wc_start = Gtk.Template.Child()
    wc_end = Gtk.Template.Child()
    cover_scale = Gtk.Template.Child()
    menu_button = Gtk.Template.Child()

    nav_row = Gtk.Template.Child()
    middle_stack = Gtk.Template.Child()
    tabs_box = Gtk.Template.Child()
    search_entry = Gtk.Template.Child()
    sort_button = Gtk.Template.Child()
    search_toggle_btn = Gtk.Template.Child()

    content_row = Gtk.Template.Child()
    paper_stack = Gtk.Template.Child()
    game_grid = Gtk.Template.Child()
    empty_page = Gtk.Template.Child()
    none_page = Gtk.Template.Child()

    info_revealer = Gtk.Template.Child()
    info_panel = Gtk.Template.Child()
    info_preview_slot = Gtk.Template.Child()
    info_close_btn = Gtk.Template.Child()
    info_title = Gtk.Template.Child()
    info_subtitle = Gtk.Template.Child()
    info_play_btn = Gtk.Template.Child()
    info_fav_btn = Gtk.Template.Child()
    info_more_btn = Gtk.Template.Child()
    info_rows_box = Gtk.Template.Child()

    PANEL_WIDTH = 300
    CARD_MARGIN = 8

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.con = lib.connect()
        self.settings = Gio.Settings.new(APP_ID)

        self._games = []            # every Game, as loaded
        self._rows = {}             # game id -> library row (serial etc.)
        self._by_id = {}
        self._tab = "all"
        self._tab_buttons = {}
        self._search = ""
        self._sort = self.settings.get_string("sort")
        if self._sort not in SORTS:
            self._sort = "title"
        self._cover_target = self.settings.get_int("cover-size")
        self._cell = 150
        self._grid_width = 0
        self._selected_id = None
        self._detected = {}         # platform key -> Emulator | None
        self._running = {}          # game id -> launch time
        self._cover_worker = None
        self._monitors = []
        self._watch_debounce = 0

        self._setup_actions()
        self._setup_grid()
        self._setup_info_panel()
        self._setup_theme()

        self.search_toggle_btn.connect("toggled", self._on_toggle_search)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("stop-search",
                                  lambda *_: self.search_toggle_btn.set_active(False))
        self.cover_scale.set_value(self._cover_target)
        self.cover_scale.connect("value-changed", self._on_cover_scale)
        self.connect("close-request", self._on_close_request)
        self.connect("realize", self._on_realize)

        self._restore_state()
        self._migrate_portal_folders()
        self._reload()
        self._setup_watching()
        self._setup_dnd()
        self._setup_titlebar_sides()
        self._apply_pointer_cursors(self)
        self._detect_emulators()
        self._start_cover_worker()

    # ------------------------------------------------------------ chrome --

    @staticmethod
    def _close_button_is_left(layout):
        return "close" in (layout or "").split(":")[0]

    def _setup_titlebar_sides(self):
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.connect("notify::gtk-decoration-layout",
                             lambda *_a: self._apply_titlebar_side())
        self._apply_titlebar_side()

    def _apply_titlebar_side(self):
        """Keep the cover-size + menu group opposite the window controls."""
        settings = Gtk.Settings.get_default()
        layout = settings.get_property("gtk-decoration-layout") if settings else ""
        box = self.titlebar_box
        left = self._close_button_is_left(layout)
        if left:
            box.reorder_child_after(self.titlebar_spacer, self.wc_start)
            previous = self.titlebar_spacer
        else:
            previous = self.wc_start
        for widget in (self.cover_scale, self.menu_button):
            box.reorder_child_after(widget, previous)
            previous = widget
        if not left:
            box.reorder_child_after(self.titlebar_spacer, previous)

    def _apply_pointer_cursors(self, root):
        def walk(widget):
            if isinstance(widget, Gtk.WindowControls):
                return
            if isinstance(widget, (Gtk.Button, Gtk.Scale, Gtk.MenuButton)):
                widget.set_cursor(POINTER_CURSOR)
            child = widget.get_first_child()
            while child:
                walk(child)
                child = child.get_next_sibling()
        walk(root)

    def _setup_theme(self):
        Adw.StyleManager.get_default().connect("notify::dark", self._on_dark_changed)
        self._apply_theme(self.settings.get_string("theme"))

    def _apply_theme(self, theme):
        Adw.StyleManager.get_default().set_color_scheme(
            THEME_SCHEMES.get(theme, Adw.ColorScheme.DEFAULT))
        self._on_dark_changed()

    def _on_dark_changed(self, *_args):
        if Adw.StyleManager.get_default().get_dark():
            self.add_css_class("dark")
        else:
            self.remove_css_class("dark")

    def _restore_state(self):
        self.set_default_size(self.settings.get_int("window-width"),
                              self.settings.get_int("window-height"))
        if self.settings.get_boolean("window-maximized"):
            self.maximize()
        self._tab = self.settings.get_string("last-tab") or "all"

    def _migrate_portal_folders(self):
        """Libraries added before portal paths were resolved: move them to the
        real path when Dice can read it there."""
        for path in lib.all_folders(self.con):
            real = self._real_path(path)
            if real != path:
                lib.rebase_folder(self.con, path, real)

    def _on_close_request(self, *_args):
        self.settings.set_boolean("window-maximized", self.is_maximized())
        if not self.is_maximized():
            width, height = self.get_default_size()
            self.settings.set_int("window-width", width)
            self.settings.set_int("window-height", height)
        self.settings.set_string("last-tab", self._tab)
        return False

    def _on_realize(self, *_args):
        surface = self.get_surface()
        if surface is not None:
            surface.connect("notify::width", lambda *_a: self._apply_layout_metrics())
            surface.connect("notify::height", lambda *_a: self._apply_layout_metrics())
        self._apply_layout_metrics()

    def _apply_layout_metrics(self):
        """5% outer margins like the siblings; the side panel slides in on the
        right and the paper reflows into the remaining width."""
        surface = self.get_surface()
        if surface is None:
            return
        width, height = surface.get_width(), surface.get_height()
        if width <= 0 or height <= 0:
            return
        margin_x = max(SPACE_L, round(width * 0.05))
        revealed = self.info_revealer.get_reveal_child()
        gap = round(width * 0.03) if revealed else 0
        self.content_row.set_margin_start(margin_x)
        self.content_row.set_margin_end(margin_x)
        self.nav_row.set_margin_start(margin_x)
        self.nav_row.set_margin_end(margin_x + (gap + self.PANEL_WIDTH if revealed else 0))
        self.info_panel.set_size_request(self.PANEL_WIDTH if revealed else 0, -1)
        self.info_revealer.set_margin_start(gap)

    # ----------------------------------------------------------- actions --

    def _setup_actions(self):
        simple = (
            ("add-folder", lambda *_a: self._on_add_folder()),
            ("rescan", lambda *_a: self._on_rescan()),
            ("preferences", lambda *_a: self._on_preferences()),
            ("find", lambda *_a: self.search_toggle_btn.set_active(
                not self.search_toggle_btn.get_active())),
            ("play", lambda *_a: self._play(self._selected_id)),
            ("toggle-fav", lambda *_a: self._toggle_fav(self._selected_id)),
            ("set-cover", lambda *_a: self._pick_cover(self._selected_id)),
            ("reset-cover", lambda *_a: self._reset_cover(self._selected_id)),
            ("fetch-cover", lambda *_a: self._fetch_cover_now(self._selected_id)),
            ("show-in-files", lambda *_a: self._show_in_files(self._selected_id)),
            ("copy-path", lambda *_a: self._copy_path(self._selected_id)),
        )
        for name, handler in simple:
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", handler)
            self.add_action(act)

        sort = Gio.SimpleAction.new_stateful(
            "sort", GLib.VariantType.new("s"), GLib.Variant.new_string(self._sort))
        sort.connect("activate", self._on_sort)
        self.add_action(sort)

        for i in range(1, 10):
            act = Gio.SimpleAction.new(f"tab-{i}", None)
            act.connect("activate", lambda *_a, n=i: self._select_tab_index(n - 1))
            self.add_action(act)

        app = self.get_application()
        if app is not None:
            app.set_accels_for_action("win.find", ["<primary>f"])
            app.set_accels_for_action("win.add-folder", ["<primary>o"])
            app.set_accels_for_action("win.rescan", ["<primary>r"])
            app.set_accels_for_action("win.preferences", ["<primary>comma"])
            for i in range(1, 10):
                app.set_accels_for_action(f"win.tab-{i}", [f"<primary>{i}"])

        key_ctl = Gtk.EventControllerKey()
        key_ctl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctl)

    def _on_key_pressed(self, _ctl, keyval, _keycode, _state):
        if keyval == Gdk.KEY_Escape and self.info_revealer.get_reveal_child():
            self._close_info()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self._selected_id:
            if self.search_entry.has_focus() or self.get_focus() is self.search_entry:
                return False
            self._play(self._selected_id)
            return True
        return False

    # -------------------------------------------------------------- grid --

    def _setup_grid(self):
        self.store = Gio.ListStore(item_type=Game)
        self.game_grid.set_model(Gtk.NoSelection(model=self.store))
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda _f, item: item.set_child(self._make_card()))
        factory.connect("bind", lambda _f, item: self._bind_card(item.get_child(),
                                                                 item.get_item()))
        factory.connect("unbind", lambda _f, item: self._unbind_card(item.get_child()))
        self.game_grid.set_factory(factory)
        self.game_grid.set_width_cb(self._on_grid_width)

    def _make_card(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.add_css_class("card-box")
        box.set_margin_start(self.CARD_MARGIN)
        box.set_margin_end(self.CARD_MARGIN)
        box.set_margin_bottom(SPACE_M)

        cover = Cover(self._cell)
        cover.set_fill(True)
        box.cover = cover

        fav = Gtk.Button(icon_name="dice-heart-symbolic", halign=Gtk.Align.END,
                         valign=Gtk.Align.END, margin_end=8, margin_bottom=8,
                         tooltip_text="Favourite", css_classes=["tile-fav"])
        fav.set_cursor(POINTER_CURSOR)
        fav.set_visible(False)
        fav.connect("clicked", lambda _b: self._toggle_fav(box.game_id))
        box.fav = fav

        badge = Gtk.Label(halign=Gtk.Align.START, valign=Gtk.Align.START,
                          margin_start=8, margin_top=8, css_classes=["platform-badge"])
        box.badge = badge

        overlay = Gtk.Overlay(child=cover)
        overlay.add_overlay(badge)
        overlay.add_overlay(fav)
        box.append(overlay)

        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                          css_classes=["card-title"], margin_top=6)
        sub = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END,
                        css_classes=["mono-dim-sm"])
        box.title, box.sub = title, sub
        box.append(title)
        box.append(sub)

        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._on_card_pressed, box)
        box.add_controller(click)
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda *_a: fav.set_visible(True))
        hover.connect("leave", lambda *_a: fav.set_visible(box.faved))
        box.add_controller(hover)
        box.set_cursor(POINTER_CURSOR)
        box.game_id = None
        box.faved = False
        box.handlers = []
        return box

    def _bind_card(self, box, game):
        box.game_id = game.id
        box.cover.set_size(self._cell)
        box.cover.set_game(game.title, game.cover_path)
        box.title.set_label(game.title)
        box.title.set_tooltip_text(game.title)
        box.badge.set_label(platforms.label(game.platform))
        # Platform is already the badge; the subtitle carries region + size.
        bits = [b for b in (game.region, _fmt_size(game.size) if game.size else "") if b]
        box.sub.set_label(" · ".join(bits))
        self._set_card_fav(box, game.favorite)
        if game.id == self._selected_id:
            box.add_css_class("tile-selected")
        else:
            box.remove_css_class("tile-selected")
        box.handlers = [
            (game, game.connect("notify::favorite",
                                lambda g, _p: self._set_card_fav(box, g.favorite))),
            (game, game.connect("notify::cover-path",
                                lambda g, _p: box.cover.set_game(g.title, g.cover_path))),
        ]

    def _unbind_card(self, box):
        for obj, handler in box.handlers:
            obj.disconnect(handler)
        box.handlers = []
        box.game_id = None

    @staticmethod
    def _set_card_fav(box, faved):
        box.faved = faved
        box.fav.set_icon_name("dice-heart-filled-symbolic" if faved else "dice-heart-symbolic")
        if faved:
            box.fav.add_css_class("faved")
        else:
            box.fav.remove_css_class("faved")
        box.fav.set_visible(faved)

    def _on_card_pressed(self, gesture, n_press, x, y, box):
        if box.game_id is None:
            return
        # Clicks on the heart are the heart's; don't also select.
        widget = box.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None and widget is not box:
            if widget is box.fav:
                return
            widget = widget.get_parent()
        if n_press == 2 and gesture.get_current_button() == 1:
            self._play(box.game_id)
            return
        self._select(box.game_id)

    def _on_grid_width(self, width):
        if width == self._grid_width:
            return
        self._grid_width = width
        GLib.idle_add(self._size_grid)

    def _size_grid(self):
        """Pick an exact column count for the grid's real width and size every
        cover to its column, so cards fill the row with no ragged edge."""
        width = self._grid_width or 800
        slot = self._cover_target + 2 * self.CARD_MARGIN
        n = max(2, min(10, round(width / slot)))
        cell = max(80, width // n - 2 * self.CARD_MARGIN)
        if self.game_grid.get_min_columns() != n or self.game_grid.get_max_columns() != n:
            self.game_grid.set_min_columns(n)
            self.game_grid.set_max_columns(n)
        if cell != self._cell:
            self._cell = cell
            stack = [self.game_grid]
            while stack:
                widget = stack.pop()
                cover = getattr(widget, "cover", None)
                if isinstance(cover, Cover):
                    cover.set_size(cell)
                    continue
                child = widget.get_first_child()
                while child:
                    stack.append(child)
                    child = child.get_next_sibling()
        return False

    def _on_cover_scale(self, scale):
        value = int(scale.get_value())
        if value == self._cover_target:
            return
        self._cover_target = value
        self.settings.set_int("cover-size", value)
        self._size_grid()

    # -------------------------------------------------------------- tabs --

    def _tab_keys(self):
        present = {g.platform for g in self._games}
        keys = ["all"] + [p.key for p in platforms.PLATFORMS if p.key in present]
        return keys + ["favourites"]

    def _build_tabs(self):
        child = self.tabs_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.tabs_box.remove(child)
            child = nxt
        self._tab_buttons = {}
        keys = self._tab_keys()
        for i, key in enumerate(keys):
            if i == 1 or (key == "favourites" and len(keys) > 2):
                self.tabs_box.append(Gtk.Label(label="|", css_classes=["tab-sep"]))
            if key == "all":
                label = "All"
            elif key == "favourites":
                label = "Favourites"
            else:
                label = platforms.label(key)
            btn = Gtk.Button(label=label, css_classes=["flat", "tab-btn"])
            platform = platforms.get(key)
            if platform is not None:
                btn.set_tooltip_text(platform.name)
            btn.set_cursor(POINTER_CURSOR)
            btn.connect("clicked", lambda _b, k=key: self._select_tab(k))
            self.tabs_box.append(btn)
            self._tab_buttons[key] = btn
        if self._tab not in self._tab_buttons:
            self._tab = "all"
        self._highlight_tab()

    def _highlight_tab(self):
        for key, btn in self._tab_buttons.items():
            if key == self._tab:
                btn.add_css_class("tab-active")
            else:
                btn.remove_css_class("tab-active")

    def _select_tab(self, key):
        self._tab = key
        self._highlight_tab()
        self._apply_filters()
        if self.store.get_n_items():
            self.game_grid.scroll_to(0, Gtk.ListScrollFlags.NONE, None)

    def _select_tab_index(self, index):
        keys = self._tab_keys()
        if index < len(keys):
            self._select_tab(keys[index])

    # ---------------------------------------------------- filter / search --

    def _on_toggle_search(self, btn):
        active = btn.get_active()
        self.middle_stack.set_visible_child_name("search" if active else "view")
        if active:
            self.search_entry.grab_focus()
        else:
            self.search_entry.set_text("")

    def _on_search_changed(self, entry):
        self._search = entry.get_text().strip().lower()
        self._apply_filters()

    def _on_sort(self, action, value):
        action.set_state(value)
        self._sort = value.get_string()
        self.settings.set_string("sort", self._sort)
        self._apply_filters()

    def _matches(self, game):
        if self._tab == "favourites" and not game.favorite:
            return False
        if self._tab not in ("all", "favourites") and game.platform != self._tab:
            return False
        if not self._search:
            return True
        row = self._rows.get(game.id)
        hay = " ".join([game.title, game.region, platforms.label(game.platform),
                        row["serial"] if row else "", os.path.basename(game.path)]).lower()
        return all(word in hay for word in self._search.split())

    def _sorted(self, games):
        order = {p.key: i for i, p in enumerate(platforms.PLATFORMS)}
        if self._sort == "platform":
            return sorted(games, key=lambda g: (order.get(g.platform, 99), _sort_title(g.title)))
        if self._sort == "played":
            return sorted(games, key=lambda g: (-g.last_played, _sort_title(g.title)))
        if self._sort == "added":
            return sorted(games, key=lambda g: (-g.added_at, _sort_title(g.title)))
        if self._sort == "size":
            return sorted(games, key=lambda g: -g.size)
        return sorted(games, key=lambda g: _sort_title(g.title))

    def _apply_filters(self):
        games = self._sorted([g for g in self._games if self._matches(g)])
        self.store.splice(0, self.store.get_n_items(), games)
        if not self._games:
            self.paper_stack.set_visible_child_name("empty")
        elif not games:
            if self._search:
                self.none_page.set_title("No Matches")
                self.none_page.set_description(f"Nothing matches “{self._search}”.")
            elif self._tab == "favourites":
                self.none_page.set_title("No Favourites Yet")
                self.none_page.set_description(
                    "Hover a cover and click its heart, or use the heart in the "
                    "side panel, to keep a game here.")
            else:
                self.none_page.set_title("Nothing Here")
                self.none_page.set_description("")
            self.paper_stack.set_visible_child_name("none")
        else:
            self.paper_stack.set_visible_child_name("grid")

    # ------------------------------------------------------------ loading --

    def _game_from_row(self, r):
        return Game(id=r["id"], path=r["path"], platform=r["platform"], title=r["title"],
                    region=r["region"] or "", size=r["size"] or 0,
                    cover_path=r["cover_path"] or "", favorite=bool(r["favorite"]),
                    added_at=r["added_at"] or 0.0, last_played=r["last_played"] or 0.0)

    def _reload(self):
        rows = lib.all_games(self.con)
        self._rows = {r["id"]: r for r in rows}
        self._games = [self._game_from_row(r) for r in rows]
        self._by_id = {g.id: g for g in self._games}
        self._build_tabs()
        self._apply_filters()
        if self._selected_id is not None:
            if self._selected_id in self._by_id:
                self._show_info(self._selected_id)
            else:
                self._close_info()
        return False

    def _toast(self, text, timeout=None):
        toast = Adw.Toast.new(text)
        if timeout is not None:
            toast.set_timeout(timeout)
        self.toast_overlay.add_toast(toast)
        return toast

    def _launch_status(self, text, timeout=3):
        """One toast for launch progress, replaced in place (toasts otherwise
        queue, so an error would only show after 'Starting…' timed out)."""
        old = getattr(self, "_launch_toast", None)
        if old is not None:
            old.dismiss()
        self._launch_toast = self._toast(text, timeout)

    def _on_add_folder(self):
        dialog = Gtk.FileDialog(title="Add ROM Folder")
        dialog.select_folder(self, None, self._folder_chosen)

    def _folder_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        path = folder.get_path() if folder else None
        if not path:
            return
        self._add_folders([self._real_path(path)])

    @staticmethod
    def _real_path(path):
        """Prefer the real host path over a document-portal one when Dice can
        read it, so emulators outside the sandbox can open the games too."""
        host = portal.host_path(path)
        return host if host != path and os.access(host, os.R_OK) else path

    def _add_folders(self, paths):
        for path in paths:
            lib.add_folder(self.con, path)

        def scan(progress):
            return sum(lib.scan_folder(self.con, p, progress) for p in paths)

        self._run_scan(scan, "Looking for games…", refresh_watchers=True)

    def _on_rescan(self):
        self._run_scan(lambda cb: lib.scan_all(self.con, cb), "Rescanning library…")

    def _run_scan(self, scan_fn, start_msg, refresh_watchers=False):
        """Scan on a worker thread behind one live progress toast."""
        toast = Adw.Toast.new(start_msg)
        toast.set_timeout(0)
        self.toast_overlay.add_toast(toast)
        state = {"last": 0.0}

        def progress(done, total):
            now = time.monotonic()
            if not total or (done < total and now - state["last"] < 0.1):
                return
            state["last"] = now
            GLib.idle_add(lambda: toast.set_title(f"Scanning… {done:,} of {total:,} files") or False)

        def work():
            try:
                scan_fn(progress)
            finally:
                GLib.idle_add(finish)

        def finish():
            toast.dismiss()
            self._reload()
            if refresh_watchers:
                self._refresh_watchers()
            n = len(self._games)
            self._toast(f"Library updated — {n:,} game{'s' if n != 1 else ''}")
            self._start_cover_worker()
            return False

        threading.Thread(target=work, daemon=True).start()

    # --------------------------------------------------------- side panel --

    def _setup_info_panel(self):
        self._info_cover = Cover(self.PANEL_WIDTH)
        self._info_cover.add_css_class("info-preview")
        self.info_preview_slot.append(self._info_cover)
        self.info_close_btn.connect("clicked", lambda *_: self._close_info())
        self.info_play_btn.connect("clicked", lambda *_: self._play(self._selected_id))
        self.info_fav_btn.connect("clicked", lambda *_: self._toggle_fav(self._selected_id))

    def _more_menu(self, row):
        menu = Gio.Menu()
        cover = Gio.Menu()
        cover.append("Set Cover Image…", "win.set-cover")
        if row["cover_source"] == "user":
            cover.append("Use Original Cover", "win.reset-cover")
        elif row["cover_source"] not in ("sidecar",):
            cover.append("Find Cover Online", "win.fetch-cover")
        menu.append_section(None, cover)
        files = Gio.Menu()
        files.append("Show in Files", "win.show-in-files")
        files.append("Copy Path", "win.copy-path")
        menu.append_section(None, files)
        return menu

    def _select(self, game_id):
        previous = self._selected_id
        self._selected_id = game_id
        self._mark_selected(previous, game_id)
        self._show_info(game_id)

    def _mark_selected(self, old_id, new_id):
        child = self.game_grid.get_first_child()
        while child:
            box = child.get_first_child()
            gid = getattr(box, "game_id", None)
            if gid is not None:
                if gid == new_id:
                    box.add_css_class("tile-selected")
                elif gid == old_id:
                    box.remove_css_class("tile-selected")
            child = child.get_next_sibling()

    def _show_info(self, game_id):
        row = lib.get_game(self.con, game_id)
        game = self._by_id.get(game_id)
        if row is None or game is None:
            return
        platform = platforms.get(row["platform"])
        self._info_cover.set_size(self.PANEL_WIDTH)
        self._info_cover.set_game(row["title"], row["cover_path"])
        self.info_title.set_label(row["title"])
        self.info_subtitle.set_label((platform.name if platform else row["platform"]).upper())
        self._update_info_fav(game.favorite)
        self.info_more_btn.set_menu_model(self._more_menu(row))

        box = self.info_rows_box
        child = box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

        box.append(self._info_row("Emulator", self._emulator_label(row["platform"]),
                                  on_click=self._on_preferences))
        box.append(self._info_divider())
        box.append(self._info_row("Region", row["region"] or "—"))
        serial_key = "Game code" if row["platform"] in ("gba", "nds", "3ds") else "Serial"
        box.append(self._info_row(serial_key, row["serial"] or "—"))
        if row["internal_title"] and row["internal_title"].lower() != row["title"].lower():
            box.append(self._info_row("Internal title", row["internal_title"]))
        box.append(self._info_row("Format", row["format"] or "—"))
        box.append(self._info_row("Size", _fmt_size(row["size"])))
        box.append(self._info_divider())
        box.append(self._info_row("Last played", _fmt_when(row["last_played"])))
        box.append(self._info_row("Play time", _fmt_duration(row["play_seconds"] or 0)))
        box.append(self._info_row("Added", _fmt_when(row["added_at"])))
        box.append(self._info_divider())
        box.append(self._info_row("File name", os.path.basename(row["path"])))
        path = row["path"]
        box.append(self._info_row("Path", path,
                                  on_click=lambda p=path: self._open_in_files(p)))

        if not self.info_revealer.get_reveal_child():
            self.info_revealer.set_visible(True)
            self.info_revealer.set_reveal_child(True)
            self._apply_layout_metrics()

    def _update_info_fav(self, faved):
        self.info_fav_btn.set_icon_name(
            "dice-heart-filled-symbolic" if faved else "dice-heart-symbolic")
        self.info_fav_btn.set_tooltip_text(
            "Remove from Favourites" if faved else "Add to Favourites")
        if faved:
            self.info_fav_btn.add_css_class("faved")
        else:
            self.info_fav_btn.remove_css_class("faved")

    def _close_info(self):
        old = self._selected_id
        self._selected_id = None
        self._mark_selected(old, None)
        self.info_revealer.set_reveal_child(False)
        self._apply_layout_metrics()

    def _info_row(self, key, value, on_click=None):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=SPACE_S)
        k = Gtk.Label(label=key, xalign=0, css_classes=["info-key"])
        v = Gtk.Label(label=value, xalign=1, hexpand=True, max_width_chars=1,
                      ellipsize=Pango.EllipsizeMode.START if key == "Path" else Pango.EllipsizeMode.END,
                      css_classes=["info-value"])
        v.set_has_tooltip(True)
        v.connect("query-tooltip", self._on_label_tooltip)
        if on_click is not None:
            v.add_css_class("info-link")
            v.set_cursor(POINTER_CURSOR)
            gesture = Gtk.GestureClick()
            gesture.connect("released", lambda *_a: on_click())
            v.add_controller(gesture)
        else:
            v.set_selectable(True)
        row.append(k)
        row.append(v)
        return row

    @staticmethod
    def _on_label_tooltip(label, _x, _y, _keyboard, tooltip):
        layout = label.get_layout()
        if layout is not None and layout.is_ellipsized():
            tooltip.set_text(label.get_text())
            return True
        return False

    @staticmethod
    def _info_divider():
        return Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL,
                             css_classes=["info-divider"])

    # ---------------------------------------------------- game actions --

    def _toggle_fav(self, game_id):
        game = self._by_id.get(game_id)
        if game is None:
            return
        game.favorite = not game.favorite
        lib.set_favorite(self.con, game_id, game.favorite)
        if game_id == self._selected_id:
            self._update_info_fav(game.favorite)
        if self._tab == "favourites":
            self._apply_filters()

    def _open_in_files(self, path):
        try:
            launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(path))
            launcher.open_containing_folder(self, None, None)
        except Exception:
            pass

    def _show_in_files(self, game_id):
        game = self._by_id.get(game_id)
        if game is not None:
            self._open_in_files(game.path)

    def _copy_path(self, game_id):
        game = self._by_id.get(game_id)
        if game is not None:
            self.get_clipboard().set(game.path)
            self._toast("Path copied")

    def _pick_cover(self, game_id):
        if game_id is None:
            return
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        images = Gtk.FileFilter(name="Images")
        images.add_mime_type("image/png")
        images.add_mime_type("image/jpeg")
        images.add_mime_type("image/webp")
        filters.append(images)
        dialog = Gtk.FileDialog(title="Choose Cover Image", filters=filters)

        def done(dlg, result):
            try:
                file = dlg.open_finish(result)
            except GLib.Error:
                return
            if file is None or not file.get_path():
                return
            try:
                path = lib.set_user_cover(self.con, game_id, file.get_path())
            except OSError as exc:
                self._toast(f"Couldn't use that image: {exc.strerror}")
                return
            self._set_game_cover(game_id, path)

        dialog.open(self, None, done)

    def _reset_cover(self, game_id):
        lib.clear_user_cover(self.con, game_id)
        row = lib.get_game(self.con, game_id)
        self._set_game_cover(game_id, row["cover_path"] if row else "")
        if row is not None and not row["cover_path"]:
            self._start_cover_worker()

    def _set_game_cover(self, game_id, path):
        game = self._by_id.get(game_id)
        if game is None:
            return
        forget_thumbnail(path or "")
        game.cover_path = path or ""
        if game_id == self._selected_id:
            self._show_info(game_id)

    def _fetch_cover_now(self, game_id):
        game = self._by_id.get(game_id)
        if game is None:
            return
        self._toast("Looking for cover art…")

        def work():
            data = covers.fetch(game.platform, game.path)
            con = lib.connect()
            path = lib.set_online_cover(con, game_id, data) if data else None
            if not data:
                lib.mark_cover_checked(con, game_id)
            con.close()
            GLib.idle_add(done, path)

        def done(path):
            if path:
                self._set_game_cover(game_id, path)
                self._toast("Cover found")
            else:
                self._toast("No cover found online — try Set Cover Image…")
            return False

        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------- covers --

    def _start_cover_worker(self):
        """Fill in missing covers from the libretro archive in the background,
        one game at a time, so they pop into the grid as they arrive."""
        if not self.settings.get_boolean("fetch-covers"):
            return
        if self._cover_worker is not None and self._cover_worker.is_alive():
            return

        def work():
            con = lib.connect()
            try:
                for row in lib.games_needing_covers(con):
                    if not self.settings.get_boolean("fetch-covers"):
                        break
                    data = covers.fetch(row["platform"], row["path"])
                    if data:
                        path = lib.set_online_cover(con, row["id"], data)
                        if path:
                            GLib.idle_add(self._set_game_cover, row["id"], path)
                    else:
                        lib.mark_cover_checked(con, row["id"])
            finally:
                con.close()

        self._cover_worker = threading.Thread(target=work, daemon=True)
        self._cover_worker.start()

    # ------------------------------------------------------------ playing --

    def _detect_emulators(self):
        def work():
            found = {p.key: emulators.detect(p.key) for p in platforms.PLATFORMS}
            GLib.idle_add(done, found)

        def done(found):
            self._detected = found
            if self._selected_id is not None:
                self._show_info(self._selected_id)
            return False

        threading.Thread(target=work, daemon=True).start()

    def _custom_commands(self):
        return dict(self.settings.get_value("emulators").unpack())

    def _command_for(self, platform_key):
        custom = self._custom_commands().get(platform_key, "").strip()
        if custom:
            return custom
        emulator = self._detected.get(platform_key)
        return emulator.command if emulator else None

    def _emulator_label(self, platform_key):
        custom = self._custom_commands().get(platform_key, "").strip()
        if custom:
            return "Custom command"
        emulator = self._detected.get(platform_key)
        if emulator is not None:
            return emulator.name + (" (Flatpak)" if emulator.kind == "flatpak" else "")
        if platform_key not in self._detected:
            return "Detecting…"
        return "Not found — set one up"

    def _play(self, game_id):
        game = self._by_id.get(game_id)
        if game is None:
            return
        command = self._command_for(game.platform)
        if not command:
            self._no_emulator(game.platform)
            return
        # Emulators run outside Dice's sandbox: give them the real path.
        argv = emulators.build_argv(command, portal.host_path(game.path))
        if not argv:
            self._no_emulator(game.platform)
            return
        try:
            proc = Gio.Subprocess.new(emulators.host_argv(argv),
                                      Gio.SubprocessFlags.STDOUT_SILENCE
                                      | Gio.SubprocessFlags.STDERR_PIPE)
        except GLib.Error as exc:
            self._launch_status(f"Couldn't start {argv[0]}: {exc.message}", 6)
            return
        started = time.time()
        self._running[game_id] = started
        lib.record_launch(self.con, game_id)
        game.last_played = started
        self._launch_status(f"Starting {game.title}…")
        if game_id == self._selected_id:
            self._show_info(game_id)
        proc.communicate_utf8_async(None, None, self._on_emulator_exit, (game_id, started))

    def _on_emulator_exit(self, proc, result, data):
        game_id, started = data
        self._running.pop(game_id, None)
        try:
            _ok, _out, err = proc.communicate_utf8_finish(result)
        except GLib.Error:
            err = ""
        elapsed = time.time() - started
        failed = proc.get_if_exited() and proc.get_exit_status() != 0
        if failed and elapsed < 5:
            last = (err or "").strip().splitlines()[-1:] or ["exit status "
                                                             f"{proc.get_exit_status()}"]
            self._launch_status(f"The emulator quit right away: {last[0][:120]}", 8)
        elif elapsed >= 30:
            lib.add_play_time(self.con, game_id, elapsed)
        if game_id == self._selected_id:
            self._show_info(game_id)

    def _no_emulator(self, platform_key):
        platform = platforms.get(platform_key)
        names = ", ".join(sorted({e.name for e in platform.emulators})) if platform else ""
        dialog = Adw.AlertDialog(
            heading=f"No {platform.label if platform else ''} emulator found",
            body=(f"Dice launches your games in an emulator installed on this "
                  f"computer. Install {names or 'one'} (Flathub has it), or set the "
                  f"command to use in Preferences."))
        dialog.add_response("close", "Close")
        dialog.add_response("prefs", "Open Preferences")
        dialog.set_response_appearance("prefs", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda _d, r: self._on_preferences() if r == "prefs" else None)
        dialog.present(self)

    # --------------------------------------------------------- preferences --

    def _on_preferences(self, *_args):
        dialog = Adw.PreferencesDialog(title="Preferences")
        page = Adw.PreferencesPage()

        appearance = Adw.PreferencesGroup(title="Appearance")
        themes = ("light", "dark", "system")
        theme_row = Adw.ComboRow(title="Theme",
                                 model=Gtk.StringList.new(["Light", "Dark", "System"]))
        current = self.settings.get_string("theme")
        theme_row.set_selected(themes.index(current) if current in themes else 2)

        def on_theme(row, _pspec):
            theme = themes[row.get_selected()]
            self.settings.set_string("theme", theme)
            self._apply_theme(theme)

        theme_row.connect("notify::selected", on_theme)
        appearance.add(theme_row)
        page.add(appearance)

        emus = Adw.PreferencesGroup(
            title="Emulators",
            description="Leave a command empty to use the emulator Dice finds "
                        "installed. {rom} is replaced by the game's path.")
        custom = self._custom_commands()
        for platform in platforms.PLATFORMS:
            detected = self._detected.get(platform.key)
            row = Adw.EntryRow(title=f"{platform.name}")
            row.set_text(custom.get(platform.key, ""))
            hint = detected.command if detected else "no emulator found"
            row.set_tooltip_text(f"Detected: {hint}")
            row.set_show_apply_button(True)
            row.connect("apply", self._on_emulator_apply, platform.key)
            status = Gtk.Label(label=(detected.name if detected else "Not found"),
                               valign=Gtk.Align.CENTER, css_classes=["dim-label", "caption"])
            row.add_suffix(status)
            emus.add(row)
        page.add(emus)

        folders = Adw.PreferencesGroup(title="ROM Folders",
                                       description="Folders Dice scans for games")
        for path in lib.all_folders(self.con):
            folder_row = Adw.ActionRow(title=GLib.markup_escape_text(path), title_lines=1)
            remove_btn = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER,
                                    tooltip_text="Remove this folder from Dice",
                                    css_classes=["flat"])
            remove_btn.connect("clicked",
                               lambda _b, p=path, d=dialog: self._confirm_remove_folder(p, d))
            folder_row.add_suffix(remove_btn)
            folders.add(folder_row)
        add_row = Adw.ActionRow(title="Add ROM Folder…", activatable=True)
        add_row.add_prefix(Gtk.Image.new_from_icon_name("list-add-symbolic"))
        add_row.connect("activated", lambda *_: (dialog.close(), self._on_add_folder()))
        folders.add(add_row)
        watch_row = Adw.SwitchRow(
            title="Watch ROM folders",
            subtitle="Rescan automatically when files in your ROM folders change")
        self.settings.bind("watch-folders", watch_row, "active", Gio.SettingsBindFlags.DEFAULT)
        folders.add(watch_row)
        page.add(folders)

        art = Adw.PreferencesGroup(
            title="Cover Art",
            description="Dice uses images named like the ROM (next to it or in a "
                        "covers folder) and art inside PSP discs first.")
        fetch_row = Adw.SwitchRow(
            title="Download missing covers",
            subtitle="Look up box art by file name in the libretro thumbnail archive")
        self.settings.bind("fetch-covers", fetch_row, "active", Gio.SettingsBindFlags.DEFAULT)
        art.add(fetch_row)
        page.add(art)

        dialog.add(page)
        dialog.present(self)

    def _on_emulator_apply(self, row, platform_key):
        custom = self._custom_commands()
        text = row.get_text().strip()
        if text:
            custom[platform_key] = text
        else:
            custom.pop(platform_key, None)
        self.settings.set_value("emulators", GLib.Variant("a{ss}", custom))
        if self._selected_id is not None:
            self._show_info(self._selected_id)

    def _confirm_remove_folder(self, path, prefs_dialog):
        confirm = Adw.AlertDialog(
            heading="Remove folder?",
            body=f"Dice will stop showing games from “{path}” and forget their "
                 "favourites and play history. Nothing on disk is touched.")
        confirm.add_response("cancel", "Cancel")
        confirm.add_response("remove", "Remove")
        confirm.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, response):
            if response != "remove":
                return
            lib.remove_folder(self.con, path)
            self._reload()
            self._refresh_watchers()
            self._toast("Folder removed")
            prefs_dialog.close()

        confirm.connect("response", on_response)
        confirm.present(self)

    # -------------------------------------------------- watching / drops --

    def _setup_watching(self):
        self.settings.connect("changed::watch-folders", lambda *_: self._refresh_watchers())
        self.settings.connect("changed::fetch-covers", lambda *_: self._start_cover_worker())
        self._refresh_watchers()

    def _refresh_watchers(self):
        for monitor in self._monitors:
            monitor.cancel()
        self._monitors = []
        if not self.settings.get_boolean("watch-folders"):
            return
        count = 0
        for root in lib.all_folders(self.con):
            for dirpath, dirnames, _files in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                if count >= 256:
                    return
                try:
                    monitor = Gio.File.new_for_path(dirpath).monitor_directory(
                        Gio.FileMonitorFlags.NONE, None)
                except GLib.Error:
                    continue
                monitor.connect("changed", self._on_folder_event)
                self._monitors.append(monitor)
                count += 1

    def _on_folder_event(self, *_args):
        if self._watch_debounce:
            GLib.source_remove(self._watch_debounce)
        self._watch_debounce = GLib.timeout_add_seconds(3, self._watch_rescan)

    def _watch_rescan(self):
        self._watch_debounce = 0

        def work():
            lib.scan_all(self.con)
            GLib.idle_add(self._reload)
            GLib.idle_add(self._start_cover_worker)

        threading.Thread(target=work, daemon=True).start()
        return False

    def _setup_dnd(self):
        """Folders dropped anywhere on the window are added to the library."""
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

    def _on_drop(self, _target, value, _x, _y):
        try:
            files = value.get_files()
        except Exception:
            return False
        folders = [self._real_path(f.get_path()) for f in files
                   if f.get_path() and os.path.isdir(f.get_path())]
        if not folders:
            self._toast("Drop a folder to add it to your library")
            return False
        self._add_folders(folders)
        return True
