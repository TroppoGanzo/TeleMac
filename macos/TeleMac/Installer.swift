// Installazione "come su iPhone": niente disco d'installazione che resta sulla
// scrivania e niente copie sparse.
//
// - Se TeleMac viene aperto dal DMG (o dai Download), si copia da solo in
//   Applicazioni, si riapre da lì e chiude la copia vecchia.
// - La copia in Applicazioni, appena partita, espelle il disco "TeleMac" e
//   sposta nel Cestino il file .dmg scaricato (si può sempre recuperare).
// - Stessa pulizia se l'utente ha trascinato l'app a mano e ha lasciato il
//   disco montato.

import Cocoa

enum Installer {
    static let appName = "TeleMac.app"

    // MARK: Dove siamo

    /// Percorso vero dell'app. Quando si apre un'app scaricata da internet,
    /// macOS la esegue da una copia temporanea nascosta ("App Translocation"):
    /// qui ricaviamo da dove l'utente l'ha davvero aperta.
    static func originalBundleURL() -> URL {
        let url = Bundle.main.bundleURL
        guard url.path.contains("/AppTranslocation/"),
              let handle = dlopen("/System/Library/Frameworks/Security.framework/Security", RTLD_LAZY) else {
            return url
        }
        defer { dlclose(handle) }
        typealias OriginalPath = @convention(c) (CFURL, UnsafeMutablePointer<Unmanaged<CFError>?>?) -> Unmanaged<CFURL>?
        guard let symbol = dlsym(handle, "SecTranslocateCreateOriginalPathForURL") else { return url }
        let originalPath = unsafeBitCast(symbol, to: OriginalPath.self)
        guard let original = originalPath(url as CFURL, nil) else { return url }
        return original.takeRetainedValue() as URL
    }

    static func isInApplications(_ url: URL) -> Bool {
        let parent = url.deletingLastPathComponent().standardizedFileURL.path
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return parent == "/Applications" || parent == home + "/Applications"
    }

    /// Vale la pena installarsi solo se siamo stati aperti da un posto "di
    /// passaggio": il disco del DMG, i Download o la Scrivania. (Non da una
    /// cartella di sviluppo come build/.)
    static func isTemporaryLocation(_ url: URL) -> Bool {
        let path = url.standardizedFileURL.path
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return path.hasPrefix("/Volumes/")
            || path.hasPrefix(home + "/Downloads/")
            || path.hasPrefix(home + "/Desktop/")
    }

    // MARK: Dischi d'installazione

    /// Punti di montaggio dei dischi immagine -> file .dmg da cui vengono.
    static func mountedImages() -> [String: String] {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/hdiutil")
        proc.arguments = ["info", "-plist"]
        let out = Pipe()
        proc.standardOutput = out
        proc.standardError = FileHandle.nullDevice
        guard (try? proc.run()) != nil else { return [:] }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        guard let plist = (try? PropertyListSerialization.propertyList(from: data, format: nil)) as? [String: Any],
              let images = plist["images"] as? [[String: Any]] else { return [:] }
        var result: [String: String] = [:]
        for image in images {
            guard let imagePath = image["image-path"] as? String,
                  let entities = image["system-entities"] as? [[String: Any]] else { continue }
            for entity in entities {
                if let mount = entity["mount-point"] as? String {
                    result[mount] = imagePath
                }
            }
        }
        return result
    }

    static func volumeRoot(of url: URL) -> String? {
        let parts = url.standardizedFileURL.pathComponents  // ["/", "Volumes", "TeleMac", ...]
        guard parts.count >= 3, parts[1] == "Volumes" else { return nil }
        return "/Volumes/" + parts[2]
    }

    /// Espelle il disco e mette il .dmg nel Cestino. Riprova un po' di volte:
    /// l'app vecchia potrebbe impiegare un attimo a liberare il disco.
    static func ejectInstaller(volume: String, image: String?, log: (String) -> Void) {
        for attempt in 0..<6 {
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/hdiutil")
            proc.arguments = attempt < 4 ? ["detach", volume] : ["detach", "-force", volume]
            proc.standardOutput = FileHandle.nullDevice
            proc.standardError = FileHandle.nullDevice
            if (try? proc.run()) != nil {
                proc.waitUntilExit()
                if proc.terminationStatus == 0 { break }
            }
            Thread.sleep(forTimeInterval: 0.7)
        }
        log("Espulso il disco d'installazione \(volume)\n")
        if let image = image, image.hasSuffix(".dmg"),
           (image as NSString).lastPathComponent.hasPrefix("TeleMac"),
           FileManager.default.fileExists(atPath: image) {
            try? FileManager.default.trashItem(at: URL(fileURLWithPath: image), resultingItemURL: nil)
            log("Spostato nel Cestino \(image)\n")
        }
    }

