// On-device translation via Apple's Translation framework (macOS 26+).
// Usage: apple_translate <source|auto> <target>   stdin: JSON array of strings   stdout: JSON array of strings
// Exit 2: language pair not downloaded (System Settings > General > Language & Region > Translation Languages).
import Foundation
import NaturalLanguage
import Translation

func fail(_ msg: String, _ code: Int32) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(code)
}

@main
struct AppleTranslate {
    static func main() async {
        let args = CommandLine.arguments
        guard args.count == 3 else { fail("usage: apple_translate <source|auto> <target>", 64) }
        guard let texts = try? JSONSerialization.jsonObject(with: FileHandle.standardInput.readDataToEndOfFile()) as? [String] else {
            fail("stdin must be a JSON array of strings", 65)
        }

        var sourceCode = args[1]
        if sourceCode == "auto" {
            let recognizer = NLLanguageRecognizer()
            recognizer.processString(texts.joined(separator: "\n"))
            sourceCode = recognizer.dominantLanguage?.rawValue ?? "en"
        }
        let source = Locale.Language(identifier: sourceCode)
        let target = Locale.Language(identifier: args[2])

        let status = await LanguageAvailability().status(from: source, to: target)
        guard status == .installed else { fail("language pair \(sourceCode)->\(args[2]) not installed (\(status))", 2) }

        do {
            let session = TranslationSession(installedSource: source, target: target)
            let requests = texts.enumerated().map {
                TranslationSession.Request(sourceText: $0.element, clientIdentifier: String($0.offset))
            }
            var byIndex = [Int: String]()
            for response in try await session.translations(from: requests) {
                if let id = response.clientIdentifier, let i = Int(id) { byIndex[i] = response.targetText }
            }
            let output = texts.indices.map { byIndex[$0] ?? "" }
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: output))
        } catch {
            fail("translation failed: \(error)", 1)
        }
    }
}
