// TeleMac.app — l'app per Mac di TeleMac, di Edoardo Ciarlo.
//
// Una finestra normale che mostra il QR da inquadrare con l'iPhone e il codice
// di abbinamento. Il server (HTTPS + WebSocket, certificati, abbinamento,
// controllo di mouse e tastiera) è codice Swift dentro l'app, negli altri file di
// questa cartella: niente Python, niente processi di sfondo. La finestra è
// la pagina web/panel.html, servita da dentro l'app con uno schema "telemac://".
//
// Con --headless parte solo il server, senza finestre: serve alle prove automatiche.

import ApplicationServices
import Cocoa
import ServiceManagement
import WebKit

let appName = "TeleMac"
let signer = "Edoardo Ciarlo"
let legacyAgentLabel = "io.github.troppoganzo.telemac"
let windowBackground = NSColor(srgbRed: 0x03 / 255.0, green: 0x11 / 255.0, blue: 0x1a / 255.0, alpha: 1)
let accessibilitySettingsURL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"

// MARK: - Registro

/// Il registro è ~/Library/Logs/TeleMac.log; oltre 1 MB si riparte da zero.
enum AppLog {
    static let url = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/TeleMac.log")
    private static let handle: FileHandle? = {
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let size = (try? FileManager.default.attributesOfItem(atPath: url.path))?[.size] as? NSNumber, size.intValue > 1_000_000 {
            try? FileManager.default.removeItem(at: url)
        }
        if !FileManager.default.fileExists(atPath: url.path) { FileManager.default.createFile(atPath: url.path, contents: nil) }
        let h = try? FileHandle(forWritingTo: url)
        h?.seekToEndOfFile()
        return h
    }()

    static func write(_ text: String) {
        guard let data = text.data(using: .utf8) else { return }
        handle?.write(data)
    }

    static func tail(maxBytes: Int = 1500) -> String {
        guard let data = try? Data(contentsOf: url) else { return "" }
        return String(decoding: data.suffix(maxBytes), as: UTF8.self)
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
    var server: TeleMacServer?
    let panelHandler = PanelSchemeHandler(webDir: Bundle.main.resourceURL!.appendingPathComponent("web"))
    var panelURL: URL?
    var launchedAtLogin = false
    var hiddenBeforeCode = false
    var axTimer: Timer?
    var lastAxTrusted: Bool?

    var version: String {
        (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "5.0"
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
        if Installer.installIfNeeded(log: { text in AppLog.write(text) }) {
            showMessage(title: "Installo TeleMac…", body: "La sposto in Applicazioni, come un'app normale.")
            showWindow()
            return
        }
        Installer.cleanUpAfterLaunch(log: { text in
            DispatchQueue.main.async { AppLog.write(text) }
        })
        removeLegacyLaunchAgent()
        showMessage(title: "Avvio di TeleMac…", body: "Un attimo, sto accendendo il telecomando.")
        if !launchedAtLogin {
            showWindow()
        }

        // Assicura che la dimensione del cursore sia normale (1.0).
        MacBackend().cursorRestore()

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
        server?.stop()
        MacBackend().cursorRestore()
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
        config.setURLSchemeHandler(panelHandler, forURLScheme: "telemac")
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
        server?.stop()
        var options = TeleMacServer.Options(webDir: Bundle.main.resourceURL!.appendingPathComponent("web"))
        options.dryRun = ProcessInfo.processInfo.environment["TELEMAC_DRY_RUN"] != nil
        let new = TeleMacServer(options)
        new.onEvent = { [weak self] event in self?.handleServerEvent(event) }
        server = new
        panelHandler.server = new
        AppLog.write("\n--- \(Date()) avvio del server\n")
        new.start()
    }

    func handleServerEvent(_ event: TeleMacServer.Event) {
        switch event {
        case .ready:
            let url = URL(string: "telemac://panel/")!
            panelURL = url
            webView.load(URLRequest(url: url))
        case .pairCode:
            // Il codice deve comparire sullo schermo (quindi sulla TV) anche
            // se la finestra era nascosta.
            hiddenBeforeCode = !window.isVisible || !NSApp.isActive
            showWindow()
        case .paired:
            if hiddenBeforeCode {
                hiddenBeforeCode = false
                DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
                    self?.window.orderOut(nil)
                    NSApp.hide(nil)
                }
            }
        case .accessibility:
            pushNativeState()
        case .pairDone:
            break
        case .portInUse:
            AppLog.write("Porta occupata: un'altra copia di TeleMac è già accesa\n")
            showMessage(
                title: "TeleMac è già acceso",
                body: "Un'altra copia di TeleMac (o un altro programma) sta già usando la porta. Chiudila e tocca Riprova.",
                buttons: [("Riprova", "restart")])
            showWindow()
        case .failed(let message):
            AppLog.write("Errore del server: \(message)\n")
            showMessage(
                title: "Il server si è fermato",
                body: "Qualcosa è andato storto. Dettagli qui sotto (il registro completo è in ~/Library/Logs/TeleMac.log).",
                detail: message,
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
            showMessage(title: "Avvio di TeleMac…", body: "Un attimo, sto accendendo il telecomando.")
            startServer()
        case "openLog":
            NSWorkspace.shared.open(AppLog.url)
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
        AppLog.write("Rimosso il vecchio LaunchAgent \(plist.path)\n")
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

if CommandLine.arguments.contains("--headless") { Headless.run(CommandLine.arguments) }

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
