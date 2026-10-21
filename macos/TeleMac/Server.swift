// Il server di TeleMac: abbinamento, WebSocket dei comandi, pagine per l'iPhone.
//
// Porta HTTPS (8765): l'app per l'iPhone, /api/ping, /api/pair/*, /ws.
// Porta HTTP (8766): pagina di configurazione, profilo e certificato della CA.
// Tutto gira su una sola coda seriale: stato, backend e connessioni non hanno lucchetti.

import Foundation
import Network

final class TeleMacServer {
    struct Options {
        var appPort: UInt16 = 8765
        var setupPort: UInt16 = 8766
        var dryRun = false
        var stateDir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".telemac")
        var webDir: URL
    }

    enum Event {
        case ready(appPort: UInt16, setupPort: UInt16?)
        case pairCode(String)
        case pairDone
        case paired(String)
        case accessibility(Bool)
        case portInUse(UInt16)
        case failed(String)
    }

    // Limiti dei messaggi dell'iPhone.
    static let moveLimit = 500.0
    static let scrollLimit = 2000.0
    static let textLimit = 2000
    static let cursorScaleMax = 4.0
    static let pairFinishBodyLimit = 4096
    static let pairedBannerSeconds = 15.0
    private static let cursorMark = "cursor-scaled"

    let options: Options
    /// Gli eventi arrivano sul thread principale.
    var onEvent: ((Event) -> Void)?

    private let queue = DispatchQueue(label: "it.edoardociarlo.telemac.server")
    private var running = false

    // Stato (si tocca solo dalla coda)
    private var backend: InputBackend = DryRunBackend()
    private var mover: SmoothMover!
    private var devices: DeviceStore!
    private var pairing: PairingManager!
    private var paths: CertPaths!
    private var hostname = "localhost"
    private var computerName = "Mac"
    private var ip: String?
    private var caFingerprint = ""
    private var accessibility: Bool?
    private var pairingCode: String?
    private var pairingExpires = 0.0
    private var lastPaired: (name: String, at: Double)?
    private var appListener: HTTPListener?
    private var setupListener: HTTPListener?
    private var appPort: UInt16 = 0
    private var setupPort: UInt16?
    private var axTimer: DispatchSourceTimer?
    private var pathMonitor: NWPathMonitor?
    private var cursorHolders: [ObjectIdentifier: Double] = [:]
    private var qrCache: (text: String, svg: String?)?
    private var networkRefresh: DispatchWorkItem?

    private var profileURL: URL { options.stateDir.appendingPathComponent("TeleMac.mobileconfig") }

    init(_ options: Options) { self.options = options }

    private func now() -> Double { ProcessInfo.processInfo.systemUptime }

    private func emit(_ event: Event) {
        guard let onEvent = onEvent else { return }
        DispatchQueue.main.async { onEvent(event) }
    }

    // MARK: Avvio e arresto

    func start() {
        queue.async {
            guard !self.running else { return }
            self.running = true
            do { try self.startLocked() } catch {
                self.running = false
                self.emit(.failed("\(error)"))
            }
        }
    }

    func stop() {
        queue.sync { stopLocked() }
    }

    private func stopLocked() {
        guard running else { return }
        running = false
        axTimer?.cancel(); axTimer = nil
        pathMonitor?.cancel(); pathMonitor = nil
        networkRefresh?.cancel()
        appListener?.stop(); appListener = nil
        setupListener?.stop(); setupListener = nil
        // Il cursore del Mac torna com'era prima di uscire.
        mover?.stop()
        backend.cursorRestore()
    }

    private func startLocked() throws {
        try FileManager.default.createDirectory(at: options.stateDir, withIntermediateDirectories: true)
        hostname = NetInfo.localHostname()
        computerName = NetInfo.computerName()
        ip = NetInfo.lanIPv4()
        try refreshCertificates()

        backend = options.dryRun ? DryRunBackend() : MacBackend()
        mover = SmoothMover(queue: queue, backend: { [unowned self] in self.backend })
        devices = DeviceStore(options.stateDir.appendingPathComponent("devices.json"))
        pairing = PairingManager()
        pairing.onCode = { [weak self] code, secondsLeft in
            guard let self = self else { return }
            self.pairingCode = code
            self.pairingExpires = self.now() + secondsLeft
            if self.options.dryRun { print("@telemac {\"event\":\"pair_code\",\"code\":\"\(code)\"}"); fflush(stdout) }
            self.emit(.pairCode(code))
        }
        pairing.onDone = { [weak self] in
            guard let self = self else { return }
            guard self.pairingCode != nil else { return }
            self.pairingCode = nil
            self.pairingExpires = 0
            self.emit(.pairDone)
        }

        startAccessibilityWatcher()
        try startListeners()
        startNetworkMonitor()
    }

    private func refreshCertificates() throws {
        paths = try Certs.ensure(in: options.stateDir, hostnames: [hostname], ips: ip.map { [$0] } ?? [])
        caFingerprint = try Certs.caFingerprint(paths)
        // Copia del profilo su disco, da mandare all'iPhone con AirDrop in alternativa alla pagina web.
        try? Certs.mobileconfig(paths, computerName: computerName).write(to: profileURL, options: .atomic)
    }

    private func startAccessibilityWatcher() {
        if options.dryRun { accessibility = nil; return }
        accessibility = Accessibility.isTrusted()
        guard accessibility == false else { return }
        // La richiesta del permesso la fa l'app (è lei che compare nell'elenco di
        // Accessibilità): qui aspettiamo che arrivi.
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + 2, repeating: 2)
        timer.setEventHandler { [weak self] in
            guard let self = self, Accessibility.isTrusted() else { return }
            self.accessibility = true
            self.axTimer?.cancel(); self.axTimer = nil
            self.emit(.accessibility(true))
        }
        timer.resume()
        axTimer = timer
    }

    private func startListeners() throws {
        let identity = try Certs.identity(paths)
        let app = HTTPListener(queue: queue, identity: identity) { [unowned self] req, conn in self.routeApp(req, conn) }
        app.onFailure = { [weak self] failure in
            guard let self = self else { return }
            switch failure {
            case .portInUse(let port): self.emit(.portInUse(port))
            case .other(let message): self.emit(.failed(message))
            }
            self.stopLocked()
        }
        let setup = HTTPListener(queue: queue, identity: nil) { [unowned self] req, conn in self.routeSetup(req, conn) }
        appListener = app
        setupListener = setup

        // Annunciamo "pronto" quando l'app è in ascolto e la pagina di configurazione
        // ha una porta o ha rinunciato (la sua porta può essere occupata: l'app
        // funziona lo stesso per chi è già abbinato).
        var appReady: UInt16?
        var setupReady: UInt16?
        var setupGaveUp = false
        var announced = false
        func announce(_ server: TeleMacServer) {
            guard !announced, let appReady = appReady, setupReady != nil || setupGaveUp else { return }
            announced = true
            server.appPort = appReady
            server.setupPort = setupReady
            server.emit(.ready(appPort: appReady, setupPort: setupReady))
        }
        setup.onFailure = { [unowned self] _ in
            setupGaveUp = true
            self.setupPort = nil
            announce(self)
        }
        try app.start(port: options.appPort) { [unowned self] port in appReady = port; announce(self) }
        do {
            try setup.start(port: options.setupPort) { [unowned self] port in
                setupReady = port
                if announced { self.setupPort = port } else { announce(self) }
            }
        } catch {
            setupListener = nil
            setupGaveUp = true
        }
        // Se la porta della pagina non risponde, annunciamo comunque dopo un attimo.
        queue.asyncAfter(deadline: .now() + 1) { [unowned self] in
            setupGaveUp = true
            announce(self)
        }
    }

    /// Se il Mac cambia rete (Wi-Fi, cavo) cambia l'IP: il certificato va rifatto
    /// per coprire il nuovo indirizzo e i server riaperti con quello nuovo.
    private func startNetworkMonitor() {
        let monitor = NWPathMonitor()
        var first = true
        monitor.pathUpdateHandler = { [weak self] _ in
            guard let self = self else { return }
            if first { first = false; return }
            self.networkRefresh?.cancel()
            let work = DispatchWorkItem { [weak self] in self?.networkChanged() }
            self.networkRefresh = work
            self.queue.asyncAfter(deadline: .now() + 2, execute: work)
        }
        monitor.start(queue: queue)
        pathMonitor = monitor
    }

    private func networkChanged() {
        guard running else { return }
        let newIP = NetInfo.lanIPv4()
        let newHost = NetInfo.localHostname()
        guard newIP != ip || newHost != hostname else { return }
        ip = newIP
        hostname = newHost
        appListener?.stop(); setupListener?.stop()
        do {
            try refreshCertificates()
            try startListeners()
        } catch {
            emit(.failed("\(error)"))
        }
    }

    // MARK: Rotte dell'app (HTTPS)

    private func routeApp(_ req: HTTPRequest, _ conn: Conn) {
        switch (req.method, req.path) {
        case ("GET", "/api/ping"):
            conn.send(HTTP.json([
                "ok": true, "name": computerName,
                "accessibility": accessibility.map { $0 as Any } ?? NSNull(), "version": 2,
            ], headers: [("Access-Control-Allow-Origin", "*")]), thenClose: true)
        case ("GET", "/ws"):
            handleWebSocket(req, conn)
        case ("POST", "/api/pair/start"):
            switch pairing.start() {
            case .ok(let expiresIn):
                conn.send(HTTP.json(["ok": true, "expires_in": expiresIn]), thenClose: true)
            case .tooFast:
                conn.send(HTTP.json(["ok": false, "error": "too_fast"], status: 429), thenClose: true)
            }
        case ("POST", "/api/pair/finish"):
            pairFinish(req, conn)
        case ("GET", _), ("HEAD", _):
            serveStatic(req.path, conn)
        default:
            conn.send(HTTP.text("Non trovato."), thenClose: true)
        }
    }

    private func pairFinish(_ req: HTTPRequest, _ conn: Conn) {
        func fail(_ status: Int, _ body: [String: Any]) { conn.send(HTTP.json(body, status: status), thenClose: true) }
        guard req.body.count <= Self.pairFinishBodyLimit,
              let payload = (try? JSONSerialization.jsonObject(with: req.body)) as? [String: Any],
              let code = payload["code"] as? String else {
            return fail(400, ["ok": false, "error": "bad_request"])
        }
        var deviceName = ((payload["device"] as? String) ?? "iPhone").trimmingCharacters(in: .whitespacesAndNewlines)
        if deviceName.isEmpty { deviceName = "iPhone" }
        deviceName = String(deviceName.prefix(100))

        switch pairing.finish(code) {
        case .ok:
            guard let token = try? devices.add(deviceName) else { return fail(500, ["ok": false, "error": "storage"]) }
            lastPaired = (deviceName, now())
            emit(.paired(deviceName))
            conn.send(HTTP.json(["ok": true, "token": token, "name": computerName]), thenClose: true)
        case .failed(let error, let attemptsLeft):
            var body: [String: Any] = ["ok": false, "error": error]
            if let attemptsLeft = attemptsLeft { body["attempts_left"] = attemptsLeft }
            fail(403, body)
        }
    }

    private func serveStatic(_ path: String, _ conn: Conn) {
        var relative = path == "/" ? "index.html" : String(path.dropFirst())
        if relative.hasSuffix("/") { relative += "index.html" }
        if relative == "telecomando" || relative == "remote" { relative = "index.html" }
        let parts = relative.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        let name = parts.last ?? ""
        // Niente "..", niente nomi nascosti, niente le pagine riservate all'app per Mac.
        guard !parts.contains(where: { $0 == ".." || $0.hasPrefix(".") || $0.isEmpty || $0.contains("\0") }),
              name != "setup.html", name != "panel.html" else {
            return conn.send(HTTP.text("Non trovato."), thenClose: true)
        }
        let file = options.webDir.appendingPathComponent(relative)
        guard file.standardizedFileURL.path.hasPrefix(options.webDir.standardizedFileURL.path + "/"),
              let data = try? Data(contentsOf: file) else {
            return conn.send(HTTP.text("Non trovato."), thenClose: true)
        }
        // no-cache: Safari ricontrolla sempre, così dopo un aggiornamento l'iPhone vede subito la versione nuova.
        conn.send(HTTP.response(200, contentType: HTTP.contentType(forExtension: file.pathExtension), body: data,
                                headers: [("Cache-Control", "no-cache")]), thenClose: true)
    }

    // MARK: Rotte della pagina di configurazione (HTTP)

    private func routeSetup(_ req: HTTPRequest, _ conn: Conn) {
        if req.method == "GET" || req.method == "HEAD" {
            switch req.path {
            case "/setup", "/setup.html":
                guard var html = try? String(contentsOf: options.webDir.appendingPathComponent("setup.html"), encoding: .utf8) else {
                    return conn.send(HTTP.text("web/setup.html non trovato."), thenClose: true)
                }
                let config: [String: Any] = [
                    "appUrls": appURLs(), "computerName": computerName,
                    "caFingerprint": caFingerprint, "profileUrl": "/TeleMac.mobileconfig",
                ]
                let json = (try? JSONSerialization.data(withJSONObject: config, options: [.withoutEscapingSlashes])).map { String(decoding: $0, as: UTF8.self) } ?? "{}"
                html = html.replacingOccurrences(of: "\"__TELEMAC_SETUP_CONFIG__\"", with: json.replacingOccurrences(of: "</", with: "<\\/"))
                return conn.send(HTTP.response(200, contentType: "text/html; charset=utf-8", body: Data(html.utf8)), thenClose: true)
            case "/TeleMac.mobileconfig":
                guard let data = try? Certs.mobileconfig(paths, computerName: computerName) else {
                    return conn.send(HTTP.text("Profilo non disponibile.", status: 500), thenClose: true)
                }
                return conn.send(HTTP.response(200, contentType: "application/x-apple-aspen-config", body: data,
                                               headers: [("Content-Disposition", "attachment; filename=\"TeleMac.mobileconfig\"")]),
                                 thenClose: true)
            case "/TeleMac-CA.cer":
                guard let data = try? Certs.caDER(paths) else {
                    return conn.send(HTTP.text("Certificato non disponibile.", status: 500), thenClose: true)
                }
                return conn.send(HTTP.response(200, contentType: "application/x-x509-ca-cert", body: data), thenClose: true)
            default:
                break
            }
        }
        routeApp(req, conn)
    }

    private func appURLs() -> [String] {
        var urls = ["https://\(hostname):\(appPort)/"]
        if let ip = ip { urls.append("https://\(ip):\(appPort)/") }
        return urls
    }

    // MARK: WebSocket

    private final class Session {
        var pressed = Set<String>()
        var message = Data()
        var messageOpcode: UInt8?
    }

    private func handleWebSocket(_ req: HTTPRequest, _ conn: Conn) {
        guard let key = req.headers["sec-websocket-key"], !key.isEmpty,
              req.headers["upgrade"]?.lowercased() == "websocket" else {
            return conn.send(HTTP.text("Serve un upgrade WebSocket.", status: 400), thenClose: true)
        }
        var out = WebSocket.handshakeResponse(key: key)
        guard devices.verify(req.query["token"]) else {
            out += WebSocket.jsonFrame(["type": "auth", "ok": false])
            out += WebSocket.closeFrame(code: 4001)
            return conn.send(out, thenClose: true)
        }
        out += WebSocket.jsonFrame(["type": "hello", "name": computerName,
                                    "accessibility": accessibility.map { $0 as Any } ?? NSNull()])
        conn.send(out)

        let session = Session()
        let previousOnClose = conn.onClose  // quello del listener, che toglie la connessione dall'elenco
        conn.onClose = { [weak self] in
            previousOnClose?()
            self?.releasePressed(session)
        }
        let pump: () -> Void = { [weak self, weak conn] in
            guard let self = self, let conn = conn else { return }
            self.pumpWebSocket(conn, session)
        }
        conn.onData = pump
        pump()  // se il primo frame era già arrivato con la richiesta
    }

    private func pumpWebSocket(_ conn: Conn, _ session: Session) {
        while !conn.closed {
            switch WebSocket.parseFrame(conn.buffer) {
            case .need:
                return
            case .bad:
                return conn.close()
            case .frame(let fin, let opcode, let payload, let consumed):
                conn.buffer = Data(conn.buffer.dropFirst(consumed))
                switch opcode {
                case WebSocket.opClose:
                    return conn.close()
                case WebSocket.opPing:
                    conn.send(WebSocket.frame(WebSocket.opPong, payload))
                    continue
                case WebSocket.opPong:
                    continue
                case 0x0:  // continuazione
                    if session.messageOpcode == nil { return conn.close() }
                default:
                    session.messageOpcode = opcode
                    session.message = Data()
                }
                session.message.append(payload)
                if session.message.count > WebSocket.maxPayload { return conn.close() }
                guard fin else { continue }
                let completed = session.message
                let kind = session.messageOpcode
                session.message = Data()
                session.messageOpcode = nil
                guard kind == WebSocket.opText else { continue }  // i frame binari non sono previsti
                if let reply = handleMessage(String(decoding: completed, as: UTF8.self), session) {
                    conn.send(WebSocket.jsonFrame(reply))
                }
            }
        }
    }

    // MARK: Messaggi dell'iPhone

    private func number(_ value: Any?) -> Double? {
        if let n = value as? NSNumber, n.doubleValue.isFinite { return n.doubleValue }
        if let s = value as? String, let d = Double(s), d.isFinite { return d }
        return nil
    }

    private func clamp(_ value: Double, _ limit: Double) -> Double { max(-limit, min(limit, value)) }

    /// Applica un messaggio testuale al backend. Un messaggio malformato viene
    /// semplicemente ignorato; restituisce l'eventuale risposta per il client.
    private func handleMessage(_ raw: String, _ session: Session) -> [String: Any]? {
        guard let msg = (try? JSONSerialization.jsonObject(with: Data(raw.utf8))) as? [String: Any],
              let kind = msg["type"] as? String else { return nil }
        switch kind {
        case "move":
            guard let dx = number(msg["dx"]), let dy = number(msg["dy"]) else { return nil }
            mover.add(clamp(dx, Self.moveLimit), clamp(dy, Self.moveLimit))
        case "scroll":
            guard let dx = number(msg["dx"]), let dy = number(msg["dy"]) else { return nil }
            backend.scroll(clamp(dx, Self.scrollLimit), clamp(dy, Self.scrollLimit))
        case "click":
            guard let button = msg["button"] as? String, Keys.mouseButtons.contains(button) else { return nil }
            mover.flush()
            backend.click(button)
        case "button":
            guard let button = msg["button"] as? String, Keys.mouseButtons.contains(button),
                  let down = (msg["down"] as? NSNumber)?.boolValue else { return nil }
            mover.flush()
            backend.button(button, down: down)
            if down { session.pressed.insert(button) } else { session.pressed.remove(button) }
        case "key":
            guard let name = msg["name"] as? String, Keys.codes[name] != nil else { return nil }
            let mods = ((msg["mods"] as? [Any]) ?? []).compactMap { $0 as? String }.filter { Keys.modifiers[$0] != nil }
            backend.key(name, mods: mods)
        case "text":
            guard let text = msg["text"] as? String else { return nil }
            backend.text(String(text.prefix(Self.textLimit)))
        case "media":
            guard let name = msg["name"] as? String, Keys.media[name] != nil else { return nil }
            backend.media(name)
        case "sleep":
            backend.displaySleep()
        case "cursor":
            guard let requested = number(msg["scale"]) else { return nil }
            let scale = min(Self.cursorScaleMax, max(1, requested))
            let id = ObjectIdentifier(session)
            if scale > 1 { cursorHolders[id] = scale } else { cursorHolders[id] = nil }
            // Vince la richiesta più grande fra le connessioni ancora aperte.
            let ok = backend.cursorScale(cursorHolders.values.max() ?? 1)
            if scale > 1 { session.pressed.insert(Self.cursorMark) } else { session.pressed.remove(Self.cursorMark) }
            return ["type": "cursor", "ok": ok]
        case "ping":
            return ["type": "pong"]
        default:
            break
        }
        return nil
    }

    /// Quando una connessione finisce: rilascia i tasti del mouse ancora premuti e,
    /// se aveva ingrandito il cursore, lo rimette com'era (se nessun'altra lo vuole).
    private func releasePressed(_ session: Session) {
        for item in session.pressed {
            if item == Self.cursorMark {
                cursorHolders[ObjectIdentifier(session)] = nil
                if let biggest = cursorHolders.values.max() { _ = backend.cursorScale(biggest) } else { backend.cursorRestore() }
            } else {
                backend.button(item, down: false)
            }
        }
        session.pressed.removeAll()
    }

    // MARK: Pannello dell'app

    /// Stato per la finestra dell'app. Si può chiamare da qualunque thread tranne la coda del server.
    func panelStatus() -> [String: Any] {
        queue.sync {
            let t = now()
            var pairingInfo: Any = NSNull()
            if let code = pairingCode, t < pairingExpires {
                pairingInfo = ["code": code, "expiresIn": max(0, Int((pairingExpires - t).rounded(.up)))]
            }
            var pairedInfo: Any = NSNull()
            if let last = lastPaired, t - last.at < Self.pairedBannerSeconds { pairedInfo = ["device": last.name] }

            let setupURL = setupPort.map { "http://\(ip ?? hostname):\($0)/" }
            var qrSvg: Any = NSNull()
            if let url = setupURL {
                if qrCache?.text != url { qrCache = (url, QRCode.svg(url)) }
                qrSvg = qrCache?.svg.map { $0 as Any } ?? NSNull()
            }
            return [
                "ok": true, "computerName": computerName, "hostname": hostname,
                "ip": ip.map { $0 as Any } ?? NSNull(),
                "setupUrl": setupURL.map { $0 as Any } ?? NSNull(),
                "qrSvg": qrSvg, "appUrls": appURLs(), "caFingerprint": caFingerprint,
                "profilePath": profileURL.path,
                "accessibility": accessibility.map { $0 as Any } ?? NSNull(),
                "pairing": pairingInfo, "paired": pairedInfo,
                "devices": devices.devices().map { ["name": $0.name, "addedAt": $0.addedAt] },
                "dryRun": options.dryRun,
            ]
        }
    }

    func forgetDevices() -> Int {
        queue.sync {
            let n = devices.count()
            devices.clear()
            return n
        }
    }
}