    /// Dischi "TeleMac" ancora montati (non quello da cui giriamo noi).
    static func leftoverInstallers(excluding ours: URL) -> [(String, String?)] {
        let images = mountedImages()
        let ownVolume = volumeRoot(of: ours)
        return images.compactMap { mount, image in
            guard mount != ownVolume,
                  FileManager.default.fileExists(atPath: mount + "/" + appName),
                  (image as NSString).lastPathComponent.hasPrefix("TeleMac") else { return nil }
            return (mount, image)
        }
    }

    // MARK: Installazione

    /// Se serve, copia l'app in Applicazioni, la riapre da lì e restituisce
    /// true: allora questa copia deve solo chiudersi.
    static func installIfNeeded(log: @escaping (String) -> Void) -> Bool {
        if ProcessInfo.processInfo.environment["TELEMAC_NO_INSTALL"] != nil { return false }
        let source = originalBundleURL()
        guard !isInApplications(source), isTemporaryLocation(source) else { return false }

        let fm = FileManager.default
        let destinations = [
            URL(fileURLWithPath: "/Applications"),
            fm.homeDirectoryForCurrentUser.appendingPathComponent("Applications"),
        ]

        // Se c'è già una TeleMac aperta (la versione vecchia), la chiudiamo:
        // il suo server occuperebbe la rete al posto di quella nuova.
        if let bundleID = Bundle.main.bundleIdentifier {
            let others = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
                .filter { $0.processIdentifier != getpid() }
            others.forEach { $0.terminate() }
            let deadline = Date().addingTimeInterval(4)
            while others.contains(where: { !$0.isTerminated }) && Date() < deadline {
                RunLoop.current.run(until: Date().addingTimeInterval(0.1))
            }
            others.filter { !$0.isTerminated }.forEach { $0.forceTerminate() }
        }

        for folder in destinations {
            let target = folder.appendingPathComponent(appName)
            do {
                try fm.createDirectory(at: folder, withIntermediateDirectories: true)
                if fm.fileExists(atPath: target.path) {
                    try fm.trashItem(at: target, resultingItemURL: nil)  // la versione vecchia, nel Cestino
                }
                try fm.copyItem(at: source, to: target)
            } catch {
                log("Non riesco a installare in \(folder.path): \(error.localizedDescription)\n")
                continue
            }
            // L'utente ha già detto "Apri comunque" per questa app: la copia in
            // Applicazioni non deve chiederlo una seconda volta.
            let xattr = Process()
            xattr.executableURL = URL(fileURLWithPath: "/usr/bin/xattr")
            xattr.arguments = ["-dr", "com.apple.quarantine", target.path]
            try? xattr.run()
            xattr.waitUntilExit()

            var arguments = ["--telemac-installed-from", source.path, "--telemac-old-pid", String(getpid())]
            if let volume = volumeRoot(of: source) {
                arguments += ["--telemac-eject", volume]
                if let image = mountedImages()[volume] { arguments += ["--telemac-image", image] }
            }
            let config = NSWorkspace.OpenConfiguration()
            config.arguments = arguments
            config.createsNewApplicationInstance = true
            config.activates = true
            NSWorkspace.shared.openApplication(at: target, configuration: config) { _, error in
                if let error = error {
                    log("Non riesco ad aprire la copia installata: \(error.localizedDescription)\n")
                }
                DispatchQueue.main.async { NSApp.terminate(nil) }
            }
            log("Installato in \(target.path)\n")
            return true
        }
        return false
    }

    /// Da chiamare all'avvio: finisce il lavoro di un'installazione appena
    /// fatta e toglie i dischi d'installazione rimasti montati.
    static func cleanUpAfterLaunch(log: @escaping (String) -> Void) {
        let args = CommandLine.arguments
        func value(_ name: String) -> String? {
            guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
            return args[i + 1]
        }
        let ours = originalBundleURL()
        guard isInApplications(ours) else { return }

        DispatchQueue.global(qos: .utility).async {
            if let pid = value("--telemac-old-pid").flatMap({ Int32($0) }) {
                let deadline = Date().addingTimeInterval(6)
                while kill(pid, 0) == 0 && Date() < deadline {
                    Thread.sleep(forTimeInterval: 0.1)
                }
            }
            if let volume = value("--telemac-eject") {
                ejectInstaller(volume: volume, image: value("--telemac-image"), log: log)
            } else if let from = value("--telemac-installed-from"),
                      !from.hasPrefix("/Volumes/"), (from as NSString).lastPathComponent == appName,
                      FileManager.default.fileExists(atPath: from) {
                // Aperta dai Download (zip): la copia lasciata lì non serve più.
                try? FileManager.default.trashItem(at: URL(fileURLWithPath: from), resultingItemURL: nil)
                log("Spostata nel Cestino la copia in \(from)\n")
            }
            for (volume, image) in leftoverInstallers(excluding: ours) {
                ejectInstaller(volume: volume, image: image, log: log)
            }
        }
    }
}
