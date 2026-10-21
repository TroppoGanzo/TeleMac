// CA locale + certificato del server compatibili con iOS, creati con /usr/bin/openssl
// (su macOS è LibreSSL: usiamo solo le opzioni che capisce).
//
// La CA si crea una volta sola e non si rigenera mai (altrimenti andrebbe
// reinstallato il profilo sull'iPhone ogni volta). Il certificato del server si
// rigenera quando manca, sta per scadere o non copre più gli host/IP richiesti
// (es. il Mac ha cambiato indirizzo). Stessi file e stesso formato della versione
// con Python: gli iPhone già abbinati continuano a funzionare.

import CryptoKit
import Foundation
import Security

struct CertPaths {
    let caCert: URL
    let caKey: URL
    let serverCert: URL
    let serverKey: URL

    init(_ dir: URL) {
        caCert = dir.appendingPathComponent("ca.pem")
        caKey = dir.appendingPathComponent("ca.key")
        serverCert = dir.appendingPathComponent("server.pem")
        serverKey = dir.appendingPathComponent("server.key")
    }
}

enum CertError: Error, CustomStringConvertible {
    case openssl(String)
    case identity(String)

    var description: String {
        switch self {
        case .openssl(let s): return "openssl: \(s)"
        case .identity(let s): return "certificato del server: \(s)"
        }
    }
}

enum Certs {
    static let caDays = 3650
    static let serverDays = 397
    static let renewMarginDays = 30.0
    static let cnMax = 64
    static let metaName = "server.json"

    /// Spazio dei nomi fisso per derivare gli UUID del profilo dall'impronta della
    /// CA: stesso Mac, stesso profilo, sempre (l'iPhone lo sostituisce, non lo duplica).
    private static let uuidNamespace = UUID(uuidString: "2f6f4b2a-8a3e-4b7a-9d3a-6a3f0b8e2c4d")!

    private static let nameConstraints =
        "permitted;DNS:local, permitted;DNS:localhost, "
        + "permitted;IP:10.0.0.0/255.0.0.0, permitted;IP:172.16.0.0/255.240.0.0, "
        + "permitted;IP:192.168.0.0/255.255.0.0, permitted;IP:169.254.0.0/255.255.0.0, "
        + "permitted;IP:100.64.0.0/255.192.0.0, permitted;IP:127.0.0.0/255.0.0.0"

    // MARK: openssl

    @discardableResult
    private static func openssl(_ args: [String]) throws -> Data {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: ProcessInfo.processInfo.environment["TELEMAC_OPENSSL"] ?? "/usr/bin/openssl")
        proc.arguments = args
        let out = Pipe(), err = Pipe()
        proc.standardOutput = out
        proc.standardError = err
        do { try proc.run() } catch { throw CertError.openssl("non parte (\(error.localizedDescription))") }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        let errData = err.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        guard proc.terminationStatus == 0 else {
            let message = String(decoding: errData, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            throw CertError.openssl("\(args.prefix(2).joined(separator: " ")) fallito: \(message)")
        }
        return data
    }

    private static func tempDir() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("telemac-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        return dir
    }

