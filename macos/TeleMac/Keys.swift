// Tabelle dei tasti macOS: codici tasto virtuali (layout ANSI/QWERTY), modificatori
// e tasti multimediali. Gli stessi nomi che manda la pagina web sull'iPhone.

import CoreGraphics

enum Keys {
    static let codes: [String: CGKeyCode] = [
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
    ]

    /// nome -> (codice del tasto modificatore, flag)
    static let modifiers: [String: (code: CGKeyCode, flag: CGEventFlags)] = [
        "cmd": (0x37, .maskCommand),
        "shift": (0x38, .maskShift),
        "alt": (0x3A, .maskAlternate),
        "ctrl": (0x3B, .maskControl),
    ]

    /// Una tastiera Apple vera manda questi flag extra per frecce e tasti "fn";
    /// alcune scorciatoie di sistema (es. Mission Control con ctrl+↑) li richiedono.
    static let extraFlags: [String: CGEventFlags] = {
        var table: [String: CGEventFlags] = [:]
        for name in ["left", "right", "up", "down"] { table[name] = [.maskNumericPad, .maskSecondaryFn] }
        for name in ["home", "end", "pageup", "pagedown", "delete",
                     "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12"] {
            table[name] = .maskSecondaryFn
        }
        return table
    }()

    /// Tasti multimediali (NX_KEYTYPE_*), mandati come eventi di sistema.
    static let media: [String: Int] = [
        "volup": 0, "voldown": 1, "brightup": 2, "brightdown": 3, "mute": 7,
        "play": 16, "next": 17, "prev": 18, "fast": 19, "rewind": 20,
    ]

    static let mouseButtons: Set<String> = ["left", "right"]
}