// MARK: - Movimento fluido

/// Rende fluido il movimento del cursore.
///
/// Sul Wi-Fi i messaggi dell'iPhone arrivano a raffiche irregolari (il telefono
/// risparmia batteria accorpando i pacchetti): applicandoli come arrivano il
/// cursore andrebbe a scatti. Qui li accumuliamo e li rilasciamo a ritmo
/// costante (~120 volte al secondo) con una curva morbida: ogni passo consuma
/// una parte di quello che resta, quindi il cursore arriva sempre esattamente
/// dove deve, solo senza strappi.
final class SmoothMover {
    static let rate = 120.0   // passi al secondo
    static let tau = 0.03     // costante di tempo della curva (secondi): più alta = più morbido
    static let snap = 0.35    // sotto questo residuo (in punti) lo diamo tutto in una volta

    private let queue: DispatchQueue
    private let backend: () -> InputBackend
    private let clock: () -> Double
    private var pending = (x: 0.0, y: 0.0)
    private var last: Double?
    private var timer: DispatchSourceTimer?

    init(queue: DispatchQueue, backend: @escaping () -> InputBackend,
         clock: @escaping () -> Double = { ProcessInfo.processInfo.systemUptime }) {
        self.queue = queue
        self.backend = backend
        self.clock = clock
    }

