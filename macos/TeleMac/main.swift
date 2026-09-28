// TeleMac.app — l'app per Mac di TeleMac, di Edoardo Ciarlo.
//
// Una finestra normale che mostra il QR da inquadrare con l'iPhone e il codice
// di abbinamento. Il "motore" resta il server in Python (Resources/telemac):
// l'app lo avvia in silenzio, senza Terminale e senza icona di Python nel Dock,
// e lo ferma quando si chiude. La finestra è una pagina locale (web/panel.html)
// servita dal server solo su 127.0.0.1 e protetta da un gettone segreto.

import ApplicationServices
import Cocoa
import ServiceManagement
import WebKit

let appName = "TeleMac"
let signer = "Edoardo Ciarlo"
let legacyAgentLabel = "io.github.troppoganzo.telemac"
let windowBackground = NSColor(srgbRed: 0x03 / 255.0, green: 0x11 / 255.0, blue: 0x1a / 255.0, alpha: 1)
let accessibilitySettingsURL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"

// MARK: - Server Python

final class ServerProcess {
    enum Failure {
        case pythonMissing
        case portInUse
        case crashed(String)
    }

    var onEvent: (([String: Any]) -> Void)?
    var onExit: ((Failure?) -> Void)?

    private var process: Process?
    private var stdinPipe: Pipe?
    private var buffer = Data()
    private var stopping = false
    private var portInUse = false
    private let log: FileHandle?
    private let logURL: URL

    init() {
        let logs = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs")
        try? FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
        logURL = logs.appendingPathComponent("TeleMac.log")
        // Il log non deve crescere all'infinito: oltre 1 MB si riparte da zero.
        if let size = (try? FileManager.default.attributesOfItem(atPath: logURL.path))?[.size] as? NSNumber,
           size.intValue > 1_000_000 {
            try? FileManager.default.removeItem(at: logURL)
        }
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        log = try? FileHandle(forWritingTo: logURL)
        log?.seekToEndOfFile()
    }

    static func findPython() -> String? {
        let fm = FileManager.default
        if let custom = ProcessInfo.processInfo.environment["TELEMAC_PYTHON"], fm.isExecutableFile(atPath: custom) {
            return custom
        }
        // /usr/bin/python3 funziona solo se ci sono gli strumenti per sviluppatori
        // (altrimenti apre la finestra per installarli): prima controlliamo.
        let developerPythons = [
            "/Library/Developer/CommandLineTools/usr/bin/python3",
            "/Applications/Xcode.app/Contents/Developer/usr/bin/python3",
        ]
        if developerPythons.contains(where: { fm.isExecutableFile(atPath: $0) }) {
            return "/usr/bin/python3"
        }
        let others = [
            "/opt/homebrew/bin/python3",
            "/usr/local/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
        ]
        return others.first(where: { fm.isExecutableFile(atPath: $0) })
    }

    func writeLog(_ text: String) {
        guard let data = text.data(using: .utf8) else { return }
        log?.write(data)
    }

    func logTail(maxBytes: Int = 1500) -> String {
        guard let data = try? Data(contentsOf: logURL) else { return "" }
        let tail = data.suffix(maxBytes)
        return String(decoding: tail, as: UTF8.self)
    }

    func start(token: String) {
        guard let python = ServerProcess.findPython() else {
            onExit?(.pythonMissing)
            return
        }
        guard let resources = Bundle.main.resourceURL else { return }
        let script = resources.appendingPathComponent("telemac/server.py").path

        stopping = false
        portInUse = false
        buffer = Data()

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: python)
        proc.arguments = [script, "--app"]
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        env["TELEMAC_PANEL_TOKEN"] = token
        // Mai scrivere __pycache__ dentro l'app: romperebbe la firma.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        proc.environment = env

        let out = Pipe()
        let err = Pipe()
        let input = Pipe()
        proc.standardOutput = out
        proc.standardError = err
        proc.standardInput = input
        stdinPipe = input

