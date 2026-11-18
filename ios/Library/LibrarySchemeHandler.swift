import Combine
import Foundation
import WebKit

/// Serves `library://app/...` to the web view. Each request is forwarded to the server; the page shell,
/// translations and the books read online are stored on the way through, progress is queued while offline, and
/// when the server cannot be reached `OfflineResponder` answers instead.
///
/// A failed request is not proof the server is gone: the client first probes every address and retries once on
/// the one that answers. Only when no address answers does the app go offline, and then it probes again every
/// `recoveryInterval` until one does.
@MainActor
final class LibrarySchemeHandler: NSObject, WKURLSchemeHandler, ObservableObject {
    static let scheme = "library"
    static let startURL = URL(string: "library://app/")!
    private static let recoveryInterval: UInt64 = 10_000_000_000

    @Published private(set) var isOffline = false
    /// False until the first probe has picked an address or found none; the launch screen shows this.
    @Published private(set) var hasCheckedServer = false
    private let client: ServerClient
    private let store: OfflineStore
    private var activeTasks = [ObjectIdentifier: Task<Void, Never>]()
    private var hasFlushedProgress = false
    private var isFlushing = false
    private var recoveryTask: Task<Void, Never>?
    /// The first request waits for this, so a LAN address that cannot be reached from cellular is never tried first.
    private var startupCheck: Task<Void, Never>?

    init(client: ServerClient, store: OfflineStore) {
        self.client = client
        self.store = store
        super.init()
        startupCheck = Task {
            await checkServer()
            hasCheckedServer = true
        }
    }

    /// Re-picks the address (for example after Wi-Fi ↔ cellular) and updates the online state.
    func checkServer() async {
        if await client.probe() { markOnline() } else { markOffline() }
    }