    func add(_ dx: Double, _ dy: Double) {
        pending.x += dx
        pending.y += dy
        if timer == nil { startTimer() }
    }

    private func take(_ fractionIn: Double) -> (Double, Double) {
        var fraction = fractionIn
        if abs(pending.x) <= Self.snap && abs(pending.y) <= Self.snap { fraction = 1 }
        let sx = pending.x * fraction, sy = pending.y * fraction
        pending = (pending.x - sx, pending.y - sy)
        return (sx, sy)
    }

    /// Un passo: sposta il cursore della parte di residuo che spetta a `dt`.
    func step(_ dt: Double) {
        let (sx, sy) = take(1 - exp(-max(dt, 0) / Self.tau))
        if sx != 0 || sy != 0 { backend().move(sx, sy) }
    }

    /// Applica subito tutto il residuo (prima di un clic: deve arrivare dove miravi).
    func flush() {
        let (sx, sy) = take(1)
        if sx != 0 || sy != 0 { backend().move(sx, sy) }
    }

    func stop() {
        timer?.cancel()
        timer = nil
        pending = (0, 0)
    }

    private func startTimer() {
        let period = 1 / Self.rate
        last = nil
        let source = DispatchSource.makeTimerSource(queue: queue)
        source.schedule(deadline: .now(), repeating: period, leeway: .milliseconds(1))
        source.setEventHandler { [weak self] in
            guard let self = self else { return }
            let t = self.clock()
            let dt = self.last.map { t - $0 } ?? period
            self.last = t
            self.step(dt)
            if self.pending.x == 0 && self.pending.y == 0 {
                self.timer?.cancel()
                self.timer = nil
            }
        }
        source.resume()
        timer = source
    }
}