        out.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            DispatchQueue.main.async { self?.consume(data) }
        }
        err.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            DispatchQueue.main.async { self?.log?.write(data) }
        }
        proc.terminationHandler = { [weak self] p in
            out.fileHandleForReading.readabilityHandler = nil
            err.fileHandleForReading.readabilityHandler = nil
            let status = p.terminationStatus
            DispatchQueue.main.async { self?.finished(p, status: status) }
        }

        writeLog("\n--- \(Date()) avvio del server con \(python)\n")
        do {
            try proc.run()
            process = proc
        } catch {
            writeLog("Impossibile avviare Python: \(error)\n")
            onExit?(.crashed("Impossibile avviare Python: \(error.localizedDescription)"))
        }
    }

    private func consume(_ data: Data) {
        buffer.append(data)
        while let newline = buffer.firstIndex(of: 0x0A) {
            let lineData = buffer.subdata(in: buffer.startIndex..<newline)
            buffer.removeSubrange(buffer.startIndex...newline)
            let line = String(decoding: lineData, as: UTF8.self)
            let prefix = "@telemac "
            if line.hasPrefix(prefix),
               let json = line.dropFirst(prefix.count).data(using: .utf8),
               let event = (try? JSONSerialization.jsonObject(with: json)) as? [String: Any] {
                if event["event"] as? String == "error", event["code"] as? String == "port_in_use" {
                    portInUse = true
                }
                onEvent?(event)
            } else {
                writeLog(line + "\n")
            }
        }
    }

    private func finished(_ proc: Process, status: Int32) {
        guard proc === process else { return }  // un server vecchio, già fermato da stop()
        process = nil
        stdinPipe = nil
        writeLog("--- server terminato (codice \(status))\n")
        if stopping { return }
        if portInUse {
            onExit?(.portInUse)
        } else {
            onExit?(.crashed(logTail()))
        }
    }

    func stop() {
        stopping = true
        guard let proc = process else { return }
        process = nil
        guard proc.isRunning else { return }
        // Chiudere lo stdin basta già a fermarlo; SIGTERM per sicurezza. Il
        // server rimette a posto il cursore prima di uscire.
        try? stdinPipe?.fileHandleForWriting.close()
        proc.terminate()
        let deadline = Date().addingTimeInterval(2)
        while proc.isRunning && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        }
        if proc.isRunning {
            kill(proc.processIdentifier, SIGKILL)
        }
    }
}

// MARK: - Maniglia della finestra

// La barra del titolo è trasparente e la pagina ci passa sotto: senza questa
// fascia invisibile il clic finirebbe alla pagina e la finestra non si
// sposterebbe. Sta sopra la pagina, nei 40 punti in alto (vuoti nel pannello).
final class DragBar: NSView {
    override var mouseDownCanMoveWindow: Bool { true }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func mouseDown(with event: NSEvent) {
        if event.clickCount == 2 {
            // Doppio clic: come la barra del titolo, secondo le Impostazioni di Sistema.
            let action = UserDefaults.standard.string(forKey: "AppleActionOnDoubleClick") ?? "Maximize"
            if action == "Minimize" {
                window?.performMiniaturize(nil)
            } else if action != "None" {
                window?.performZoom(nil)
            }
            return
        }
        window?.performDrag(with: event)
    }
}

