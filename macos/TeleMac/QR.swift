// Codice QR in SVG, fatto da Core Image (niente librerie).

import CoreImage
import CoreImage.CIFilterBuiltins
import Foundation

enum QRCode {
    /// SVG del QR per `text`, con un bordo bianco di 3 moduli. nil se non riesce.
    static func svg(_ text: String) -> String? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        filter.correctionLevel = "M"
        guard let image = filter.outputImage else { return nil }
        let width = Int(image.extent.width), height = Int(image.extent.height)
        guard width > 0, height > 0 else { return nil }

        // Core Image dà un pixel per modulo: leggiamo i pixel scuri.
        let context = CIContext(options: [.useSoftwareRenderer: true])
        var pixels = [UInt8](repeating: 255, count: width * height * 4)
        context.render(image, toBitmap: &pixels, rowBytes: width * 4,
                       bounds: image.extent, format: .RGBA8, colorSpace: CGColorSpaceCreateDeviceRGB())
        func dark(_ x: Int, _ y: Int) -> Bool { pixels[(y * width + x) * 4] < 128 }

        // Il simbolo è il quadrato che contiene tutti i moduli scuri (ci sono i
        // tre riquadri agli angoli): ignoriamo l'eventuale margine bianco di Core Image.
        var minX = width, minY = height, maxX = -1, maxY = -1
        for y in 0..<height {
            for x in 0..<width where dark(x, y) {
                minX = min(minX, x); maxX = max(maxX, x); minY = min(minY, y); maxY = max(maxY, y)
            }
        }
        guard maxX > minX, maxY > minY else { return nil }
        let size = max(maxX - minX, maxY - minY) + 1
        let border = 3
        var path = ""
        for y in 0..<size {
            var x = 0
            while x < size {
                if minX + x < width, minY + y < height, dark(minX + x, minY + y) {
                    var run = 1
                    while x + run < size, minX + x + run < width, dark(minX + x + run, minY + y) { run += 1 }
                    path += "M\(x + border) \(y + border)h\(run)v1h-\(run)z"
                    x += run
                } else {
                    x += 1
                }
            }
        }
        let total = size + 2 * border
        return "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 \(total) \(total)\" shape-rendering=\"crispEdges\">"
            + "<rect width=\"\(total)\" height=\"\(total)\" fill=\"#fff\"/><path d=\"\(path)\" fill=\"#000\"/></svg>"
    }
}