    private static func generateKey(_ url: URL) throws {
        try openssl(["genrsa", "-out", url.path, "2048"])
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    private static func randomSerialHex() -> String {
        var raw = (0..<16).map { _ in UInt8.random(in: 0...255) }
        raw[0] &= 0x7F  // bit più alto a 0: alcuni openssl trattano un MSB=1 come negativo
        if raw[0] == 0 { raw[0] = 1 }
        return "0x" + raw.map { String(format: "%02x", $0) }.joined()
    }

    // MARK: Configurazioni openssl

    /// Il CN ammette al massimo 64 caratteri, ma un nome host di macOS può arrivare
    /// a 63 (+ ".local"): serve solo da etichetta, per Safari conta il subjectAltName.
    static func caCommonName(_ host: String) -> String {
        let prefix = "TeleMac CA (", suffix = ")"
        let room = cnMax - prefix.count - suffix.count
        return prefix + String(host.prefix(room)) + suffix
    }

    private static func caConfig(_ host: String) -> String {
        """
        [req]
        distinguished_name = dn
        prompt = no
        [dn]
        CN = \(caCommonName(host))
        O = TeleMac
        [v3_ca]
        basicConstraints = critical, CA:TRUE, pathlen:0
        keyUsage = critical, keyCertSign, cRLSign
        subjectKeyIdentifier = hash
        nameConstraints = \(nameConstraints)

        """
    }

    private static func leafConfig(hostnames: [String], ips: [String]) -> String {
        var lines = [
            "[v3_leaf]",
            "basicConstraints = critical, CA:FALSE",
            "keyUsage = critical, digitalSignature, keyEncipherment",
            "extendedKeyUsage = serverAuth",
            "subjectAltName = @alt_names",
            "authorityKeyIdentifier = keyid",
            "",
            "[alt_names]",
        ]
        for (i, host) in hostnames.enumerated() { lines.append("DNS.\(i + 1) = \(host)") }
        for (i, ip) in ips.enumerated() { lines.append("IP.\(i + 1) = \(ip)") }
        return lines.joined(separator: "\n") + "\n"
    }

    // MARK: Creazione

    private static func createCA(_ paths: CertPaths, hostnames: [String]) throws {
        try generateKey(paths.caKey)
        let tmp = try tempDir()
        defer { try? FileManager.default.removeItem(at: tmp) }
        let cnf = tmp.appendingPathComponent("ca.cnf")
        try caConfig(hostnames.first ?? "TeleMac").write(to: cnf, atomically: true, encoding: .utf8)
        try openssl(["req", "-new", "-x509", "-key", paths.caKey.path, "-config", cnf.path,
                     "-extensions", "v3_ca", "-days", String(caDays), "-sha256", "-out", paths.caCert.path])
    }

    private static func createServerCert(_ dir: URL, _ paths: CertPaths, hostnames: [String], ips: [String]) throws {
        let hosts = hostnames.isEmpty ? ["localhost"] : hostnames
        try generateKey(paths.serverKey)
        let tmp = try tempDir()
        defer { try? FileManager.default.removeItem(at: tmp) }
        let csrCnf = tmp.appendingPathComponent("csr.cnf")
        try "[req]\ndistinguished_name = dn\nprompt = no\n[dn]\nCN = \(String(hosts[0].prefix(cnMax)))\n"
            .write(to: csrCnf, atomically: true, encoding: .utf8)
        let csr = tmp.appendingPathComponent("server.csr")
        try openssl(["req", "-new", "-key", paths.serverKey.path, "-config", csrCnf.path, "-out", csr.path])
        let leafCnf = tmp.appendingPathComponent("leaf.cnf")
        try leafConfig(hostnames: hosts, ips: ips).write(to: leafCnf, atomically: true, encoding: .utf8)
        try openssl(["x509", "-req", "-in", csr.path, "-CA", paths.caCert.path, "-CAkey", paths.caKey.path,
                     "-set_serial", randomSerialHex(), "-days", String(serverDays), "-sha256",
                     "-extfile", leafCnf.path, "-extensions", "v3_leaf", "-out", paths.serverCert.path])
        let meta: [String: Any] = [
            "hostnames": hosts, "ips": ips,
            "not_after": Date().timeIntervalSince1970 + Double(serverDays) * 86400,
        ]
        try JSONSerialization.data(withJSONObject: meta).write(to: dir.appendingPathComponent(metaName), options: .atomic)
    }

    private static func dedup(_ items: [String]) -> [String] {
        var seen: [String] = []
        for item in items where !item.isEmpty && !seen.contains(item) { seen.append(item) }
        return seen
    }

    /// Crea (se serve) la CA e il certificato del server, e ne restituisce i percorsi.
    @discardableResult
    static func ensure(in dir: URL, hostnames: [String], ips: [String]) throws -> CertPaths {
        let fm = FileManager.default
        try fm.createDirectory(at: dir, withIntermediateDirectories: true)
        let paths = CertPaths(dir)
        let wantedHosts = dedup(hostnames)
        let wantedIPs = dedup(ips.filter(NetInfo.isAllowedIP))

        if !fm.fileExists(atPath: paths.caCert.path) || !fm.fileExists(atPath: paths.caKey.path) {
            try createCA(paths, hostnames: wantedHosts.isEmpty ? ["TeleMac"] : wantedHosts)
        }

        let meta = (try? Data(contentsOf: dir.appendingPathComponent(metaName)))
            .flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        var needsNew = !fm.fileExists(atPath: paths.serverCert.path) || !fm.fileExists(atPath: paths.serverKey.path) || meta == nil
        if let meta = meta, !needsNew {
            let notAfter = (meta["not_after"] as? NSNumber)?.doubleValue ?? 0
            if notAfter < Date().timeIntervalSince1970 + renewMarginDays * 86400 { needsNew = true }
            let metaHosts = Set((meta["hostnames"] as? [String]) ?? [])
            let metaIPs = Set((meta["ips"] as? [String]) ?? [])
            if !(Set(wantedHosts).isSubset(of: metaHosts) && Set(wantedIPs).isSubset(of: metaIPs)) { needsNew = true }
        }
        if needsNew { try createServerCert(dir, paths, hostnames: wantedHosts, ips: wantedIPs) }
        return paths
    }

    // MARK: CA: DER, impronta, profilo

    /// Certificato della CA in formato DER (quello che si scarica come .cer).
    static func caDER(_ paths: CertPaths) throws -> Data {
        let pem = try String(contentsOf: paths.caCert, encoding: .ascii)
        let body = pem.split(whereSeparator: \.isNewline).filter { !$0.hasPrefix("-----") }.joined()
        guard let der = Data(base64Encoded: body) else { throw CertError.openssl("ca.pem non leggibile") }
        return der
    }

    /// Impronta SHA-256 della CA, in maiuscolo, a coppie separate da ':'.
    static func caFingerprint(_ paths: CertPaths) throws -> String {
        SHA256.hash(data: try caDER(paths)).map { String(format: "%02X", $0) }.joined(separator: ":")
    }

    private static func uuid5(_ name: String) -> String {
        var bytes = withUnsafeBytes(of: uuidNamespace.uuid) { Array($0) } + Array(name.utf8)
        var digest = Array(Insecure.SHA1.hash(data: Data(bytes)))
        bytes = []
        digest[6] = (digest[6] & 0x0F) | 0x50
        digest[8] = (digest[8] & 0x3F) | 0x80
        let u = digest.prefix(16).map { String(format: "%02x", $0) }.joined()
        let c = Array(u)
        return String(c[0..<8]) + "-" + String(c[8..<12]) + "-" + String(c[12..<16]) + "-"
            + String(c[16..<20]) + "-" + String(c[20..<32])
    }

    /// Profilo .mobileconfig (non firmato) che installa solo la CA come radice fidata.
    static func mobileconfig(_ paths: CertPaths, computerName: String) throws -> Data {
        let fingerprint = try caFingerprint(paths)
        let root: [String: Any] = [
            "PayloadType": "Configuration",
            "PayloadVersion": 1,
            "PayloadIdentifier": "io.github.troppoganzo.telemac.profile",
            "PayloadUUID": uuid5(fingerprint + "|profile"),
            "PayloadDisplayName": "TeleMac – " + computerName,
            "PayloadDescription": "Permette all'iPhone di collegarsi in modo sicuro a TeleMac sul tuo Mac. "
                + "Vale solo per i dispositivi della rete di casa.",
            "PayloadOrganization": "TeleMac",
            "PayloadContent": [[
                "PayloadType": "com.apple.security.root",
                "PayloadVersion": 1,
                "PayloadIdentifier": "io.github.troppoganzo.telemac.ca",
                "PayloadUUID": uuid5(fingerprint + "|ca"),
                "PayloadCertificateFileName": "TeleMac-CA.cer",
                "PayloadDisplayName": "TeleMac CA",
                "PayloadContent": try caDER(paths),
            ] as [String: Any]],
        ]
        return try PropertyListSerialization.data(fromPropertyList: root, format: .xml, options: 0)
    }

    // MARK: Identità per TLS

    /// Chiave + certificato del server come `SecIdentity`, per Network.framework.
    /// Passa da un .p12 temporaneo; dove macOS lo permette resta solo in memoria
    /// (niente voci nel portachiavi dell'utente).
    static func identity(_ paths: CertPaths) throws -> SecIdentity {
        let tmp = try tempDir()
        defer { try? FileManager.default.removeItem(at: tmp) }
        let p12 = tmp.appendingPathComponent("server.p12")
        let password = UUID().uuidString
        try openssl(["pkcs12", "-export", "-inkey", paths.serverKey.path, "-in", paths.serverCert.path,
                     "-out", p12.path, "-passout", "pass:" + password])
        var options: [String: Any] = [kSecImportExportPassphrase as String: password]
        if #available(macOS 15.0, *) { options[kSecImportToMemoryOnly as String] = true }
        var items: CFArray?
        let status = SecPKCS12Import(try Data(contentsOf: p12) as CFData, options as CFDictionary, &items)
        guard status == errSecSuccess,
              let first = (items as? [[String: Any]])?.first,
              let identity = first[kSecImportItemIdentity as String] else {
            throw CertError.identity("importazione fallita (\(status))")
        }
        return identity as! SecIdentity
    }
}