// MARK: - App

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKScriptMessageHandler,
    WKNavigationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    let server = ServerProcess()
    var panelURL: URL?
    var launchedAtLogin = false
    var crashCount = 0
    var serverStartedAt = Date()
    var hiddenBeforeCode = false
    var axTimer: Timer?
    var lastAxTrusted: Bool?

    var version: String {
        (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "4.0"
    }

    func applicationWillFinishLaunching(_ notification: Notification) {
        if let event = NSAppleEventManager.shared().currentAppleEvent,
           event.eventID == AEEventID(kAEOpenApplication),
           event.paramDescriptor(forKeyword: AEKeyword(keyAEPropData))?.enumCodeValue == OSType(keyAELaunchedAsLogInItem) {
            launchedAtLogin = true
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        buildWindow()

        // Aperta dal DMG o dai Download: si installa in Applicazioni e si
        // riapre da lì. Questa copia ha finito il suo lavoro.
        if Installer.installIfNeeded(log: { [weak self] text in self?.server.writeLog(text) }) {
            showMessage(title: "Installo TeleMac…", body: "La sposto in Applicazioni, come un'app normale.")
            showWindow()
            return
        }
        Installer.cleanUpAfterLaunch(log: { [weak self] text in
            DispatchQueue.main.async { self?.server.writeLog(text) }
        })
        removeLegacyLaunchAgent()
        showMessage(title: "Avvio di TeleMac…", body: "Un attimo, sto accendendo il telecomando.")
        if !launchedAtLogin {
            showWindow()
        }

        // La prima volta macOS mette TeleMac nell'elenco di Accessibilità e
        // chiede il permesso: senza, il Mac ignorerebbe i comandi dell'iPhone.
        if !AXIsProcessTrusted() {
            let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(options)
        }
        axTimer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            let trusted = AXIsProcessTrusted()
            if trusted != self.lastAxTrusted { self.pushNativeState() }
        }

        server.onEvent = { [weak self] event in self?.handleServerEvent(event) }
        server.onExit = { [weak self] failure in self?.handleServerExit(failure) }
        startServer()
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showWindow()
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        false  // chiudere la finestra non spegne il telecomando: per quello c'è Esci
    }

    func applicationWillTerminate(_ notification: Notification) {
        axTimer?.invalidate()
        server.stop()
    }

    // MARK: Finestra

    func buildWindow() {
        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 460, height: 780),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered, defer: false)
        window.title = appName
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.backgroundColor = windowBackground
        window.appearance = NSAppearance(named: .darkAqua)
        window.minSize = NSSize(width: 400, height: 560)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.setFrameAutosaveName("TeleMacMain")
        if !window.setFrameUsingName("TeleMacMain") {
            window.center()
        }

        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "telemac")
        webView = WKWebView(frame: window.contentView!.bounds, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        window.contentView!.addSubview(webView)

        let barHeight: CGFloat = 40
        let bounds = window.contentView!.bounds
        let dragBar = DragBar(frame: NSRect(x: 0, y: bounds.height - barHeight, width: bounds.width, height: barHeight))
        dragBar.autoresizingMask = [.width, .minYMargin]
        window.contentView!.addSubview(dragBar, positioned: .above, relativeTo: webView)
    }

    func showWindow() {
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        sender.orderOut(nil)  // nascondi soltanto: il server resta acceso
        return false
    }

    // MARK: Server

    func startServer() {
        serverStartedAt = Date()
        server.start(token: UUID().uuidString + UUID().uuidString)
    }

    func handleServerEvent(_ event: [String: Any]) {
        switch event["event"] as? String {
        case "ready":
            if let s = event["panel"] as? String, let url = URL(string: s) {
                panelURL = url
                webView.load(URLRequest(url: url))
            }
        case "pair_code":
            // Il codice deve comparire sullo schermo (quindi sulla TV) anche
            // se la finestra era nascosta.
            hiddenBeforeCode = !window.isVisible || !NSApp.isActive
            showWindow()
        case "paired":
            if hiddenBeforeCode {
                hiddenBeforeCode = false
                DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
                    self?.window.orderOut(nil)
                    NSApp.hide(nil)
                }
            }
        default:
            break
        }
    }

    func handleServerExit(_ failure: ServerProcess.Failure?) {
        guard let failure = failure else { return }
        switch failure {
        case .pythonMissing:
            showMessage(
                title: "Manca Python",
                body: "TeleMac usa Python, che su macOS arriva con gli “strumenti per sviluppatori”. Installali (gratis, ci vogliono pochi minuti) e poi tocca Riprova.",
                buttons: [("Installa gli strumenti", "installTools"), ("Riprova", "restart")])
            showWindow()
        case .portInUse:
            showMessage(
                title: "TeleMac è già acceso",
                body: "Un'altra copia di TeleMac (per esempio avviata dal Terminale) sta già usando la rete. Chiudila e tocca Riprova.",
                buttons: [("Riprova", "restart")])
            showWindow()
        case .crashed(let tail):
            // Se era partito bene e poi si è fermato, lo riaccendiamo da soli (poche volte).
            if Date().timeIntervalSince(serverStartedAt) > 20 && crashCount < 3 {
                crashCount += 1
                startServer()
                return
            }
            showMessage(
                title: "Il server si è fermato",
                body: "Qualcosa è andato storto. Dettagli qui sotto (il registro completo è in ~/Library/Logs/TeleMac.log).",
                detail: tail,
                buttons: [("Riprova", "restart"), ("Apri il registro", "openLog")])
            showWindow()
        }
    }

    // Pagina semplice (avvio, errori) disegnata qui, con lo stesso stile del pannello.
    func showMessage(title: String, body: String, detail: String? = nil, buttons: [(String, String)] = []) {
        func esc(_ s: String) -> String {
            s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;")
                .replacingOccurrences(of: ">", with: "&gt;").replacingOccurrences(of: "\"", with: "&quot;")
        }
        let buttonsHTML = buttons.enumerated().map { index, button in
            "<button class=\"\(index == 0 ? "primary" : "")\" onclick=\"send('\(button.1)')\">\(esc(button.0))</button>"
        }.joined()
        let detailHTML = detail.map { "<pre>\(esc($0))</pre>" } ?? ""
        let html = """
        <!doctype html><html lang="it"><head><meta charset="utf-8"><style>
        body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
          background:radial-gradient(90% 55% at 50% -8%,rgba(26,184,239,.22),transparent 70%),#03111a;
          color:#eaf8fe;font:13px/1.5 -apple-system,system-ui,sans-serif;-webkit-user-select:none;cursor:default}
        .box{max-width:360px;padding:40px 24px;text-align:center}
        .logo{width:56px;height:56px;border-radius:14px;margin:0 auto 18px;
          background:linear-gradient(135deg,#5fddff,#0a7cc9);box-shadow:0 6px 18px rgba(0,0,0,.35)}
        h1{font-size:18px;margin:0 0 8px}p{color:#86a8ba;margin:0 0 18px}
        pre{text-align:left;white-space:pre-wrap;background:#0b2331;color:#86a8ba;border-radius:10px;
          padding:10px;font:11px ui-monospace,Menlo,monospace;max-height:180px;overflow:auto;-webkit-user-select:text}
        button{font:inherit;font-weight:600;border:none;border-radius:8px;padding:7px 14px;margin:4px;
          background:#133247;color:#eaf8fe;cursor:pointer}
        button.primary{background:#1ab8ef;color:#012332}
        .spin{width:22px;height:22px;margin:0 auto;border-radius:50%;border:3px solid #133247;
          border-top-color:#1ab8ef;animation:s 0.9s linear infinite}@keyframes s{to{transform:rotate(360deg)}}
        </style></head><body><div class="box"><div class="logo"></div><h1>\(esc(title))</h1><p>\(esc(body))</p>
        \(detailHTML)\(buttons.isEmpty ? "<div class=\"spin\"></div>" : buttonsHTML)</div>
        <script>function send(a){window.webkit.messageHandlers.telemac.postMessage({action:a})}</script>
        </body></html>
        """
        panelURL = nil
        webView.loadHTMLString(html, baseURL: nil)
    }

    // MARK: Ponte pagina ↔ app

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
        switch action {
        case "hello":
            pushNativeState()
        case "openAccessibility":
            let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(options)
            if let url = URL(string: accessibilitySettingsURL) { NSWorkspace.shared.open(url) }
        case "setLoginItem":
            setLoginItem(enabled: body["enabled"] as? Bool ?? false)
        case "revealProfile":
            let profile = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent(".telemac/TeleMac.mobileconfig")
            NSWorkspace.shared.activateFileViewerSelecting([profile])
        case "restart":
            crashCount = 0
            showMessage(title: "Avvio di TeleMac…", body: "Un attimo, sto accendendo il telecomando.")
            server.stop()
            startServer()
        case "installTools":
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select")
            proc.arguments = ["--install"]
            try? proc.run()
        case "openLog":
            let logURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/TeleMac.log")
            NSWorkspace.shared.open(logURL)
        default:
            break
        }
    }

    func loginItemState() -> String {
        if #available(macOS 13.0, *) {
            switch SMAppService.mainApp.status {
            case .enabled: return "on"
            case .requiresApproval: return "approval"
            default: return "off"
            }
        }
        return "unsupported"
    }

    func setLoginItem(enabled: Bool) {
        if #available(macOS 13.0, *) {
            do {
                if enabled {
                    try SMAppService.mainApp.register()
                } else {
                    try SMAppService.mainApp.unregister()
                }
            } catch {
                let alert = NSAlert()
                alert.messageText = "Non riesco a cambiare l'avvio automatico"
                alert.informativeText = "Puoi farlo da Impostazioni di Sistema → Generali → Elementi login.\n\n\(error.localizedDescription)"
                alert.runModal()
            }
            if SMAppService.mainApp.status == .requiresApproval {
                SMAppService.openSystemSettingsLoginItems()
            }
        }
        pushNativeState()
    }

    func pushNativeState() {
        let trusted = AXIsProcessTrusted()
        lastAxTrusted = trusted
        let state: [String: Any] = [
            "loginItem": loginItemState(),
            "axTrusted": trusted,
            "version": version,
            "signer": signer,
        ]
        guard panelURL != nil,
              let data = try? JSONSerialization.data(withJSONObject: state),
              let json = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.telemacNativeUpdate && window.telemacNativeUpdate(\(json))",
                                   completionHandler: nil)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        pushNativeState()
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if let url = panelURL { webView.load(URLRequest(url: url)) }
    }

    // MARK: Vecchio avvio automatico

    // Le versioni precedenti usavano un LaunchAgent che avviava Python di
    // nascosto: lo togliamo, se no occuperebbe la porta al posto dell'app.
    func removeLegacyLaunchAgent() {
        let plist = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/\(legacyAgentLabel).plist")
        guard FileManager.default.fileExists(atPath: plist.path) else { return }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        proc.arguments = ["bootout", "gui/\(getuid())/\(legacyAgentLabel)"]
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        try? proc.run()
        proc.waitUntilExit()
        try? FileManager.default.removeItem(at: plist)
        server.writeLog("Rimosso il vecchio LaunchAgent \(plist.path)\n")
    }

    // MARK: Menu

    func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Informazioni su \(appName)", action: #selector(showAbout), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Nascondi \(appName)", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let others = appMenu.addItem(withTitle: "Nascondi altre", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        others.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "Mostra tutte", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Esci da \(appName)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem()
        main.addItem(editItem)
        let editMenu = NSMenu(title: "Modifica")
        editMenu.addItem(withTitle: "Taglia", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copia", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Incolla", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Seleziona tutto", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu

        let windowItem = NSMenuItem()
        main.addItem(windowItem)
        let windowMenu = NSMenu(title: "Finestra")
        windowMenu.addItem(withTitle: "Mostra \(appName)", action: #selector(showMainWindow), keyEquivalent: "0")
        windowMenu.addItem(withTitle: "Riduci a icona", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Chiudi finestra", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windowItem.submenu = windowMenu
        NSApp.windowsMenu = windowMenu

        NSApp.mainMenu = main
    }

    @objc func showMainWindow() {
        showWindow()
    }

    @objc func showAbout() {
        let credits = NSAttributedString(
            string: "Telecomando ad aria per il Mac, dall'iPhone.\nFirmata da \(signer).",
            attributes: [
                .font: NSFont.systemFont(ofSize: 11),
                .foregroundColor: NSColor.secondaryLabelColor,
            ])
        NSApp.orderFrontStandardAboutPanel(options: [.credits: credits])
        NSApp.activate(ignoringOtherApps: true)
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
