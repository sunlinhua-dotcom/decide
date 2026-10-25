// 本机 OCR（Apple Vision）：ocr 图1 图2 … → 每张一行 JSON {"path":…,"text":…}
import Foundation
import Vision
import AppKit

for path in CommandLine.arguments.dropFirst() {
    var text = ""
    if let img = NSImage(contentsOfFile: path),
       let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) {
        let req = VNRecognizeTextRequest()
        req.recognitionLevel = .accurate
        req.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]
        req.usesLanguageCorrection = true
        try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
        let lines = (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }
        text = lines.joined(separator: "\n")
    }
    let obj: [String: String] = ["path": path, "text": text]
    if let data = try? JSONSerialization.data(withJSONObject: obj), let s = String(data: data, encoding: .utf8) {
        print(s)
    }
}
