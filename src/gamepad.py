"""Gamepad navigation through libmanette (part of the GNOME runtime).

Maps pads to a handful of actions the window understands:

    D-pad / left stick   move the selection
    A (south)            play the selected game (or select the first one)
    B (east)             close the side panel
    Y (north)            toggle favourite
    LB / RB              previous / next tab

Input only counts while Dice's window is active, so pressing buttons in an
emulator Dice launched never drives Dice behind it. If libmanette isn't
available the class does nothing and the app works as before.
"""
from gi.repository import GLib

try:
    import gi
    gi.require_version("Manette", "0.2")
    from gi.repository import Manette
except (ImportError, ValueError):
    Manette = None

# Linux input codes, which libmanette reports after applying SDL mappings.
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 304, 305, 307, 308
BTN_TL, BTN_TR = 310, 311
BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT = 544, 545, 546, 547
ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y = 0, 1, 16, 17

BUTTONS = {
    BTN_SOUTH: "activate", BTN_EAST: "back", BTN_NORTH: "favourite",
    BTN_TL: "prev-tab", BTN_TR: "next-tab",
    BTN_DPAD_UP: "up", BTN_DPAD_DOWN: "down",
    BTN_DPAD_LEFT: "left", BTN_DPAD_RIGHT: "right",
}
STICK_THRESHOLD = 0.6
REPEAT_DELAY, REPEAT_RATE = 350, 110      # ms, while a direction is held


class Gamepads:
    def __init__(self, on_action, is_active):
        """on_action(name) is called on the main loop; is_active() says
        whether the window should take input right now."""
        self._on_action = on_action
        self._is_active = is_active
        self._held = {}          # (device id, axis) -> direction name
        self._repeat_id = 0
        self._repeat_dir = None
        self.available = Manette is not None
        if not self.available:
            return
        self._monitor = Manette.Monitor.new()
        self._monitor.connect("device-connected", lambda _m, dev: self._watch(dev))
        iterator = self._monitor.iterate()
        while True:
            ok, device = iterator.next()
            if not ok:
                break
            self._watch(device)

    def _watch(self, device):
        device.connect("button-press-event", self._on_button)
        device.connect("absolute-axis-event", self._on_axis)
        device.connect("hat-axis-event", self._on_hat)

    def _fire(self, action):
        if self._is_active():
            self._on_action(action)

    def _on_button(self, _device, event):
        ok, button = event.get_button()
        if ok and button in BUTTONS:
            self._fire(BUTTONS[button])

    def _on_hat(self, device, event):
        ok, axis, value = event.get_hat()
        if ok and axis in (ABS_HAT0X, ABS_HAT0Y):
            names = ("left", "right") if axis == ABS_HAT0X else ("up", "down")
            self._direction(device, axis, names[0] if value < 0 else names[1] if value > 0 else None)

    def _on_axis(self, device, event):
        ok, axis, value = event.get_absolute()
        if ok and axis in (ABS_X, ABS_Y):
            names = ("left", "right") if axis == ABS_X else ("up", "down")
            direction = (names[0] if value < -STICK_THRESHOLD
                         else names[1] if value > STICK_THRESHOLD else None)
            self._direction(device, axis, direction)

    def _direction(self, device, axis, direction):
        key = (id(device), axis)
        if self._held.get(key) == direction:
            return
        self._held[key] = direction
        self._stop_repeat()
        if direction is None:
            return
        self._fire(direction)
        self._repeat_dir = direction
        self._repeat_id = GLib.timeout_add(REPEAT_DELAY, self._start_repeat)

    def _start_repeat(self):
        self._repeat_id = GLib.timeout_add(REPEAT_RATE, self._repeat)
        return False

    def _repeat(self):
        if self._repeat_dir is None:
            self._repeat_id = 0
            return False
        self._fire(self._repeat_dir)
        return True

    def _stop_repeat(self):
        if self._repeat_id:
            GLib.source_remove(self._repeat_id)
        self._repeat_id = 0
        self._repeat_dir = None