    private enum FetchOutcome {
        case response(ProxyResponse)
        case offlineFallback
        case cancelled
    }

    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        let key = ObjectIdentifier(task)
        let request = Self.proxyRequest(from: task.request)
        activeTasks[key] = Task { [weak self] in
            guard let self else { return }
            let response = await self.respondIfActive(to: request)
            guard !Task.isCancelled, let response else {
                self.activeTasks.removeValue(forKey: key)
                return
            }
            self.finish(task, with: response)
        }
    }

    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {
        activeTasks.removeValue(forKey: ObjectIdentifier(task))?.cancel()
    }

    func respond(to request: ProxyRequest) async -> ProxyResponse {
        await respondCore(to: request) ?? OfflineResponder.respond(to: request, store: store)
    }

    private func respondIfActive(to request: ProxyRequest) async -> ProxyResponse? {
        guard !Task.isCancelled else { return nil }
        return await respondCore(to: request)
    }

    private func respondCore(to request: ProxyRequest) async -> ProxyResponse? {
        if request.method == "POST", let bookID = request.bookID, request.path.hasSuffix("/progress") {
            return await saveProgress(request, bookID: bookID)
        }
        await startupCheck?.value
        guard !Task.isCancelled else { return nil }
        if isOffline { return OfflineResponder.respond(to: request, store: store) }
        switch await sendWithRetry(request) {
        case .cancelled:
            return nil
        case .offlineFallback:
            return OfflineResponder.respond(to: request, store: store)
        case let .response(response):
            remember(response, for: request)
            return response
        }
    }

    private func sendWithRetry(_ request: ProxyRequest) async -> FetchOutcome {
        guard !Task.isCancelled else { return .cancelled }
        do {
            let response = try await client.send(request)
            markOnline()
            return .response(response)
        } catch is CancellationError {
            return .cancelled
        } catch let error as URLError where error.code == .cancelled {
            return .cancelled
        } catch {
            NSLog("Request failed, probing addresses: path=%@ error=%@", request.path, error.localizedDescription)
        }
        guard !Task.isCancelled else { return .cancelled }
        guard await client.probe() else {
            guard !Task.isCancelled else { return .cancelled }
            markOffline()
            return .offlineFallback
        }
        guard !Task.isCancelled else { return .cancelled }
        do {
            let response = try await client.send(request)
            markOnline()
            return .response(response)
        } catch is CancellationError {
            return .cancelled
        } catch let error as URLError where error.code == .cancelled {
            return .cancelled
        } catch {
            // The server answered the probe, so one slow request does not make the app offline
            NSLog("Request failed after retry: path=%@ error=%@", request.path, error.localizedDescription)
            return .offlineFallback
        }
    }

    // MARK: progress

    private func saveProgress(_ request: ProxyRequest, bookID: Int) async -> ProxyResponse {
        let body = Self.stampOpenedAt(request.body ?? Data())
        store.saveLastProgress(body, bookID: bookID)
        let stamped = ProxyRequest(method: "POST", pathAndQuery: request.pathAndQuery, body: body, headers: request.headers)
        await startupCheck?.value
        if !isOffline, case let .response(response) = await sendWithRetry(stamped) { return response }
        store.queueProgress(body, bookID: bookID)
        return .json(["ok": true, "queued": true])
    }

    /// The server keeps the newest progress by `opened_at`, so a queued save replayed later must carry the time
    /// it was made.
    static func stampOpenedAt(_ body: Data) -> Data {
        guard var json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] else { return body }
        if json["opened_at"] == nil { json["opened_at"] = Int(Date().timeIntervalSince1970 * 1000) }
        return (try? JSONSerialization.data(withJSONObject: json)) ?? body
    }

    private func flushPendingProgress() async {
        guard !isFlushing else { return }
        isFlushing = true
        defer { isFlushing = false }
        for item in store.pendingProgress() {
            let request = ProxyRequest(method: "POST", pathAndQuery: "/api/books/\(item.bookID)/progress",
                                       body: item.body, headers: ["Content-Type": "application/json"])
            do {
                let response = try await client.send(request)
                // A 4xx body will never be accepted; dropping it keeps the queue from sticking
                if response.status < 500 { store.clearPendingProgress(item.bookID, ifMatching: item.body) }
            } catch {
                NSLog("Progress replay failed: bookId=%d error=%@", item.bookID, error.localizedDescription)
                return
            }
        }
    }

    // MARK: online state

    private func markOnline() {
        let wasOffline = isOffline
        if wasOffline { isOffline = false }
        recoveryTask?.cancel()
        recoveryTask = nil
        if wasOffline || !hasFlushedProgress {
            hasFlushedProgress = true
            Task { await flushPendingProgress() }
        }
    }

    private func markOffline() {
        guard !isOffline else { return }
        NSLog("No server address answered, using offline store")
        isOffline = true
        recoveryTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: Self.recoveryInterval)
                guard !Task.isCancelled, let self else { return }
                if await self.client.probe() {
                    self.markOnline()
                    return
                }
            }
        }
    }

    // MARK: helpers

    /// Whole book files are too big to keep as a side effect of reading; progress is stored separately.
    private static let uncachedBookSuffixes = ["/stream", "/download", "/progress"]

    private func remember(_ response: ProxyResponse, for request: ProxyRequest) {
        guard response.status == 200 else { return }
        if request.method == "GET", let bookID = request.bookID, !store.isDownloaded(bookID),
           !Self.uncachedBookSuffixes.contains(where: request.path.hasSuffix) {
            store.putReadThroughAsync(response, for: request, bookID: bookID)
            return
        }
        let isShell = request.path == "/" || request.path == "/index.html"
            || request.path.hasPrefix("/vendor/") || request.path.hasPrefix("/assets/")
        let isTranslation = request.method == "POST" && request.path.hasPrefix("/api/translate")
        // shortcut: translations are kept forever with no size cap; add eviction if the folder grows large
        guard isShell || isTranslation else { return }
        store.putAsync(response, key: OfflineStore.key(for: OfflineResponder.shellRequest(request)), bookID: nil)
    }

    private func finish(_ task: WKURLSchemeTask, with response: ProxyResponse) {
        guard activeTasks.removeValue(forKey: ObjectIdentifier(task)) != nil, let url = task.request.url else { return }
        var headers = response.headers
        if response.status != 204 && response.status != 304 {
            headers["Content-Length"] = String(response.body.count)
        }
        let path = url.path
        let isShellPageOrAppBundle = path.isEmpty || path == "/" || path == "/index.html" || path.hasPrefix("/assets/")
        if isShellPageOrAppBundle || headers["Cache-Control"] == nil {
            // Keep app shell/assets fresh on update while honoring backend cache headers on covers, vendor libs, and rendered pages
            headers["Cache-Control"] = "no-cache"
        }
        guard let http = HTTPURLResponse(url: url, statusCode: response.status, httpVersion: "HTTP/1.1",
                                         headerFields: headers) else {
            task.didFailWithError(URLError(.badServerResponse))
            return
        }
        task.didReceive(http)
        if !response.body.isEmpty { task.didReceive(response.body) }
        task.didFinish()
    }

    private static func proxyRequest(from request: URLRequest) -> ProxyRequest {
        let comps = request.url.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false) }
        let path = comps?.percentEncodedPath.isEmpty == false ? comps!.percentEncodedPath : "/"
        let pathAndQuery = comps?.percentEncodedQuery.map { "\(path)?\($0)" } ?? path
        return ProxyRequest(method: request.httpMethod ?? "GET", pathAndQuery: pathAndQuery,
                            body: request.httpBody ?? readStream(request.httpBodyStream),
                            headers: request.allHTTPHeaderFields ?? [:])
    }

    private static func readStream(_ stream: InputStream?) -> Data? {
        guard let stream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 16_384)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            data.append(buffer, count: count)
        }
        return data
    }
}
