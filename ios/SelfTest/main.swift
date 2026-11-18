// Self-check of the app's proxy and offline logic, run on macOS against the local server:
//   ios/SelfTest/run.sh [epubBookId] [pdfBookId]
// It downloads two books into a temporary store, then answers requests with the server unreachable.
import Foundation

@main
@MainActor
enum SelfTest {
    static var failures = 0

    static func check(_ condition: Bool, _ label: String) {
        print(condition ? "PASS" : "FAIL", label)
        if !condition { failures += 1 }
    }

    static func main() async {
        let args = CommandLine.arguments
        let epubID = args.count > 1 ? Int(args[1]) ?? 7332 : 7332
        let pdfID = args.count > 2 ? Int(args[2]) ?? 4935 : 4935
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("library-selftest-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let store = OfflineStore(root: root)

        checkPureLogic()
        await checkDownloads(store: store, bookIDs: [epubID, pdfID])
        await checkOffline(store: store, epubID: epubID, pdfID: pdfID)
        await checkProgressReplay(store: store)
        await checkAddressFailover()
        await checkReadThrough(pdfID: pdfID)
        print(failures == 0 ? "ALL PASSED" : "\(failures) FAILED")
        exit(failures == 0 ? 0 : 1)
    }

    static func checkPureLogic() {
        let config = try? ServerConfig.parse(" http://192.168.1.5:8765/?token=abc ")
        check(config?.baseURL.absoluteString == "http://192.168.1.5:8765" && config?.token == "abc", "parse token URL")
        check((try? ServerConfig.parse("not a url")) == nil, "reject bad URL")
        let withV = ProxyRequest(method: "GET", pathAndQuery: "/api/books/1/pdf_page/3?v=abc")
        let without = ProxyRequest(method: "GET", pathAndQuery: "/api/books/1/pdf_page/3")
        check(OfflineStore.key(for: withV) == OfflineStore.key(for: without), "cache key ignores v")
        let asset = ProxyRequest(method: "GET", pathAndQuery: "/api/books/1/epub_asset?path=a%26b.jpg")
        check(asset.query("path") == "a&b.jpg" && asset.pathAndQuery.hasSuffix("a%26b.jpg"), "query kept as encoded")
        let html = #"<img src="/api/books/9/epub_asset?path=OEBPS%2Fi.jpg&amp;x=1"/>"#
        check(BookDownloader.assetPaths(in: html) == ["/api/books/9/epub_asset?path=OEBPS%2Fi.jpg&x=1"], "asset URL extraction")
        let stamped = LibrarySchemeHandler.stampOpenedAt(Data(#"{"chapter_idx":2}"#.utf8))
        let json = (try? JSONSerialization.jsonObject(with: stamped)) as? [String: Any]
        check(json?["opened_at"] != nil && json?["chapter_idx"] as? Int == 2, "progress gets opened_at")
    }

    static func checkDownloads(store: OfflineStore, bookIDs: [Int]) async {
        let online = ServerClient(config: ServerConfig(baseURL: URL(string: "http://127.0.0.1:8765")!, token: ""))
        let downloader = BookDownloader(client: online, store: store)
        for id in bookIDs {
            let started = Date()
            await downloader.download(bookID: id)
            let seconds = Int(Date().timeIntervalSince(started))
            check(store.isDownloaded(id), "download book \(id) (\(seconds)s, \(store.sizeOfBook(id) / 1024) KB) \(downloader.status?.error ?? "")")
            downloader.dismissError()
        }
        let handler = LibrarySchemeHandler(client: online, store: store)
        for path in ["/", "/vendor/pdf.min.js"] {
            let response = await handler.respond(to: ProxyRequest(method: "GET", pathAndQuery: path))
            check(response.status == 200, "online shell \(path)")
        }
    }

    static func checkOffline(store: OfflineStore, epubID: Int, pdfID: Int) async {
        let dead = ServerClient(config: ServerConfig(baseURL: URL(string: "http://127.0.0.1:1")!, token: ""))
        let handler = LibrarySchemeHandler(client: dead, store: store)
        func get(_ path: String) async -> ProxyResponse {
            await handler.respond(to: ProxyRequest(method: "GET", pathAndQuery: path))
        }
        check((await get("/")).status == 200, "offline page shell")
        check((await get("/index.html")).status == 200, "offline /index.html")
        let shelf = (try? JSONSerialization.jsonObject(with: (await get("/api/books?q=&page=1")).body)) as? [String: Any]
        let ids = Set(((shelf?["items"] as? [[String: Any]]) ?? []).compactMap { ($0["id"] as? NSNumber)?.intValue })
        check(ids == [epubID, pdfID], "offline shelf lists downloaded books")
        let resume = (try? JSONSerialization.jsonObject(with: (await get("/api/books/\(epubID)/read")).body)) as? [String: Any]
        check(resume?["ok"] as? Bool == true, "offline resume chapter")
        check((await get("/api/books/\(epubID)/read?chapter=1")).status == 200, "offline chapter 1")
        check((await get("/api/books/\(pdfID)/read")).status == 200, "offline PDF read")
        check((await get("/api/books/\(pdfID)/pdf_layout")).status == 200, "offline PDF layout")
        let page = await get("/api/books/\(pdfID)/pdf_page/3?v=whatever")
        check(page.status == 200 && page.contentType.contains("jpeg"), "offline PDF page image")
        check((await get("/api/books/\(pdfID)/pdf_text/3")).status == 200, "offline PDF text")
        check((await get("/api/stats")).status == 503, "offline uncached → 503")
        let progress = ProxyRequest(method: "POST", pathAndQuery: "/api/books/\(epubID)/progress",
                                    body: Data(#"{"chapter_idx":1,"offset":0.5}"#.utf8))
        let saved = await handler.respond(to: progress)
        check(saved.status == 200 && store.pendingProgress().contains { $0.bookID == epubID }, "offline progress queued")
        let resumed = (try? JSONSerialization.jsonObject(with: (await get("/api/books/\(epubID)/read")).body)) as? [String: Any]
        check(resumed?["current_chapter"] as? Int == 1, "offline resume follows local progress")
        store.clearPendingProgress(epubID)  // never replay test progress into the real library
    }

    /// The first address is dead; the probe must switch to the live one and requests must then go there.
    static func checkAddressFailover() async {
        let dead = URL(string: "http://127.0.0.1:1")!
        let live = URL(string: "http://127.0.0.1:8765")!
        let client = ServerClient(config: ServerConfig(baseURL: live, token: "", addresses: [dead, live]))
        check(client.activeBaseURL == dead, "starts on the first address")
        let isReachable = await client.probe()
        check(isReachable && client.activeBaseURL == live, "probe fails over to the live address")
        check((try? await client.get("/api/stats"))?.status == 200, "requests use the live address")
        // Without a token the LAN address answers 403; loopback must win even though the LAN comes first
        let lan = await lanURL()
        check(lan != nil, "server reports a LAN address")
        let lanFirst = ServerClient(config: ServerConfig(baseURL: live, token: "", addresses: [lan ?? dead, live]))
        let lanReachable = await lanFirst.probe()
        check(lanReachable && lanFirst.activeBaseURL == live, "address answering 403 is skipped")
        check(lanFirst.config.addresses == [lan ?? dead, live], "probe never changes the saved addresses")
        let reported = await lanFirst.fetchReportedAddresses()
        check(reported?.isEmpty == false, "server reports its addresses")
        check(lanFirst.addAddresses(reported ?? []) == 0 && lanFirst.addAddresses([dead]) == 1, "sync adds only new addresses")
        lanFirst.removeAddress(live)
        check(!lanFirst.config.addresses.contains(live) && lanFirst.activeBaseURL != live, "removing the active address moves off it")
        let single = ServerConfig(baseURL: live, token: "")
        check(single.removing(live).addresses == [live], "the last address cannot be removed")
        let none = ServerClient(config: ServerConfig(baseURL: dead, token: ""))
        check(!(await none.probe()), "probe reports no address answering")
    }

    /// The LAN address the local server reports first in `/api/connection`.
    static func lanURL() async -> URL? {
        let client = ServerClient(config: ServerConfig(baseURL: URL(string: "http://127.0.0.1:8765")!, token: ""))
        let body = (try? await client.get("/api/connection"))?.body ?? Data()
        let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
        return ((json?["addresses"] as? [String])?.first).flatMap(URL.init(string:))
    }

    /// A book read online (not downloaded) is cached on the way through, listed as partial offline, and evicted.
    static func checkReadThrough(pdfID: Int) async {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("library-readthrough-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let store = OfflineStore(root: root)
        let online = LibrarySchemeHandler(
            client: ServerClient(config: ServerConfig(baseURL: URL(string: "http://127.0.0.1:8765")!, token: "")),
            store: store)
        for path in ["/assets/js/main.js", "/api/books/\(pdfID)", "/api/books/\(pdfID)/pdf_page/2?v=x"] {
            let response = await online.respond(to: ProxyRequest(method: "GET", pathAndQuery: path))
            check(response.status == 200, "online \(path)")
        }
        let offline = LibrarySchemeHandler(
            client: ServerClient(config: ServerConfig(baseURL: URL(string: "http://127.0.0.1:1")!, token: "")),
            store: store)
        func get(_ path: String) async -> ProxyResponse {
            await offline.respond(to: ProxyRequest(method: "GET", pathAndQuery: path))
        }
        check((await get("/assets/js/main.js")).status == 200, "offline /assets script")
        let shelf = (try? JSONSerialization.jsonObject(with: (await get("/api/books?q=")).body)) as? [String: Any]
        let items = (shelf?["items"] as? [[String: Any]]) ?? []
        check(items.count == 1 && items.first?["offline_partial"] as? Bool == true, "offline shelf lists partial book")
        check((await get("/api/books/\(pdfID)/pdf_page/2")).status == 200, "offline page read online before")
        check((await get("/api/books/\(pdfID)/pdf_page/3")).status == 503, "offline page never read → 503")
        store.evictReadThrough(limit: 0)
        check(store.partialDetails().isEmpty && store.readThroughSize() == 0, "eviction removes cached books")
    }

    /// Replays a queued save for a book id that does not exist, so the server accepts it and changes nothing.
    static func checkProgressReplay(store: OfflineStore) async {
        let missingBook = 999_999_999
        store.queueProgress(Data(#"{"chapter_idx":0,"opened_at":1000}"#.utf8), bookID: missingBook)
        let online = ServerClient(config: ServerConfig(baseURL: URL(string: "http://127.0.0.1:8765")!, token: ""))
        let handler = LibrarySchemeHandler(client: online, store: store)
        _ = await handler.respond(to: ProxyRequest(method: "GET", pathAndQuery: "/api/stats"))
        for _ in 0..<50 where !store.pendingProgress().isEmpty { try? await Task.sleep(nanoseconds: 100_000_000) }
        check(store.pendingProgress().isEmpty, "queued progress replayed when back online")
    }
}
