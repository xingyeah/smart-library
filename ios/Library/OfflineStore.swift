import CryptoKit
import Foundation

/// Server responses kept on disk for offline use.
///
/// Layout under Application Support/Offline:
///   shared/                 page shell (`/`, `/vendor/*`, `/assets/*`) and translations
///   books/<id>/             one downloaded book: responses, `book.json`, `progress.json`, `complete`
///   cache/<id>/             what was read online of a book that is not downloaded: responses, `book.json`;
///                           whole books are evicted, least recently used first, above `readThroughLimit`
///   pending-progress/<id>   latest progress POST body not yet accepted by the server
final class OfflineStore {
    static let shared = OfflineStore()
    static let readThroughLimit: Int64 = 500 * 1024 * 1024
    /// Summing the cache folder costs a directory walk, so the limit is checked every this many writes.
    private static let evictionCheckInterval = 50

    private var writesSinceEvictionCheck = 0

    private let root: URL
    private let fm = FileManager()
    private let ioQueue = DispatchQueue(label: "library.offline-store", qos: .utility)

    init(root: URL? = nil) {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        self.root = root ?? support.appendingPathComponent("Offline", isDirectory: true)
    }

    /// Cache key: method, path and query with the `v` / `retry` cache-busters dropped and items sorted, plus a
    /// body hash for POSTs.
    static func key(for request: ProxyRequest) -> String {
        let ignored: Set<String> = ["v", "retry"]
        let query = request.queryItems
            .filter { !ignored.contains($0.name) }
            .map { "\($0.name)=\($0.value ?? "")" }
            .sorted()
            .joined(separator: "&")
        let bodyHash = request.body.map { hexDigest($0) } ?? ""
        return "\(request.method) \(request.path)?\(query)#\(bodyHash)"
    }

    // MARK: responses

    func put(_ response: ProxyResponse, key: String, bookID: Int?) {
        ioQueue.sync { _put(response, key: key, bookID: bookID) }
    }

    func putAsync(_ response: ProxyResponse, key: String, bookID: Int?) {
        ioQueue.async { self._put(response, key: key, bookID: bookID) }
    }

    private func _put(_ response: ProxyResponse, key: String, bookID: Int?) {
        _writeResponse(response, key: key, dir: bookID.map(bookDir) ?? sharedDir)
    }

    private func _writeResponse(_ response: ProxyResponse, key: String, dir: URL) {
        let name = Self.hexDigest(Data(key.utf8))
        let meta = ["Content-Type": response.contentType]
        _write(response.body, to: dir.appendingPathComponent(name + ".bin"))
        _write((try? JSONSerialization.data(withJSONObject: meta)) ?? Data(), to: dir.appendingPathComponent(name + ".json"))
    }

    /// Looks in the shared folder, then in that book's download and read-through cache, or in every downloaded
    /// book when the request names no book (covers).
    func get(key: String, bookID: Int?) -> ProxyResponse? {
        ioQueue.sync {
            let name = Self.hexDigest(Data(key.utf8))
            let bookDirs = bookID.map { [bookDir($0), cacheDir($0)] } ?? _downloadedBookIDs().map(bookDir)
            for dir in [sharedDir] + bookDirs {
                guard let body = try? Data(contentsOf: dir.appendingPathComponent(name + ".bin")) else { continue }
                if dir.path.hasPrefix(cacheRoot.path) { _touch(dir) }
                let metaData = (try? Data(contentsOf: dir.appendingPathComponent(name + ".json"))) ?? Data()
                let meta = (try? JSONSerialization.jsonObject(with: metaData)) as? [String: String] ?? [:]
                return ProxyResponse(status: 200, headers: meta, body: body)
            }
            return nil
        }
    }

    // MARK: read-through cache

    /// Keeps a response the reader fetched online. The detail JSON also lists the book on the offline shelf, and a
    /// resume (`/read` without a chapter) is also stored as that chapter, which is how offline reading looks it up.
    func putReadThrough(_ response: ProxyResponse, for request: ProxyRequest, bookID: Int) {
        ioQueue.sync { _putReadThrough(response, for: request, bookID: bookID) }
    }

