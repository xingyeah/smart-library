import Foundation

/// A request as the web page made it, before it is sent to the server or answered from the offline store.
/// The query is kept exactly as the page encoded it, since re-encoding query items would leave `&` and `+`
/// inside values unescaped.
struct ProxyRequest {
    let method: String
    let path: String
    let rawQuery: String?
    let body: Data?
    let headers: [String: String]

    init(method: String, pathAndQuery: String, body: Data? = nil, headers: [String: String] = [:]) {
        let comps = URLComponents(string: pathAndQuery)
        self.method = method
        self.path = comps?.percentEncodedPath ?? pathAndQuery
        self.rawQuery = comps?.percentEncodedQuery
        self.body = body
        self.headers = headers
    }

    var pathAndQuery: String { rawQuery.map { "\(path)?\($0)" } ?? path }

    var queryItems: [URLQueryItem] {
        var comps = URLComponents()
        comps.percentEncodedQuery = rawQuery
        return comps.queryItems ?? []
    }

    func query(_ name: String) -> String? { queryItems.first { $0.name == name }?.value }

    /// `/api/books/<id>...` → id; nil for any other path.
    var bookID: Int? {
        let parts = path.split(separator: "/")
        guard parts.count >= 3, parts[0] == "api", parts[1] == "books" else { return nil }
        return Int(parts[2])
    }
}

struct ProxyResponse {
    let status: Int
    let headers: [String: String]
    let body: Data

    var contentType: String { headers["Content-Type"] ?? "application/octet-stream" }

    static func json(_ object: Any, status: Int = 200) -> ProxyResponse {
        let body = (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{}".utf8)
        return ProxyResponse(status: status, headers: ["Content-Type": "application/json; charset=utf-8"], body: body)
    }

    static let offlineUnavailable = json(["ok": false, "error": "离线不可用：此内容未下载"], status: 503)
}

/// Talks to the library server. The LAN token goes in the `lib_token` cookie the gate expects, so the web
/// page never sees it. Requests go to `activeBaseURL`; `probe()` picks it from the configured addresses.
@MainActor
final class ServerClient {
    /// Long enough for a slow PDF page render over Tailscale on cellular; a probe decides reachability instead.
    private static let requestTimeout: TimeInterval = 30
    private static let probeTimeout: TimeInterval = 4
    /// Each later address starts this much after the one before, so a LAN address that answers wins over Tailscale.
    private static let probeStagger: UInt64 = 300_000_000
    private static let probePath = "/api/connection"
    private nonisolated static let passedHeaders = ["Range", "Content-Type", "Accept", "If-None-Match"]
    private nonisolated static let returnedHeaders = ["Content-Type", "Content-Range", "Accept-Ranges", "Content-Disposition", "ETag", "Cache-Control"]

    private(set) var config: ServerConfig
    private(set) var activeBaseURL: URL
    /// Called when the address list changes (sync or delete), so it can be saved.
    var onAddressesChange: ((ServerConfig) -> Void)?
    private let session: URLSession
    private var runningProbe: Task<Bool, Never>?

    init(config: ServerConfig) {
        self.config = config
        activeBaseURL = config.addresses.first ?? config.baseURL
        let conf = URLSessionConfiguration.ephemeral
        conf.timeoutIntervalForRequest = Self.requestTimeout
        conf.httpShouldSetCookies = false
        conf.httpCookieStorage = nil
        conf.urlCache = nil
        session = URLSession(configuration: conf)
    }

    /// Throws `URLError` when the server cannot be reached; HTTP error statuses are returned, not thrown.
    func send(_ request: ProxyRequest) async throws -> ProxyResponse {
        try await Self.send(request, to: activeBaseURL, token: config.token, session: session, timeout: nil)
    }

    func get(_ pathAndQuery: String) async throws -> ProxyResponse {
        try await send(ProxyRequest(method: "GET", pathAndQuery: pathAndQuery))
    }

    /// Tries every address (staggered, first answer wins) and switches to the one that answered. Returns false when
    /// none did. Calls made while a probe runs wait for that probe.
    func probe() async -> Bool {
        if let runningProbe { return await runningProbe.value }
        let task = Task { await runProbe() }
        runningProbe = task
        let isReachable = await task.value
        runningProbe = nil
        return isReachable
    }

    private func runProbe() async -> Bool {
        let request = ProxyRequest(method: "GET", pathAndQuery: Self.probePath)
        let (token, session) = (config.token, session)
        let winner = await withTaskGroup(of: (URL, ProxyResponse)?.self) { group -> (URL, ProxyResponse)? in
            for (index, base) in config.addresses.enumerated() {
                group.addTask {
                    if index > 0 { try? await Task.sleep(nanoseconds: UInt64(index) * Self.probeStagger) }
                    guard !Task.isCancelled else { return nil }
                    let response = try? await Self.send(request, to: base, token: token, session: session,
                                                        timeout: Self.probeTimeout)
                    // 403: the gate refuses this address without a token (loopback needs none, the LAN does)
                    guard let response, response.status != 403 else { return nil }
                    return (base, response)
                }
            }
            for await result in group {
                if let result {
                    group.cancelAll()
                    return result
                }
            }
            return nil
        }
        guard let (base, _) = winner else { return false }
        activeBaseURL = base
        return true
    }

    /// The addresses the server reports at `/api/connection`; nil when it cannot be reached or does not report any
    /// (an older server answers 404).
    func fetchReportedAddresses() async -> [URL]? {
        guard let response = try? await get(Self.probePath), response.status == 200,
              let json = (try? JSONSerialization.jsonObject(with: response.body)) as? [String: Any],
              let reported = json["addresses"] as? [String] else { return nil }
        return reported.compactMap(URL.init(string:))
    }

    /// Adds the addresses not known yet and returns how many were added.
    func addAddresses(_ urls: [URL]) -> Int {
        let updated = config.adding(urls)
        let added = updated.addresses.count - config.addresses.count
        if added > 0 { apply(updated) }
        return added
    }

    func removeAddress(_ url: URL) {
        apply(config.removing(url))
        if !config.addresses.contains(activeBaseURL) { activeBaseURL = config.addresses[0] }
    }

    private func apply(_ updated: ServerConfig) {
        guard updated != config else { return }
        config = updated
        onAddressesChange?(updated)
    }

    private nonisolated static func send(_ request: ProxyRequest, to base: URL, token: String, session: URLSession,
                                         timeout: TimeInterval?) async throws -> ProxyResponse {
        guard let url = URL(string: request.pathAndQuery, relativeTo: base) else {
            throw URLError(.badURL)
        }
        var urlRequest = URLRequest(url: url)
        urlRequest.httpMethod = request.method
        urlRequest.httpBody = request.body
        if let timeout { urlRequest.timeoutInterval = timeout }
        for name in passedHeaders {
            if let value = request.headers[name] { urlRequest.setValue(value, forHTTPHeaderField: name) }
        }
        if !token.isEmpty { urlRequest.setValue("lib_token=\(token)", forHTTPHeaderField: "Cookie") }
        let (data, response) = try await session.data(for: urlRequest)
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        var headers: [String: String] = [:]
        for name in returnedHeaders {
            if let value = http.value(forHTTPHeaderField: name) { headers[name] = value }
        }
        return ProxyResponse(status: http.statusCode, headers: headers, body: data)
    }
}
