// macOS 内蔵の翻訳（Translation フレームワーク、端末内で動く）で、英語の文をまとめて日本語にする。
// 文は Mac の外に出ない。translate.py がこれを swiftc で作って呼ぶ。
//
//   入力（標準入力）:  {"source": "en", "target": "ja", "texts": ["...", "..."]}
//   出力（標準出力）:  {"status": "installed", "translations": ["...", "..."]}
//                     言語データが入っていなければ {"status": "supported"}（システム設定から入れる）
//                     対応していない組み合わせなら {"status": "unsupported"}
import Foundation
import Translation

struct Input: Decodable { let source: String; let target: String; let texts: [String] }
struct Output: Encodable { let status: String; var translations: [String]? = nil; var error: String? = nil }

func emit(_ o: Output) {
    let data = try! JSONEncoder().encode(o)
    FileHandle.standardOutput.write(data)
}

let raw = FileHandle.standardInput.readDataToEndOfFile()
guard let input = try? JSONDecoder().decode(Input.self, from: raw) else {
    emit(Output(status: "error", error: "入力の JSON が読めない"))
    exit(2)
}
let src = Locale.Language(identifier: input.source), dst = Locale.Language(identifier: input.target)
let status = await LanguageAvailability().status(from: src, to: dst)
switch status {
case .installed:
    do {
        let session = TranslationSession(installedSource: src, target: dst)
        let reqs = input.texts.enumerated().map { TranslationSession.Request(sourceText: $0.element, clientIdentifier: String($0.offset)) }
        var out = Array(repeating: "", count: input.texts.count)
        for r in try await session.translations(from: reqs) {
            if let id = r.clientIdentifier, let i = Int(id) { out[i] = r.targetText }
        }
        emit(Output(status: "installed", translations: out))
    } catch {
        emit(Output(status: "error", error: String(describing: error)))
        exit(1)
    }
case .supported:
    emit(Output(status: "supported"))
default:
    emit(Output(status: "unsupported"))
}