    func putReadThroughAsync(_ response: ProxyResponse, for request: ProxyRequest, bookID: Int) {
        ioQueue.async { self._putReadThrough(response, for: request, bookID: bookID) }
    }

    private func _putReadThrough(_ response: ProxyResponse, for request: ProxyRequest, bookID: Int) {
        let dir = cacheDir(bookID)
        _writeResponse(response, key: Self.key(for: request), dir: dir)
        if request.path == "/api/books/\(bookID)" { _write(response.body, to: dir.appendingPathComponent("book.json")) }
        if request.path.hasSuffix("/read"), request.query("chapter") == nil,
           let json = (try? JSONSerialization.jsonObject(with: response.body)) as? [String: Any],
           let chapter = (json["current_chapter"] as? NSNumber)?.intValue {
            let chapterRequest = ProxyRequest(method: "GET", pathAndQuery: "\(request.path)?chapter=\(chapter)")
            _writeResponse(response, key: Self.key(for: chapterRequest), dir: dir)
        }
        _touch(dir)
        writesSinceEvictionCheck += 1
        if writesSinceEvictionCheck >= Self.evictionCheckInterval {
            writesSinceEvictionCheck = 0
            _evictReadThrough(limit: Self.readThroughLimit)
        }
    }

    /// Detail JSON of books read online but not downloaded, marked `offline_partial` for the shelf.
    func partialDetails() -> [[String: Any]] {
        ioQueue.sync {
            _cachedBookIDs().filter { !_isDownloaded($0) }.compactMap { id in
                let data = try? Data(contentsOf: cacheDir(id).appendingPathComponent("book.json"))
                guard var json = data.flatMap({ try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }) else {
                    return nil
                }
                json["offline_partial"] = true
                return json
            }
        }
    }

    func readThroughSize() -> Int64 {
        ioQueue.sync { _cachedBookIDs().reduce(0) { $0 + _folderSize(cacheDir($1)) } }
    }

    func clearReadThrough() {
        ioQueue.sync { _remove(cacheRoot) }
    }

    /// Removes whole books, least recently read first, until the cache is at most `limit` bytes.
    func evictReadThrough(limit: Int64) {
        ioQueue.sync { _evictReadThrough(limit: limit) }
    }

    private func _evictReadThrough(limit: Int64) {
        var books = _cachedBookIDs().map { id -> (id: Int, size: Int64, used: Date) in
            let dir = cacheDir(id)
            let used = (try? dir.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            return (id, _folderSize(dir), used)
        }
        var total = books.reduce(0) { $0 + $1.size }
        books.sort { $0.used < $1.used }
        for book in books where total > limit {
            _remove(cacheDir(book.id))
            total -= book.size
        }
    }

    private func _cachedBookIDs() -> [Int] {
        ((try? fm.contentsOfDirectory(atPath: cacheRoot.path)) ?? []).compactMap(Int.init)
    }

    /// The folder's modification date is its last use, which eviction orders by.
    private func _touch(_ dir: URL) {
        guard fm.fileExists(atPath: dir.path) else { return }
        do { try fm.setAttributes([.modificationDate: Date()], ofItemAtPath: dir.path) } catch {
            NSLog("Offline store touch failed: path=%@ error=%@", dir.path, error.localizedDescription)
        }
    }

    // MARK: books

    func downloadedBookIDs() -> [Int] {
        ioQueue.sync { _downloadedBookIDs() }
    }

    private func _downloadedBookIDs() -> [Int] {
        let names = (try? fm.contentsOfDirectory(atPath: booksRoot.path)) ?? []
        return names.compactMap(Int.init).filter(_isDownloaded).sorted()
    }

    func isDownloaded(_ bookID: Int) -> Bool {
        ioQueue.sync { _isDownloaded(bookID) }
    }

    private func _isDownloaded(_ bookID: Int) -> Bool {
        fm.fileExists(atPath: bookDir(bookID).appendingPathComponent("complete").path)
    }

    func markComplete(_ bookID: Int) {
        ioQueue.sync { _write(Data(), to: bookDir(bookID).appendingPathComponent("complete")) }
    }

    func removeBook(_ bookID: Int) {
        ioQueue.sync { _remove(bookDir(bookID)) }
    }

    func saveDetail(_ json: Data, bookID: Int) {
        ioQueue.sync { _write(json, to: bookDir(bookID).appendingPathComponent("book.json")) }
    }

    /// Detail JSON (`/api/books/<id>`) of every downloaded book.
    func details() -> [[String: Any]] {
        ioQueue.sync {
            _downloadedBookIDs().compactMap { id in
                let data = try? Data(contentsOf: bookDir(id).appendingPathComponent("book.json"))
                return data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            }
        }
    }

    func sizeOfBook(_ bookID: Int) -> Int64 {
        ioQueue.sync { _folderSize(bookDir(bookID)) }
    }

    private func _folderSize(_ dir: URL) -> Int64 {
        let urls = (try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.fileSizeKey])) ?? []
        return urls.reduce(0) { sum, url in
            sum + Int64((try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0)
        }
    }

