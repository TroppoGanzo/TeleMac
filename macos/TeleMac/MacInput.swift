// Mouse, tastiera e cursore del Mac tramite CoreGraphics. Serve il permesso di
// Accessibilità per TeleMac (Impostazioni di Sistema → Privacy e sicurezza).
//
// Tutte le chiamate arrivano dalla coda del server: niente lucchetti qui.

import AppKit
import ApplicationServices
import CoreGraphics

/// Quello che il server sa fare con mouse e tastiera. Due versioni: `MacBackend`
/// (vera) e `DryRunBackend` (stampa soltanto, per le prove).
protocol InputBackend: AnyObject {
    func move(_ dx: Double, _ dy: Double)
    func scroll(_ dx: Double, _ dy: Double)
    func click(_ button: String)
    func button(_ button: String, down: Bool)
    func key(_ name: String, mods: [String])
    func text(_ text: String)
    func media(_ name: String)
    func displaySleep()
    func cursorScale(_ scale: Double) -> Bool
    func cursorRestore()
}

/// Non tocca niente: scrive i comandi ricevuti su stdout, nel formato "  [prova] ...".
final class DryRunBackend: InputBackend {
    private func log(_ parts: Any...) {
        print("  [prova] " + parts.map { "\($0)" }.joined(separator: " "))
        fflush(stdout)
    }
    private func r(_ v: Double) -> Double { (v * 10).rounded() / 10 }

    func move(_ dx: Double, _ dy: Double) { log("move", r(dx), r(dy)) }
    func scroll(_ dx: Double, _ dy: Double) { log("scroll", r(dx), r(dy)) }
    func click(_ button: String) { log("click", button) }
    func button(_ button: String, down: Bool) { log("button", button, down ? "down" : "up") }
    func key(_ name: String, mods: [String]) { log("key", (mods + [name]).joined(separator: "+")) }
    func text(_ text: String) { log("text", text.debugDescription) }
    func media(_ name: String) { log("media", name) }
    func displaySleep() { log("display_sleep") }
    func cursorScale(_ scale: Double) -> Bool { log("cursor_scale", scale); return true }
    func cursorRestore() { log("cursor_restore") }
}

/// Permesso di Accessibilità. Con `prompt` macOS mostra la richiesta.
enum Accessibility {
    static func isTrusted(prompt: Bool = false) -> Bool {
        if !prompt { return AXIsProcessTrusted() }
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        return AXIsProcessTrustedWithOptions(options)
    }
}

private func now() -> Double { ProcessInfo.processInfo.systemUptime }

final class MacBackend: InputBackend {
    private struct ButtonSpec {
        let button: CGMouseButton
        let down: CGEventType
        let up: CGEventType
        let drag: CGEventType
    }
    private let buttons: [String: ButtonSpec] = [
        "left": ButtonSpec(button: .left, down: .leftMouseDown, up: .leftMouseUp, drag: .leftMouseDragged),
        "right": ButtonSpec(button: .right, down: .rightMouseDown, up: .rightMouseUp, drag: .rightMouseDragged),
    ]
    private static let doubleClickTime = 0.5
    private static let doubleClickDistance = 6.0

    private var pos: CGPoint?
    private var posTime = 0.0
    private var pressed = Set<String>()
    private var lastClick: (button: String, time: Double, x: Double, y: Double, count: Int64)?
    private var displays: [CGRect] = []
    private var displaysTime = 0.0
    private var scrollRest = (x: 0.0, y: 0.0)
    private var displayAsleep = false

    private var cursorAPI: CursorAPI??  // nil = non cercato; .some(nil) = non c'è
    private var cursorOriginal: Float?  // dimensione del cursore prima che la cambiassimo
    private var cursorTarget: Float?    // dimensione voluta dal telecomando (nil = non toccare)
    private var cursorChecked = 0.0

    // MARK: Posizione del puntatore

    private func realLocation() -> CGPoint {
        CGEvent(source: nil)?.location ?? .zero
    }

    /// Teniamo una posizione nostra (con i decimali) per movimenti fluidi; la
    /// risincronizziamo con quella reale se non ci muoviamo da un po'.
    private func location() -> CGPoint {
        let t = now()
        if pos == nil || t - posTime > 0.4 { pos = realLocation() }
        posTime = t
        return pos!
    }

