// Mini server HTTP/HTTPS e WebSocket sopra Network.framework: quanto basta a
// TeleMac (pagine statiche, due POST, un WebSocket) e niente di più.
//
// Tutto gira sulla coda che gli si dà: nessun lucchetto. Ogni risposta HTTP
// chiude la connessione ("Connection: close"), come faceva il server Python.

import CryptoKit
import Foundation
import Network

// MARK: - Richieste e risposte

struct HTTPRequest {
    let method: String
    let path: String
    let query: [String: String]
    let headers: [String: String]  // nomi in minuscolo
    let body: Data
}

enum HTTP {
    static let maxHeaderBytes = 16 * 1024
    static let maxBodyBytes = 1_000_000

    enum Parsed {
        case need            // servono altri byte
        case bad             // richiesta malformata o troppo grande
        case request(HTTPRequest, consumed: Int)
    }

    static func parse(_ data: Data) -> Parsed {
        guard let end = data.range(of: Data([13, 10, 13, 10])) else {
            return data.count > maxHeaderBytes ? .bad : .need
        }
        guard end.lowerBound <= maxHeaderBytes else { return .bad }
        let head = String(decoding: data[data.startIndex..<end.lowerBound], as: UTF8.self)
        var lines = head.components(separatedBy: "\r\n")
        let requestLine = lines.removeFirst().split(separator: " ")
        guard requestLine.count >= 2 else { return .bad }
        var headers: [String: String] = [:]
        for line in lines {
            guard let colon = line.firstIndex(of: ":") else { continue }
            headers[line[..<colon].lowercased()] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }
        let length = Int(headers["content-length"] ?? "0") ?? -1
        guard length >= 0, length <= maxBodyBytes else { return .bad }
        let bodyStart = end.upperBound
        guard data.count - (bodyStart - data.startIndex) >= length else { return .need }

        let target = String(requestLine[1])
        let comps = URLComponents(string: "http://x" + (target.hasPrefix("/") ? target : "/" + target))
        var query: [String: String] = [:]
        for item in comps?.queryItems ?? [] where query[item.name] == nil { query[item.name] = item.value ?? "" }
        let request = HTTPRequest(
            method: String(requestLine[0]), path: comps?.path ?? "/", query: query, headers: headers,
            body: Data(data[bodyStart..<(bodyStart + length)]))
        return .request(request, consumed: bodyStart - data.startIndex + length)
    }

    private static let reasons = [
        101: "Switching Protocols", 200: "OK", 400: "Bad Request", 403: "Forbidden",
        404: "Not Found", 429: "Too Many Requests", 500: "Internal Server Error",
    ]

    static func response(_ status: Int, contentType: String, body: Data, headers: [(String, String)] = []) -> Data {
        var head = "HTTP/1.1 \(status) \(reasons[status] ?? "OK")\r\n"
        head += "Content-Type: \(contentType)\r\nContent-Length: \(body.count)\r\nConnection: close\r\n"
        for (k, v) in headers { head += "\(k): \(v)\r\n" }
        head += "\r\n"
        return Data(head.utf8) + body
    }

    static func json(_ object: Any, status: Int = 200, headers: [(String, String)] = []) -> Data {
        let body = (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{}".utf8)
        return response(status, contentType: "application/json; charset=utf-8", body: body, headers: headers)
    }

    static func text(_ message: String, status: Int = 404) -> Data {
        response(status, contentType: "text/plain; charset=utf-8", body: Data(message.utf8))
    }

    static func contentType(forExtension ext: String) -> String {
        switch ext.lowercased() {
        case "html": return "text/html; charset=utf-8"
        case "js": return "text/javascript; charset=utf-8"
        case "css": return "text/css; charset=utf-8"
        case "json", "webmanifest": return "application/json; charset=utf-8"
        case "png": return "image/png"
        case "svg": return "image/svg+xml"
        case "ico": return "image/x-icon"
        default: return "application/octet-stream"
        }
    }
}

// MARK: - Connessione

final class Conn {
    let nw: NWConnection
    private let queue: DispatchQueue
    var buffer = Data()
    var onData: (() -> Void)?
    var onClose: (() -> Void)?
    private(set) var closed = false
    private var idle: DispatchSourceTimer?
    private let idleTimeout: TimeInterval

