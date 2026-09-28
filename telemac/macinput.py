"""Controllo di mouse e tastiera su macOS tramite CoreGraphics (via ctypes, zero dipendenze).

Richiede che l'app che avvia il server (di solito il Terminale) sia autorizzata in
Impostazioni di Sistema → Privacy e sicurezza → Accessibilità.
"""

from __future__ import annotations

import ctypes
import subprocess
import time
from ctypes import (
    POINTER, Structure, c_bool, c_char_p, c_double, c_float, c_int32, c_int64, c_long,
    c_short, c_uint16, c_uint32, c_uint64, c_ulong, c_void_p,
)

from keys import EXTRA_FLAGS, KEYCODES, MEDIA_KEYS, MODIFIERS


class CGPoint(Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


class CGSize(Structure):
    _fields_ = [("width", c_double), ("height", c_double)]


class CGRect(Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


def _load(path):
    return ctypes.CDLL(path)


_CG = _load("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
_CF = _load("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
_AS = _load("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
_OBJC = _load("/usr/lib/libobjc.A.dylib")
_load("/System/Library/Frameworks/AppKit.framework/AppKit")  # registra la classe NSEvent


def _fn(lib, name, restype, *argtypes):
    f = getattr(lib, name)
    f.restype = restype
    f.argtypes = list(argtypes)
    return f


CGEventCreate = _fn(_CG, "CGEventCreate", c_void_p, c_void_p)
CGEventGetLocation = _fn(_CG, "CGEventGetLocation", CGPoint, c_void_p)
CGEventCreateMouseEvent = _fn(_CG, "CGEventCreateMouseEvent", c_void_p, c_void_p, c_uint32, CGPoint, c_uint32)
CGEventCreateKeyboardEvent = _fn(_CG, "CGEventCreateKeyboardEvent", c_void_p, c_void_p, c_uint16, c_bool)
CGEventCreateScrollWheelEvent2 = _fn(
    _CG, "CGEventCreateScrollWheelEvent2", c_void_p, c_void_p, c_uint32, c_uint32, c_int32, c_int32, c_int32
)
CGEventSetIntegerValueField = _fn(_CG, "CGEventSetIntegerValueField", None, c_void_p, c_uint32, c_int64)
CGEventSetFlags = _fn(_CG, "CGEventSetFlags", None, c_void_p, c_uint64)
CGEventKeyboardSetUnicodeString = _fn(
    _CG, "CGEventKeyboardSetUnicodeString", None, c_void_p, c_ulong, POINTER(c_uint16)
)
CGEventPost = _fn(_CG, "CGEventPost", None, c_uint32, c_void_p)
CGGetActiveDisplayList = _fn(
    _CG, "CGGetActiveDisplayList", c_int32, c_uint32, POINTER(c_uint32), POINTER(c_uint32)
)
CGDisplayBounds = _fn(_CG, "CGDisplayBounds", CGRect, c_uint32)
CFRelease = _fn(_CF, "CFRelease", None, c_void_p)
CFDictionaryCreate = _fn(
    _CF, "CFDictionaryCreate", c_void_p,
    c_void_p, POINTER(c_void_p), POINTER(c_void_p), c_long, c_void_p, c_void_p,
)
AXIsProcessTrusted = _fn(_AS, "AXIsProcessTrusted", c_bool)
AXIsProcessTrustedWithOptions = _fn(_AS, "AXIsProcessTrustedWithOptions", c_bool, c_void_p)

# Runtime Objective-C: serve solo per i tasti multimediali (NSEvent).
objc_getClass = _fn(_OBJC, "objc_getClass", c_void_p, c_char_p)
sel_registerName = _fn(_OBJC, "sel_registerName", c_void_p, c_char_p)
objc_autoreleasePoolPush = _fn(_OBJC, "objc_autoreleasePoolPush", c_void_p)
objc_autoreleasePoolPop = _fn(_OBJC, "objc_autoreleasePoolPop", None, c_void_p)
_MSG_SEND = ctypes.cast(_OBJC.objc_msgSend, c_void_p).value
# +[NSEvent otherEventWithType:location:modifierFlags:timestamp:windowNumber:context:subtype:data1:data2:]
_msg_other_event = ctypes.CFUNCTYPE(
    c_void_p, c_void_p, c_void_p,
    c_ulong, CGPoint, c_ulong, c_double, c_long, c_void_p, c_short, c_long, c_long,
)(_MSG_SEND)
# -[NSEvent CGEvent]
_msg_get_ptr = ctypes.CFUNCTYPE(c_void_p, c_void_p, c_void_p)(_MSG_SEND)

_NSEvent = objc_getClass(b"NSEvent")
_SEL_OTHER_EVENT = sel_registerName(
    b"otherEventWithType:location:modifierFlags:timestamp:windowNumber:context:subtype:data1:data2:"
)
_SEL_CGEVENT = sel_registerName(b"CGEvent")

kCGHIDEventTap = 0
kCGEventLeftMouseDown = 1
kCGEventLeftMouseUp = 2
kCGEventRightMouseDown = 3
kCGEventRightMouseUp = 4
kCGEventMouseMoved = 5
kCGEventLeftMouseDragged = 6
kCGEventRightMouseDragged = 7
kCGMouseEventClickState = 1
kCGMouseEventDeltaX = 4
kCGMouseEventDeltaY = 5
kCGScrollEventUnitPixel = 0
NSEventTypeSystemDefined = 14

_BUTTONS = {
    # nome: (codice CG, evento giù, evento su, evento trascinamento)
    "left": (0, kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGEventLeftMouseDragged),
    "right": (1, kCGEventRightMouseDown, kCGEventRightMouseUp, kCGEventRightMouseDragged),
}

DOUBLE_CLICK_TIME = 0.5
DOUBLE_CLICK_DISTANCE = 6.0


def _post(event):
    if event:
        CGEventPost(kCGHIDEventTap, event)
        CFRelease(event)


def _cursor_scale_api():
    """Funzioni (non documentate) che regolano la dimensione del cursore: le
    stesse usate da Impostazioni → Accessibilità → Dimensioni del puntatore.
    Stanno in CoreGraphics (macOS più vecchi) o in SkyLight (più recenti).
    Restituisce (connessione, leggi, imposta) oppure None se non ci sono."""
    candidates = [
        (_CG, "CGSMainConnectionID", "CGSGetCursorScale", "CGSSetCursorScale"),
    ]
    try:
        sky = _load("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight")
        candidates.append((sky, "SLSMainConnectionID", "SLSGetCursorScale", "SLSSetCursorScale"))
    except OSError:
        pass
    for lib, main_name, get_name, set_name in candidates:
        try:
            main = _fn(lib, main_name, c_int32)
            get = _fn(lib, get_name, c_int32, c_int32, POINTER(c_float))
            set_ = _fn(lib, set_name, c_int32, c_int32, c_float)
            return main, get, set_
        except AttributeError:
            continue
    return None


def is_trusted(prompt=False):
    """True se il processo può inviare eventi. Con prompt=True macOS mostra la richiesta di permesso."""
    if not prompt:
        return bool(AXIsProcessTrusted())
    key = c_void_p.in_dll(_AS, "kAXTrustedCheckOptionPrompt")
    true = c_void_p.in_dll(_CF, "kCFBooleanTrue")
    keys = (c_void_p * 1)(key.value)
    values = (c_void_p * 1)(true.value)
    options = CFDictionaryCreate(None, keys, values, 1, None, None)
    try:
        return bool(AXIsProcessTrustedWithOptions(options))
    finally:
        if options:
            CFRelease(options)


class MacBackend:
    def __init__(self):
        self._pos = None
        self._pos_time = 0.0
        self._pressed = set()
        self._last_click = None  # (bottone, istante, x, y, conteggio)
        self._displays = []
        self._displays_time = 0.0
        self._scroll_rest = [0.0, 0.0]
        self._display_asleep = False
        self._cursor_api = None
        self._cursor_original = None  # dimensione del cursore prima che la cambiassimo

    # --- posizione del puntatore ---

    def _real_location(self):
        ev = CGEventCreate(None)
        p = CGEventGetLocation(ev)
        CFRelease(ev)
        return p.x, p.y

    def _location(self):
        # Teniamo una posizione nostra (con i decimali) per movimenti fluidi; la
        # risincronizziamo con quella reale se non ci muoviamo da un po'.
        now = time.monotonic()
        if self._pos is None or now - self._pos_time > 0.4:
            self._pos = self._real_location()
        self._pos_time = now
        return self._pos

    def _display_rects(self):
        now = time.monotonic()
        if not self._displays or now - self._displays_time > 3:
            ids = (c_uint32 * 16)()
            count = c_uint32(0)
            if CGGetActiveDisplayList(16, ids, ctypes.byref(count)) == 0:
                rects = []
                for i in range(count.value):
                    r = CGDisplayBounds(ids[i])
                    rects.append((r.origin.x, r.origin.y, r.origin.x + r.size.width, r.origin.y + r.size.height))
                self._displays = rects
            self._displays_time = now
        return self._displays

    def _clamp(self, x, y, prev):
        rects = self._display_rects()
        if not rects:
            return x, y
        for x0, y0, x1, y1 in rects:
            if x0 <= x < x1 and y0 <= y < y1:
                return x, y
        # Fuori da tutti gli schermi: resta sul bordo dello schermo in cui eravamo.
        px, py = prev
        best = rects[0]
        for r in rects:
            if r[0] <= px < r[2] and r[1] <= py < r[3]:
                best = r
                break
        x0, y0, x1, y1 = best
        return min(max(x, x0), x1 - 1), min(max(y, y0), y1 - 1)

    def _wake(self):
        if self._display_asleep:
            self._display_asleep = False
            subprocess.Popen(["caffeinate", "-u", "-t", "1"])

    # --- mouse ---

    def move(self, dx, dy):
        self._wake()
        prev = self._location()
        x, y = self._clamp(prev[0] + dx, prev[1] + dy, prev)
        self._pos = (x, y)
        if "left" in self._pressed:
            button, _, _, etype = _BUTTONS["left"]
        elif "right" in self._pressed:
            button, _, _, etype = _BUTTONS["right"]
        else:
            button, etype = 0, kCGEventMouseMoved
        ev = CGEventCreateMouseEvent(None, etype, CGPoint(x, y), button)
        CGEventSetIntegerValueField(ev, kCGMouseEventDeltaX, int(round(dx)))
        CGEventSetIntegerValueField(ev, kCGMouseEventDeltaY, int(round(dy)))
        _post(ev)

    def _mouse_event(self, button, down, click_state):
        code, down_type, up_type, _ = _BUTTONS[button]
        x, y = self._location()
        ev = CGEventCreateMouseEvent(None, down_type if down else up_type, CGPoint(x, y), code)
        CGEventSetIntegerValueField(ev, kCGMouseEventClickState, click_state)
        _post(ev)

    def click(self, button):
        self._wake()
        x, y = self._location()
        now = time.monotonic()
        count = 1
        last = self._last_click
        if (
            last and last[0] == button and now - last[1] < DOUBLE_CLICK_TIME
            and abs(x - last[2]) < DOUBLE_CLICK_DISTANCE and abs(y - last[3]) < DOUBLE_CLICK_DISTANCE
        ):
            count = min(last[4] + 1, 3)
        self._last_click = (button, now, x, y, count)
        self._mouse_event(button, True, count)
        self._mouse_event(button, False, count)

    def button(self, button, down):
        self._wake()
        if down:
            self._pressed.add(button)
        else:
            self._pressed.discard(button)
        self._last_click = None
        self._mouse_event(button, down, 1)

    def scroll(self, dx, dy):
        self._wake()
        self._scroll_rest[0] += dx
        self._scroll_rest[1] += dy
        ix, iy = int(self._scroll_rest[0]), int(self._scroll_rest[1])
        if not ix and not iy:
            return
        self._scroll_rest[0] -= ix
        self._scroll_rest[1] -= iy
        _post(CGEventCreateScrollWheelEvent2(None, kCGScrollEventUnitPixel, 2, iy, ix, 0))

    # --- tastiera ---

    def _key_event(self, keycode, down, flags):
        ev = CGEventCreateKeyboardEvent(None, keycode, down)
        CGEventSetFlags(ev, flags)
        _post(ev)

    def key(self, name, mods):
        self._wake()
        flags = 0
        held = []
        for m in mods:
            code, flag = MODIFIERS[m]
            flags |= flag
            held.append(code)
            self._key_event(code, True, flags)
        keycode = KEYCODES[name]
        key_flags = flags | EXTRA_FLAGS.get(name, 0)
        self._key_event(keycode, True, key_flags)
        self._key_event(keycode, False, key_flags)
        for m, code in zip(reversed(list(mods)), reversed(held)):
            flags &= ~MODIFIERS[m][1]
            self._key_event(code, False, flags)

    def text(self, s):
        self._wake()
        for ch in s:
            if ch == "\n":
                self.key("return", [])
                continue
            units = ch.encode("utf-16-le")
            n = len(units) // 2
            buf = (c_uint16 * n).from_buffer_copy(units)
            for down in (True, False):
                ev = CGEventCreateKeyboardEvent(None, 0, down)
                CGEventSetFlags(ev, 0)
                CGEventKeyboardSetUnicodeString(ev, n, buf)
                _post(ev)

    def media(self, name):
        self._wake()
        code = MEDIA_KEYS[name]
        for down in (True, False):
            state = 0xA if down else 0xB
            pool = objc_autoreleasePoolPush()
            try:
                ns_event = _msg_other_event(
                    _NSEvent, _SEL_OTHER_EVENT,
                    NSEventTypeSystemDefined, CGPoint(0, 0), state << 8, 0.0, 0, None,
                    8, (code << 16) | (state << 8), -1,
                )
                if ns_event:
                    cg_event = _msg_get_ptr(ns_event, _SEL_CGEVENT)
                    if cg_event:
                        CGEventPost(kCGHIDEventTap, cg_event)
            finally:
                objc_autoreleasePoolPop(pool)

    # --- cursore grande ---

    def cursor_scale(self, scale):
        """Ingrandisce il cursore del Mac (1 = normale). True se ci è riuscito."""
        try:
            if self._cursor_api is None:
                self._cursor_api = _cursor_scale_api() or False
            if not self._cursor_api:
                return False
            main, get, set_ = self._cursor_api
            cid = main()
            if self._cursor_original is None:
                current = c_float(1.0)
                if get(cid, ctypes.byref(current)) == 0:
                    self._cursor_original = current.value
            target = scale if scale > 1 else (self._cursor_original or 1.0)
            return set_(cid, c_float(target)) == 0
        except Exception:
            return False

    def cursor_restore(self):
        if self._cursor_original is None:
            return
        self.cursor_scale(1)
        self._cursor_original = None

    # --- sistema ---

    def display_sleep(self):
        subprocess.Popen(["pmset", "displaysleepnow"])
        self._display_asleep = True
