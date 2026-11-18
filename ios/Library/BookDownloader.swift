import Foundation

/// Downloads everything the reader requests for one book so it opens offline: detail JSON and covers, every
/// chapter with its images, or every PDF page image and its text blocks.
@MainActor
final class BookDownloader: ObservableObject {
    struct Status: Equatable {
        var title: String
        var done: Int
        var total: Int
        var error: String?
    }

    enum DownloadError: LocalizedError {
        case http(status: Int, path: String)
        case badJSON(path: String)
        var errorDescription: String? {
            switch self {
            case let .http(status, path): return "服务器返回 \(status)（\(path)）"
            case let .badJSON(path): return "响应格式不对（\(path)）"
            }
        }
    }

    private static let coverWidths = [360, 720]  // COVER_THUMB_WIDTH and COVER_DETAIL_WIDTH in index.html
    private static let assetPattern = try! NSRegularExpression(
        pattern: #"/api/books/\d+/(?:epub_asset|mobi_image)\?[^"'\s<>)]+"#)

    @Published private(set) var status: Status?
    /// Called after a book is added or removed, so the page can update its offline buttons.
    var onLibraryChange: (() -> Void)?

    private let client: ServerClient
    private let store: OfflineStore

    init(client: ServerClient, store: OfflineStore) {
        self.client = client
        self.store = store
    }

    func download(bookID: Int) async {
        guard status == nil || status?.error != nil else { return }
        status = Status(title: "准备下载…", done: 0, total: 1)
        do {
            let detail = try await fetchJSON("/api/books/\(bookID)", bookID: bookID)
            status?.title = detail.json["title"] as? String ?? "第 \(bookID) 本书"
            try await downloadCovers(detail.json, bookID: bookID)
            try await downloadContent(bookID: bookID)
            store.saveDetail(detail.raw, bookID: bookID)
            store.markComplete(bookID)
            status = nil
            onLibraryChange?()
        } catch {
            NSLog("Offline download failed: bookId=%d error=%@", bookID, error.localizedDescription)
            store.removeBook(bookID)
            status?.error = "下载失败：\(error.localizedDescription)"
        }
    }

    func remove(bookID: Int) {
        store.removeBook(bookID)
        onLibraryChange?()
    }

    func dismissError() { status = nil }

    // MARK: steps

    private func downloadCovers(_ detail: [String: Any], bookID: Int) async throws {
        guard let cover = detail["cover_path"] as? String, !cover.isEmpty else { return }
        for width in Self.coverWidths {
            try await fetch("\(cover)?w=\(width)", bookID: bookID)
        }
    }

    private func downloadContent(bookID: Int) async throws {
        let first = try await fetchJSON("/api/books/\(bookID)/read?chapter=0", bookID: bookID).json
        guard first["ok"] as? Bool == true else {
            throw DownloadError.http(status: 409, path: first["error"] as? String ?? "read")
        }
        if first["mode"] as? String == "pdf" {
            try await downloadPDF(bookID: bookID)
        } else {
            try await downloadChapters(first, bookID: bookID)
        }
    }

    private func downloadPDF(bookID: Int) async throws {
        // 409 means the server cannot render this PDF; the reader then falls back to pdf.js on the whole file
        let layoutResponse = try await client.get("/api/books/\(bookID)/pdf_layout")
        let layout = (try? JSONSerialization.jsonObject(with: layoutResponse.body)) as? [String: Any] ?? [:]
        guard layoutResponse.status == 200, let pageCount = layout["page_count"] as? Int else {
            try await fetch("/api/books/\(bookID)/stream", bookID: bookID)
            return
        }
        let layoutRequest = ProxyRequest(method: "GET", pathAndQuery: "/api/books/\(bookID)/pdf_layout")
        store.put(layoutResponse, key: OfflineStore.key(for: layoutRequest), bookID: bookID)
        status?.total = pageCount * 2 + 2
        for page in 1...max(pageCount, 1) {
            try await fetch("/api/books/\(bookID)/pdf_page/\(page)", bookID: bookID)
            try await fetch("/api/books/\(bookID)/pdf_text/\(page)", bookID: bookID)
        }
    }

    private func downloadChapters(_ first: [String: Any], bookID: Int) async throws {
        let total = max(first["total_chapters"] as? Int ?? 1, 1)
        status?.total = total + 2
        var assets = Self.assetPaths(in: first["html"] as? String ?? "")
        for chapter in 1..<max(total, 1) {
            let json = try await fetchJSON("/api/books/\(bookID)/read?chapter=\(chapter)", bookID: bookID).json
            assets.formUnion(Self.assetPaths(in: json["html"] as? String ?? ""))
        }
        status?.total += assets.count
        for path in assets.sorted() {
            try await fetch(path, bookID: bookID)
        }
    }

    /// Image and stylesheet URLs the server wrote into a chapter's HTML (`&amp;` decoded).
    static func assetPaths(in html: String) -> Set<String> {
        let range = NSRange(html.startIndex..., in: html)
        let matches = assetPattern.matches(in: html, range: range)
        return Set(matches.compactMap { Range($0.range, in: html).map { html[$0].replacingOccurrences(of: "&amp;", with: "&") } })
    }

    // MARK: fetching

    @discardableResult
    private func fetch(_ path: String, bookID: Int) async throws -> ProxyResponse {
        let request = ProxyRequest(method: "GET", pathAndQuery: path)
        let response = try await client.send(request)
        guard response.status == 200 else { throw DownloadError.http(status: response.status, path: path) }
        store.put(response, key: OfflineStore.key(for: request), bookID: bookID)
        status?.done += 1
        return response
    }

    private func fetchJSON(_ path: String, bookID: Int) async throws -> (json: [String: Any], raw: Data) {
        let response = try await fetch(path, bookID: bookID)
        guard let json = (try? JSONSerialization.jsonObject(with: response.body)) as? [String: Any] else {
            throw DownloadError.badJSON(path: path)
        }
        return (json, response.body)
    }
}