    init(_ nw: NWConnection, queue: DispatchQueue, idleTimeout: TimeInterval = 30) {
        self.nw = nw
        self.queue = queue
        self.idleTimeout = idleTimeout
    }

    func start() {
        nw.stateUpdateHandler = { [weak self] state in
            switch state {
            case .failed, .cancelled: self?.close()
            default: break
            }
        }
        nw.start(queue: queue)
        resetIdle()
        receive()
    }

    private func resetIdle() {
        idle?.cancel()
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + idleTimeout)
        timer.setEventHandler { [weak self] in self?.close() }
        timer.resume()
        idle = timer
    }

    private func receive() {
        nw.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [weak self] data, _, isComplete, error in
            guard let self = self, !self.closed else { return }
            if let data = data, !data.isEmpty {
                self.buffer.append(data)
                self.resetIdle()
                self.onData?()
            }
            if error != nil || isComplete { self.close(); return }
            if !self.closed { self.receive() }
        }
    }

    /// Manda dati; con `thenClose` chiude la connessione dopo l'invio.
    func send(_ data: Data, thenClose: Bool = false) {
        guard !closed else { return }
        if thenClose {
            nw.send(content: data, contentContext: .finalMessage, isComplete: true,
                    completion: .contentProcessed { [weak self] error in
                        guard let self = self, !self.closed else { return }
                        if error != nil { self.close(); return }
                        self.queue.asyncAfter(deadline: .now() + 5) { [weak self] in self?.close() }
                    })
        } else {
            nw.send(content: data, completion: .contentProcessed { [weak self] error in
                if error != nil { self?.close() }
            })
        }
    }

    func close() {
        guard !closed else { return }
        closed = true
        idle?.cancel()
        idle = nil
        nw.cancel()
        let handler = onClose
        onClose = nil
        onData = nil
        handler?()
    }
}

// MARK: - Ascolto

final class HTTPListener {
    enum Failure: Error {
        case portInUse(UInt16)
        case other(String)
    }

    private let queue: DispatchQueue
    private var listener: NWListener?
    private var conns: [ObjectIdentifier: Conn] = [:]
    private let route: (HTTPRequest, Conn) -> Void
    private(set) var port: UInt16 = 0
    var onFailure: ((Failure) -> Void)?

    /// `identity` presente = HTTPS; assente = HTTP semplice.
    init(queue: DispatchQueue, identity: SecIdentity?, route: @escaping (HTTPRequest, Conn) -> Void) {
        self.queue = queue
        self.route = route
        self.identity = identity
    }
    private let identity: SecIdentity?

    func start(port requested: UInt16, ready: @escaping (UInt16) -> Void) throws {
        let tcp = NWProtocolTCP.Options()
        tcp.noDelay = true  // i movimenti del cursore sono piccoli e frequenti: niente attese
        let parameters: NWParameters
        if let identity = identity, let secIdentity = sec_identity_create(identity) {
            let tls = NWProtocolTLS.Options()
            sec_protocol_options_set_local_identity(tls.securityProtocolOptions, secIdentity)
            sec_protocol_options_set_min_tls_protocol_version(tls.securityProtocolOptions, .TLSv12)
            parameters = NWParameters(tls: tls, tcp: tcp)
        } else {
            parameters = NWParameters(tls: nil, tcp: tcp)
        }
        parameters.allowLocalEndpointReuse = true
        let endpointPort = requested == 0 ? NWEndpoint.Port.any : NWEndpoint.Port(rawValue: requested)!
        let listener = try NWListener(using: parameters, on: endpointPort)
        listener.stateUpdateHandler = { [weak self, weak listener] state in
            guard let self = self else { return }
            switch state {
            case .ready:
                self.port = listener?.port?.rawValue ?? requested
                ready(self.port)
            case .failed(let error):
                if case .posix(let code) = error, code == .EADDRINUSE {
                    self.onFailure?(.portInUse(requested))
                } else {
                    self.onFailure?(.other("\(error)"))
                }
            default:
                break
            }
        }
        listener.newConnectionHandler = { [weak self] nw in self?.accept(nw) }
        self.listener = listener
        listener.start(queue: queue)
    }

