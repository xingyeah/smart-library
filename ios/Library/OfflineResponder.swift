import Foundation

/// Answers the web page's requests while the server cannot be reached, from what `OfflineStore` holds.
enum OfflineResponder {
    static func respond(to request: ProxyRequest, store: OfflineStore) -> ProxyResponse {
        if request.method == "GET" && request.path == "/api/books" {
            return shelf(request, store: store)
        }
        if request.method == "GET", let bookID = request.bookID,
           request.path.hasSuffix("/read"), request.query("chapter") == nil {
            return resumeChapter(bookID: bookID, store: store)
        }
        if let cached = store.get(key: OfflineStore.key(for: shellRequest(request)), bookID: request.bookID) {
            return cached
        }
        return .offlineUnavailable
    }

    /// `/` and `/index.html` are one page; both are stored under `/`.
    static func shellRequest(_ request: ProxyRequest) -> ProxyRequest {
        guard request.path == "/index.html" else { return request }
        return ProxyRequest(method: request.method, pathAndQuery: "/", body: request.body)
    }

    /// The shelf offline: downloaded books, then books read online (`offline_partial`), filtered by the search box
    /// text against title and author.
    private static func shelf(_ request: ProxyRequest, store: OfflineStore) -> ProxyResponse {
        let needle = (request.query("q") ?? "").trimmingCharacters(in: .whitespaces).lowercased()
        let items = (store.details() + store.partialDetails()).filter { book in
            guard !needle.isEmpty else { return true }
            let text = "\(book["title"] ?? "") \(book["author"] ?? "")".lowercased()
            return text.contains(needle)
        }
        return .json([
            "items": items,
            "total": items.count,
            "page": 1,
            "page_size": max(items.count, 1),
            "elapsed_ms": 0,
        ])
    }

    /// The reader opens a book with `/read` (no chapter) and the server picks the saved chapter; offline the
    /// chapter comes from the last progress this phone saved.
    private static func resumeChapter(bookID: Int, store: OfflineStore) -> ProxyResponse {
        let progress = store.lastProgress(bookID)
        let chapter = (progress?["chapter_idx"] as? Int) ?? 0
        // A PDF's /read answer is the same for every chapter, so only chapter 0 is downloaded
        let cached = [chapter, 0].lazy
            .map { ProxyRequest(method: "GET", pathAndQuery: "/api/books/\(bookID)/read?chapter=\($0)") }
            .compactMap { store.get(key: OfflineStore.key(for: $0), bookID: bookID) }
            .first
        guard let cached,
              var json = (try? JSONSerialization.jsonObject(with: cached.body)) as? [String: Any] else {
            return .offlineUnavailable
        }
        json["saved_pos"] = progress ?? NSNull()
        return .json(json)
    }
}