    private func displayRects() -> [CGRect] {
        let t = now()
        if displays.isEmpty || t - displaysTime > 3 {
            var ids = [CGDirectDisplayID](repeating: 0, count: 16)
            var count: UInt32 = 0
            if CGGetActiveDisplayList(16, &ids, &count) == .success {
                displays = ids.prefix(Int(count)).map { CGDisplayBounds($0) }
            }
            displaysTime = t
        }
        return displays
    }

    private func clamp(_ p: CGPoint, previous prev: CGPoint) -> CGPoint {
        let rects = displayRects()
        if rects.isEmpty { return p }
        if rects.contains(where: { $0.contains(p) }) { return p }
        // Fuori da tutti gli schermi: resta sul bordo dello schermo in cui eravamo.
        let best = rects.first(where: { $0.contains(prev) }) ?? rects[0]
        return CGPoint(x: min(max(p.x, best.minX), best.maxX - 1), y: min(max(p.y, best.minY), best.maxY - 1))
    }

    private func wake() {
        guard displayAsleep else { return }
        displayAsleep = false
        spawn("/usr/bin/caffeinate", ["-u", "-t", "1"])
    }

    private func spawn(_ path: String, _ args: [String]) {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: path)
        proc.arguments = args
        try? proc.run()
    }

    // MARK: Mouse

    func move(_ dx: Double, _ dy: Double) {
        wake()
        keepCursorBig()
        let prev = location()
        let p = clamp(CGPoint(x: prev.x + dx, y: prev.y + dy), previous: prev)
        pos = p
        let type: CGEventType
        let button: CGMouseButton
        if pressed.contains("left") { type = .leftMouseDragged; button = .left }
        else if pressed.contains("right") { type = .rightMouseDragged; button = .right }
        else { type = .mouseMoved; button = .left }
        guard let ev = CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: p, mouseButton: button) else { return }
        ev.setIntegerValueField(.mouseEventDeltaX, value: Int64(dx.rounded()))
        ev.setIntegerValueField(.mouseEventDeltaY, value: Int64(dy.rounded()))
        ev.post(tap: .cghidEventTap)
    }

    private func mouseEvent(_ name: String, down: Bool, clickState: Int64) {
        guard let spec = buttons[name] else { return }
        let p = location()
        guard let ev = CGEvent(mouseEventSource: nil, mouseType: down ? spec.down : spec.up,
                               mouseCursorPosition: p, mouseButton: spec.button) else { return }
        ev.setIntegerValueField(.mouseEventClickState, value: clickState)
        ev.post(tap: .cghidEventTap)
    }

    func click(_ button: String) {
        wake()
        let p = location()
        let t = now()
        var count: Int64 = 1
        if let last = lastClick, last.button == button, t - last.time < Self.doubleClickTime,
           abs(p.x - last.x) < Self.doubleClickDistance, abs(p.y - last.y) < Self.doubleClickDistance {
            count = min(last.count + 1, 3)
        }
        lastClick = (button, t, p.x, p.y, count)
        mouseEvent(button, down: true, clickState: count)
        mouseEvent(button, down: false, clickState: count)
    }

    func button(_ button: String, down: Bool) {
        wake()
        if down { pressed.insert(button) } else { pressed.remove(button) }
        lastClick = nil
        mouseEvent(button, down: down, clickState: 1)
    }

    func scroll(_ dx: Double, _ dy: Double) {
        wake()
        scrollRest.x += dx
        scrollRest.y += dy
        let ix = Int32(scrollRest.x.rounded(.towardZero))
        let iy = Int32(scrollRest.y.rounded(.towardZero))
        if ix == 0 && iy == 0 { return }
        scrollRest.x -= Double(ix)
        scrollRest.y -= Double(iy)
        CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 2, wheel1: iy, wheel2: ix, wheel3: 0)?
            .post(tap: .cghidEventTap)
    }

    // MARK: Tastiera

    private func keyEvent(_ code: CGKeyCode, down: Bool, flags: CGEventFlags) {
        guard let ev = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down) else { return }
        ev.flags = flags
        ev.post(tap: .cghidEventTap)
    }

    func key(_ name: String, mods: [String]) {
        guard let keycode = Keys.codes[name] else { return }
        wake()
        var flags: CGEventFlags = []
        var held: [(CGKeyCode, CGEventFlags)] = []
        for m in mods {
            guard let mod = Keys.modifiers[m] else { continue }
            flags.insert(mod.flag)
            held.append(mod)
            keyEvent(mod.code, down: true, flags: flags)
        }
        let keyFlags = flags.union(Keys.extraFlags[name] ?? [])
        keyEvent(keycode, down: true, flags: keyFlags)
        keyEvent(keycode, down: false, flags: keyFlags)
        for mod in held.reversed() {
            flags.remove(mod.1)
            keyEvent(mod.0, down: false, flags: flags)
        }
    }

    func text(_ text: String) {
        wake()
        for ch in text {
            if ch.isNewline {
                key("return", mods: [])
                continue
            }
            let units = Array(String(ch).utf16)
            for down in [true, false] {
                guard let ev = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: down) else { continue }
                ev.flags = []
                ev.keyboardSetUnicodeString(stringLength: units.count, unicodeString: units)
                ev.post(tap: .cghidEventTap)
            }
        }
    }

    func media(_ name: String) {
        guard let code = Keys.media[name] else { return }
        wake()
        for down in [true, false] {
            let state = down ? 0xA : 0xB
            let event = NSEvent.otherEvent(
                with: .systemDefined, location: .zero,
                modifierFlags: NSEvent.ModifierFlags(rawValue: UInt(state << 8)),
                timestamp: 0, windowNumber: 0, context: nil,
                subtype: 8, data1: (code << 16) | (state << 8), data2: -1)
            event?.cgEvent?.post(tap: .cghidEventTap)
        }
    }

    // MARK: Cursore grande

    /// Funzioni (non documentate) che regolano la dimensione del cursore: le
    /// stesse di Impostazioni → Accessibilità → Dimensioni del puntatore. Stanno
    /// in CoreGraphics (macOS più vecchi) o in SkyLight (più recenti).
    private struct CursorAPI {
        let mainConnection: @convention(c) () -> Int32
        let get: @convention(c) (Int32, UnsafeMutablePointer<Float>) -> Int32
        let set: @convention(c) (Int32, Float) -> Int32
    }

    private static func findCursorAPI() -> CursorAPI? {
        let names = [
            ("CGSMainConnectionID", "CGSGetCursorScale", "CGSSetCursorScale"),
            ("SLSMainConnectionID", "SLSGetCursorScale", "SLSSetCursorScale"),
        ]
        let libs = [
            dlopen("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics", RTLD_LAZY),
            dlopen("/System/Library/PrivateFrameworks/SkyLight.framework/SkyLight", RTLD_LAZY),
        ]
        for lib in libs.compactMap({ $0 }) {
            for (main, get, set) in names {
                guard let m = dlsym(lib, main), let g = dlsym(lib, get), let s = dlsym(lib, set) else { continue }
                return CursorAPI(
                    mainConnection: unsafeBitCast(m, to: (@convention(c) () -> Int32).self),
                    get: unsafeBitCast(g, to: (@convention(c) (Int32, UnsafeMutablePointer<Float>) -> Int32).self),
                    set: unsafeBitCast(s, to: (@convention(c) (Int32, Float) -> Int32).self))
            }
        }
        return nil
    }

    private func api() -> CursorAPI? {
        if cursorAPI == nil { cursorAPI = .some(Self.findCursorAPI()) }
        return cursorAPI!
    }

    func cursorScale(_ scale: Double) -> Bool {
        guard let api = api() else { return false }
        let cid = api.mainConnection()
        let target: Float = scale > 1 ? Float(scale) : 1.0
        cursorTarget = scale > 1 ? Float(scale) : nil
        cursorChecked = now()
        return api.set(cid, target) == 0
    }

    /// Mentre il telecomando muove il cursore, deve restare grande (se richiesto): se macOS (o
    /// un'altra app) l'ha rimesso piccolo, lo ringrandiamo. Controllo leggero,
    /// al massimo due volte al secondo.
    private func keepCursorBig() {
        guard let target = cursorTarget, let api = api() else { return }
        let t = now()
        if t - cursorChecked < 0.5 { return }
        cursorChecked = t
        let cid = api.mainConnection()
        var current: Float = 0
        if api.get(cid, &current) != 0 || abs(current - target) > 0.05 {
            _ = api.set(cid, target)
        }
    }

    func cursorRestore() {
        cursorTarget = nil
        guard let api = api() else { return }
        let cid = api.mainConnection()
        _ = api.set(cid, 1.0)
        cursorOriginal = nil
    }

    // MARK: Sistema

    func displaySleep() {
        spawn("/usr/bin/pmset", ["displaysleepnow"])
        displayAsleep = true
    }
}
