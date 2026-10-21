// Nome del Mac e indirizzo IP della rete di casa. Non solleva mai errori: se
// qualcosa manca restituisce un valore di ripiego ragionevole.

import Darwin
import Foundation
import SystemConfiguration

enum NetInfo {
    /// Nome ".local" usato per il certificato e per gli indirizzi (es. Mac-di-Mario.local).
    static func localHostname() -> String {
        let raw = (SCDynamicStoreCopyLocalHostName(nil) as String?) ?? ProcessInfo.processInfo.hostName
        let cleaned = String(raw.unicodeScalars.filter {
            ($0.value < 128) && (CharacterSet.alphanumerics.contains($0) || $0 == "-")
        })
        return cleaned.isEmpty ? "localhost" : cleaned + ".local"
    }

    /// Nome "umano" del Mac (es. "Mac di Mario"), quello che vede l'utente.
    static func computerName() -> String {
        (SCDynamicStoreCopyComputerName(nil, nil) as String?) ?? Host.current().localizedName ?? "Mac"
    }

    /// Reti private: le uniche accettate come indirizzo del Mac (e nel certificato).
    private static let privateNets: [(base: UInt32, bits: Int)] = [
        (0x0A00_0000, 8),    // 10.0.0.0/8
        (0xAC10_0000, 12),   // 172.16.0.0/12
        (0xC0A8_0000, 16),   // 192.168.0.0/16
        (0xA9FE_0000, 16),   // 169.254.0.0/16
        (0x6440_0000, 10),   // 100.64.0.0/10
        (0x7F00_0000, 8),    // 127.0.0.0/8
    ]

    static func parseIPv4(_ ip: String) -> UInt32? {
        let parts = ip.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return nil }
        var value: UInt32 = 0
        for part in parts {
            guard let octet = UInt8(part) else { return nil }
            value = (value << 8) | UInt32(octet)
        }
        return value
    }

    static func isAllowedIP(_ ip: String) -> Bool {
        guard let value = parseIPv4(ip) else { return false }
        return privateNets.contains { net in
            let mask: UInt32 = net.bits == 0 ? 0 : ~UInt32(0) << UInt32(32 - net.bits)
            return value & mask == net.base
        }
    }

    /// IP della rete di casa, o nil se non ne troviamo uno privato.
    ///
    /// Trucco classico: apriamo un socket UDP "verso" 8.8.8.8 (non parte nessun
    /// pacchetto) solo per far scegliere al sistema l'interfaccia di uscita, e ne
    /// leggiamo l'indirizzo locale.
    static func lanIPv4() -> String? {
        let fd = socket(AF_INET, SOCK_DGRAM, 0)
        guard fd >= 0 else { return nil }
        defer { close(fd) }
        var target = sockaddr_in()
        target.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        target.sin_family = sa_family_t(AF_INET)
        target.sin_port = UInt16(80).bigEndian
        inet_pton(AF_INET, "8.8.8.8", &target.sin_addr)
        let connected = withUnsafePointer(to: &target) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard connected == 0 else { return nil }
        var local = sockaddr_in()
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let named = withUnsafeMutablePointer(to: &local) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &length) }
        }
        guard named == 0 else { return nil }
        var buffer = [CChar](repeating: 0, count: Int(INET_ADDRSTRLEN))
        guard inet_ntop(AF_INET, &local.sin_addr, &buffer, socklen_t(INET_ADDRSTRLEN)) != nil else { return nil }
        let ip = String(cString: buffer)
        return isAllowedIP(ip) && !ip.hasPrefix("127.") ? ip : nil
    }
}
