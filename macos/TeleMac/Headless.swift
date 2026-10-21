// `TeleMac --headless`: solo il server, senza finestre né Dock. Serve alle prove
// automatiche (tests/test_app.py) e a chi vuole lanciarlo a mano dal Terminale.
//
//   --dry-run          non tocca mouse e tastiera, scrive i comandi su stdout
//   --port N           porta HTTPS (predefinita 8765; 0 = a caso)
//   --setup-port N     porta della pagina di configurazione (predefinita 8766)
//   --home DIR         cartella di certificati e dispositivi (predefinita ~/.telemac)
//   --web DIR          cartella delle pagine (predefinita: quella dentro l'app)
//
// Eventi su stdout, una riga ciascuno: "@telemac {json}".

import Foundation

enum Headless {
    static func run(_ args: [String]) -> Never {
        func value(_ name: String) -> String? {
            guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
            return args[i + 1]
        }
        let web = value("--web").map { URL(fileURLWithPath: $0) }
            ?? Bundle.main.resourceURL!.appendingPathComponent("web")
        var options = TeleMacServer.Options(webDir: web)
        options.dryRun = args.contains("--dry-run")
        if let p = value("--port").flatMap({ UInt16($0) }) { options.appPort = p }
        if let p = value("--setup-port").flatMap({ UInt16($0) }) { options.setupPort = p }
        if let home = value("--home") { options.stateDir = URL(fileURLWithPath: home) }

        func emit(_ event: [String: Any]) {
            guard let data = try? JSONSerialization.data(withJSONObject: event) else { return }
            print("@telemac " + String(decoding: data, as: UTF8.self))
            fflush(stdout)
        }

        let server = TeleMacServer(options)
        server.onEvent = { event in
            switch event {
            case .ready(let app, let setup):
                emit(["event": "ready", "appPort": Int(app), "setupPort": setup.map { Int($0) } as Any])
            case .pairCode: emit(["event": "pair_code"])
            case .pairDone: emit(["event": "pair_done"])
            case .paired(let name): emit(["event": "paired", "device": name])
            case .accessibility(let granted): emit(["event": "accessibility", "granted": granted])
            case .portInUse(let port):
                emit(["event": "error", "code": "port_in_use", "port": Int(port)])
                exit(3)
            case .failed(let message):
                emit(["event": "error", "code": "failed", "message": message])
                exit(1)
            }
        }
        server.start()

        // SIGTERM / Ctrl+C: il server rimette a posto il cursore prima di uscire.
        for sig in [SIGTERM, SIGINT] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler { server.stop(); exit(0) }
            source.resume()
            signalSources.append(source)
        }
        dispatchMain()
    }

    private static var signalSources: [DispatchSourceSignal] = []
}