    func stop() {
        listener?.cancel()
        listener = nil
        for conn in conns.values { conn.close() }
        conns.removeAll()
    }

    private func accept(_ nw: NWConnection) {
        let conn = Conn(nw, queue: queue)
        let id = ObjectIdentifier(conn)
        conns[id] = conn
        conn.onClose = { [weak self] in self?.conns[id] = nil }
        conn.onData = { [weak self, weak conn] in
            guard let self = self, let conn = conn else { return }
            switch HTTP.parse(conn.buffer) {
            case .need:
                break
            case .bad:
                conn.send(HTTP.json(["ok": false, "error": "bad_request"], status: 400), thenClose: true)
            case .request(let request, let consumed):
                conn.buffer = Data(conn.buffer.dropFirst(consumed))
                conn.onData = nil  // da qui decide la rotta (di solito: risponde e chiude)
                self.route(request, conn)
            }
        }
        conn.start()
    }
}

// MARK: - WebSocket (RFC 6455, quanto basta: messaggi testo, un client per connessione)

enum WebSocket {
    static let guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    /// Oltre questa dimensione (per messaggio, anche riassemblato) chiudiamo: ai
    /// nostri comandi bastano poche decine di byte.
    static let maxPayload = 64 * 1024

    static let opText: UInt8 = 0x1
    static let opClose: UInt8 = 0x8
    static let opPing: UInt8 = 0x9
    static let opPong: UInt8 = 0xA

    static func acceptKey(_ clientKey: String) -> String {
        Data(Insecure.SHA1.hash(data: Data((clientKey + guid).utf8))).base64EncodedString()
    }

    /// Risposta di upgrade: deve essere HTTP/1.1, Safari su iOS rifiuta altre versioni.
    static func handshakeResponse(key: String) -> Data {
        Data(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
              + "Sec-WebSocket-Accept: \(acceptKey(key))\r\n\r\n").utf8)
    }

    static func frame(_ opcode: UInt8, _ payload: Data = Data()) -> Data {
        var out = Data([0x80 | opcode])
        let n = payload.count
        if n < 126 {
            out.append(UInt8(n))
        } else if n < 65536 {
            out.append(126)
            out.append(contentsOf: [UInt8(n >> 8), UInt8(n & 0xFF)])
        } else {
            out.append(127)
            out.append(contentsOf: (0..<8).map { UInt8((n >> (8 * (7 - $0))) & 0xFF) })
        }
        return out + payload
    }

    static func jsonFrame(_ object: Any) -> Data {
        frame(opText, (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{}".utf8))
    }

    static func closeFrame(code: UInt16) -> Data {
        frame(opClose, Data([UInt8(code >> 8), UInt8(code & 0xFF)]))
    }

    enum Parsed {
        case need
        case bad
        case frame(fin: Bool, opcode: UInt8, payload: Data, consumed: Int)
    }

    /// Legge un frame dall'inizio del buffer (se completo).
    static func parseFrame(_ buf: Data) -> Parsed {
        let b = [UInt8](buf.prefix(14))
        guard b.count >= 2 else { return .need }
        let fin = b[0] & 0x80 != 0
        let opcode = b[0] & 0x0F
        let masked = b[1] & 0x80 != 0
        var length = Int(b[1] & 0x7F)
        var offset = 2
        if length == 126 {
            guard b.count >= 4 else { return .need }
            length = Int(b[2]) << 8 | Int(b[3])
            offset = 4
        } else if length == 127 {
            guard b.count >= 10 else { return .need }
            var big: UInt64 = 0
            for i in 2..<10 { big = big << 8 | UInt64(b[i]) }
            guard big <= UInt64(maxPayload) else { return .bad }
            length = Int(big)
            offset = 10
        }
        guard length <= maxPayload else { return .bad }
        let maskLength = masked ? 4 : 0
        guard buf.count >= offset + maskLength + length else { return .need }
        let all = [UInt8](buf.prefix(offset + maskLength + length))
        var payload = Array(all[(offset + maskLength)...])
        if masked {
            let mask = Array(all[offset..<(offset + 4)])
            for i in payload.indices { payload[i] ^= mask[i % 4] }
        }
        return .frame(fin: fin, opcode: opcode, payload: Data(payload), consumed: offset + maskLength + length)
    }
}