    // MARK: progress

    func saveLastProgress(_ body: Data, bookID: Int) {
        ioQueue.sync { _write(body, to: bookDir(bookID).appendingPathComponent("progress.json")) }
    }

    func lastProgress(_ bookID: Int) -> [String: Any]? {
        ioQueue.sync {
            let data = try? Data(contentsOf: bookDir(bookID).appendingPathComponent("progress.json"))
            return data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
        }
    }

    func queueProgress(_ body: Data, bookID: Int) {
        ioQueue.sync { _write(body, to: pendingDir.appendingPathComponent(String(bookID))) }
    }

    func pendingProgress() -> [(bookID: Int, body: Data)] {
        ioQueue.sync {
            let names = (try? fm.contentsOfDirectory(atPath: pendingDir.path)) ?? []
            return names.compactMap { name in
                guard let id = Int(name),
                      let body = try? Data(contentsOf: pendingDir.appendingPathComponent(name)) else { return nil }
                return (id, body)
            }
        }
    }

    func clearPendingProgress(_ bookID: Int) {
        ioQueue.sync { _remove(pendingDir.appendingPathComponent(String(bookID))) }
    }

    func clearPendingProgress(_ bookID: Int, ifMatching expectedBody: Data) {
        ioQueue.sync {
            let url = pendingDir.appendingPathComponent(String(bookID))
            guard let current = try? Data(contentsOf: url), current == expectedBody else { return }
            _remove(url)
        }
    }

    // MARK: files

    private var sharedDir: URL { root.appendingPathComponent("shared", isDirectory: true) }
    private var booksRoot: URL { root.appendingPathComponent("books", isDirectory: true) }
    private var cacheRoot: URL { root.appendingPathComponent("cache", isDirectory: true) }
    private var pendingDir: URL { root.appendingPathComponent("pending-progress", isDirectory: true) }
    private func bookDir(_ bookID: Int) -> URL { booksRoot.appendingPathComponent(String(bookID), isDirectory: true) }
    private func cacheDir(_ bookID: Int) -> URL { cacheRoot.appendingPathComponent(String(bookID), isDirectory: true) }

    private func _write(_ data: Data, to url: URL) {
        do {
            try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: url, options: .atomic)
        } catch {
            NSLog("Offline store write failed: path=%@ error=%@", url.path, error.localizedDescription)
        }
    }

    private func _remove(_ url: URL) {
        guard fm.fileExists(atPath: url.path) else { return }
        do { try fm.removeItem(at: url) } catch {
            NSLog("Offline store remove failed: path=%@ error=%@", url.path, error.localizedDescription)
        }
    }

    private static func hexDigest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}
