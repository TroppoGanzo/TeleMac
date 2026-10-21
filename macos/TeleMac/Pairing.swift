// Abbinamento con codice a 6 cifre (come l'Apple TV) e archivio dei dispositivi
// abbinati (token permanenti salvati sul Mac, solo come impronta SHA-256).

import CryptoKit
import Foundation

private func secureRandomBytes(_ count: Int) -> [UInt8] {
    var rng = SystemRandomNumberGenerator()
    return (0..<count).map { _ in UInt8.random(in: 0...255, using: &rng) }
}

/// Confronto a tempo costante (niente scorciatoie che rivelino quante lettere coincidono).
func constantTimeEqual(_ a: String, _ b: String) -> Bool {
    let x = Array(a.utf8), y = Array(b.utf8)
    var diff = x.count ^ y.count
    for i in 0..<max(x.count, y.count) {
        diff |= Int(i < x.count ? x[i] : 0) ^ Int(i < y.count ? y[i] : 0)
    }
    return diff == 0
}

/// Il codice di abbinamento corrente: uno solo alla volta.
///
/// Una volta che il codice "termina" (successo, scadenza scoperta o blocco per
/// troppi tentativi sbagliati) resta in quello stato finché non si richiama
/// `start()`, che ne genera uno nuovo. Ogni codice bloccato raddoppia l'attesa
/// prima del successivo (fino a `maxBackoff`): provarli tutti a forza richiederebbe giorni.
final class PairingManager {
    enum StartResult {
        case ok(expiresIn: Double)
        case tooFast
    }
    enum FinishResult: Equatable {
        case ok
        case failed(error: String, attemptsLeft: Int?)
    }

    private let lock = NSLock()
    private let clock: () -> Double
    private let ttl: Double
    private let maxAttempts: Int
    private let minInterval: Double
    private let maxBackoff: Double
    var onCode: ((String, Double) -> Void)?
    var onDone: (() -> Void)?

    private var lockouts = 0  // codici bloccati di fila (si azzera a ogni successo)
    private var code: String?
    private var expiresAt = 0.0
    private var attemptsLeft = 0
    /// nil = codice attivo (o mai creato); "locked"/"expired" = esiste ma non è più usabile.
    private var terminal: String?
    private var lastCall: Double?

    init(clock: @escaping () -> Double = { ProcessInfo.processInfo.systemUptime },
         ttl: Double = 120, maxAttempts: Int = 5, minInterval: Double = 3, maxBackoff: Double = 300) {
        self.clock = clock
        self.ttl = ttl
        self.maxAttempts = maxAttempts
        self.minInterval = minInterval
        self.maxBackoff = maxBackoff
    }

    private var currentInterval: Double { min(maxBackoff, minInterval * pow(2, Double(lockouts))) }

    func start() -> StartResult {
        lock.lock()
        let (result, fire) = locked_start()
        lock.unlock()
        fire?()
        return result
    }

    private func locked_start() -> (StartResult, (() -> Void)?) {
        let t = clock()
        // Il limite vale per OGNI chiamata (anche per rimostrare un codice già valido):
        // toccare due volte di fretta il pulsante sul telefono non deve aprire due finestre.
        if let last = lastCall, t - last < currentInterval { return (.tooFast, nil) }
        lastCall = t

        if let current = code, terminal == nil, t < expiresAt {
            let left = max(0, (expiresAt - t).rounded())
            return (.ok(expiresIn: left), { [onCode] in onCode?(current, left) })
        }
        let fresh = String(format: "%06d", Int.random(in: 0..<1_000_000))
        code = fresh
        expiresAt = t + ttl
        attemptsLeft = maxAttempts
        terminal = nil
        return (.ok(expiresIn: ttl), { [onCode, ttl] in onCode?(fresh, ttl) })
    }

    func finish(_ candidate: String) -> FinishResult {
        lock.lock()
        let (result, fire) = locked_finish(candidate)
        lock.unlock()
        fire?()
        return result
    }

    private func locked_finish(_ candidate: String) -> (FinishResult, (() -> Void)?) {
        guard let current = code else { return (.failed(error: "no_code", attemptsLeft: nil), nil) }
        if terminal == "locked" { return (.failed(error: "locked", attemptsLeft: nil), nil) }
        if terminal == "expired" { return (.failed(error: "expired", attemptsLeft: nil), nil) }

        if clock() >= expiresAt {
            terminal = "expired"
            return (.failed(error: "expired", attemptsLeft: nil), { [onDone] in onDone?() })
        }

        // Solo 6 cifre ASCII.
        let isSixDigits = candidate.utf8.count == 6 && candidate.utf8.allSatisfy { $0 >= 48 && $0 <= 57 }
        if constantTimeEqual(isSixDigits ? candidate : "x", current) {
            code = nil
            expiresAt = 0
            attemptsLeft = 0
            terminal = nil
            lockouts = 0
            return (.ok, { [onDone] in onDone?() })
        }

        attemptsLeft -= 1
        if attemptsLeft <= 0 {
            terminal = "locked"
            lockouts += 1
            return (.failed(error: "locked", attemptsLeft: 0), { [onDone] in onDone?() })
        }
        return (.failed(error: "wrong_code", attemptsLeft: attemptsLeft), nil)
    }
}

/// Elenco dei dispositivi abbinati: salviamo solo l'impronta del token, mai il
/// token in chiaro. File JSON con scrittura atomica e permessi 600 (stesso
/// formato della versione con Python).
final class DeviceStore {
    struct Device {
        let name: String
        let addedAt: String
    }

    private let url: URL
    private let lock = NSLock()

    init(_ url: URL) { self.url = url }

    private func load() -> [[String: Any]] {
        guard let data = try? Data(contentsOf: url),
              let list = (try? JSONSerialization.jsonObject(with: data)) as? [[String: Any]] else { return [] }
        return list
    }

    private func save(_ devices: [[String: Any]]) throws {
        let dir = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let tmp = dir.appendingPathComponent(".tmp-devices-" + UUID().uuidString)
        let data = try JSONSerialization.data(withJSONObject: devices)
        guard FileManager.default.createFile(atPath: tmp.path, contents: data, attributes: [.posixPermissions: 0o600]) else {
            throw CocoaError(.fileWriteUnknown)
        }
        if rename(tmp.path, url.path) != 0 {
            try? FileManager.default.removeItem(at: tmp)
            throw CocoaError(.fileWriteUnknown)
        }
    }

    private static func digest(_ token: String) -> String {
        SHA256.hash(data: Data(token.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    /// Registra un nuovo dispositivo e restituisce il suo token (l'unica volta che esiste in chiaro).
    func add(_ name: String) throws -> String {
        let token = Data(secureRandomBytes(32)).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        lock.lock(); defer { lock.unlock() }
        var devices = load()
        devices.append([
            "name": name,
            "token_sha256": Self.digest(token),
            "added_at": ISO8601DateFormatter().string(from: Date()),
        ])
        try save(devices)
        return token
    }

    func verify(_ token: String?) -> Bool {
        guard let token = token, !token.isEmpty else { return false }
        let digest = Self.digest(token)
        return load().contains { constantTimeEqual(digest, ($0["token_sha256"] as? String) ?? "") }
    }

    func clear() {
        lock.lock(); defer { lock.unlock() }
        try? save([])
    }

    func count() -> Int { load().count }

    /// Nome e data di abbinamento di ogni dispositivo (mai l'impronta del token).
    func devices() -> [Device] {
        load().map { Device(name: ($0["name"] as? String) ?? "iPhone", addedAt: ($0["added_at"] as? String) ?? "") }
    }
}
