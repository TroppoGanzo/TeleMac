"""Tabelle dei tasti macOS (virtual keycode ANSI, flag dei modificatori, tasti multimediali)."""

# Codici tasto virtuali macOS (kVK_*), layout ANSI/QWERTY.
KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "equal": 24, "9": 25,
    "7": 26, "minus": 27, "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35,
    "l": 37, "j": 38, "k": 40, "comma": 43, "slash": 44, "n": 45, "m": 46,
    "period": 47,
    "return": 36, "tab": 48, "space": 49, "backspace": 51, "escape": 53,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98,
    "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    "home": 115, "pageup": 116, "delete": 117, "end": 119, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
}

FLAG_SHIFT = 0x00020000
FLAG_CONTROL = 0x00040000
FLAG_ALTERNATE = 0x00080000
FLAG_COMMAND = 0x00100000
FLAG_NUMERIC_PAD = 0x00200000
FLAG_SECONDARY_FN = 0x00800000

# nome -> (keycode del modificatore, flag)
MODIFIERS = {
    "cmd": (0x37, FLAG_COMMAND),
    "shift": (0x38, FLAG_SHIFT),
    "alt": (0x3A, FLAG_ALTERNATE),
    "ctrl": (0x3B, FLAG_CONTROL),
}

# Una tastiera Apple vera invia questi flag extra per frecce e tasti "fn";
# alcune scorciatoie di sistema (es. Mission Control con ctrl+↑) li richiedono.
EXTRA_FLAGS = {name: FLAG_NUMERIC_PAD | FLAG_SECONDARY_FN for name in ("left", "right", "up", "down")}
EXTRA_FLAGS.update({name: FLAG_SECONDARY_FN for name in (
    "home", "end", "pageup", "pagedown", "delete",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
)})

# Tasti multimediali (NX_KEYTYPE_*), inviati come eventi NSSystemDefined.
MEDIA_KEYS = {
    "volup": 0,
    "voldown": 1,
    "brightup": 2,
    "brightdown": 3,
    "mute": 7,
    "play": 16,
    "next": 17,
    "prev": 18,
    "fast": 19,
    "rewind": 20,
}

MOUSE_BUTTONS = ("left", "right")
