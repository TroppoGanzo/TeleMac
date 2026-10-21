// La pagina della finestra (web/panel.html) vive dentro l'app: la serviamo con
// uno schema "telemac://panel/" invece che con un server su 127.0.0.1. Così
// nessun altro programma (né la rete) può leggere il codice di abbinamento o
// scollegare gli iPhone: la pagina esiste solo dentro la finestra.

import Foundation
import WebKit

final class PanelSchemeHandler: NSObject, WKURLSchemeHandler {
    var server: TeleMacServer?
    private let webDir: URL

    init(webDir: URL) { self.webDir = webDir }

    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        guard let url = task.request.url else {
            task.didFailWithError(URLError(.badURL))
            return
        }
        let method = task.request.httpMethod ?? "GET"
        var status = 200
        var type = "application/json; charset=utf-8"
        var body = Data()

        switch (method, url.path.isEmpty ? "/" : url.path) {
        case ("GET", "/"):
            if let page = try? Data(contentsOf: webDir.appendingPathComponent("panel.html")) {
                body = page
                type = "text/html; charset=utf-8"
            } else {
                status = 404
            }
        case ("GET", let path) where path == "/icon-192.png" || path == "/icon-512.png":
            if let icon = try? Data(contentsOf: webDir.appendingPathComponent(String(path.dropFirst()))) {
                body = icon
                type = "image/png"
            } else {
                status = 404
            }
        case ("GET", "/api/status"):
            if let server = server {
                body = (try? JSONSerialization.data(withJSONObject: server.panelStatus())) ?? Data()
            } else {
                status = 503
                body = Data("{\"ok\":false}".utf8)
            }
        case ("POST", "/api/forget-devices"):
            let removed = server?.forgetDevices() ?? 0
            body = Data("{\"ok\":true,\"removed\":\(removed)}".utf8)
        default:
            status = 404
        }

        let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": type, "Cache-Control": "no-store"])!
        task.didReceive(response)
        task.didReceive(body)
        task.didFinish()
    }

    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {}
}
