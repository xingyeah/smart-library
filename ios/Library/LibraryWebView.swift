import Combine
import SwiftUI
import WebKit

/// The library's own web page, loaded through `LibrarySchemeHandler`. The page talks back over the `library`
/// message handler: `{action: "download" | "remove", bookId}`, `{action: "settings"}` and `{action: "ready"}` once
/// its scripts have run.
struct LibraryWebView: UIViewRepresentable {
    let connection: Connection
    let store: OfflineStore
    let onOpenSettings: () -> Void
    /// Called once the page has drawn (its `ready` message), or when it failed to load and shows WebKit's error.
    let onPageReady: () -> Void

    func makeCoordinator() -> Coordinator {
        let coordinator = Coordinator(connection: connection, store: store, onOpenSettings: onOpenSettings)
        coordinator.onPageReady = onPageReady
        return coordinator
    }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.setURLSchemeHandler(connection.schemeHandler, forURLScheme: LibrarySchemeHandler.scheme)
        configuration.userContentController.add(context.coordinator, name: Coordinator.messageName)
        configuration.allowsInlineMediaPlayback = true
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.isOpaque = false
        webView.backgroundColor = .clear
        webView.navigationDelegate = context.coordinator
        // WebKit's own back swipe previews a stale snapshot for same-page history, so the page closes its top layer itself
        let edgeSwipe = UIScreenEdgePanGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.handleEdgeSwipe(_:)))
        edgeSwipe.edges = .left
        webView.addGestureRecognizer(edgeSwipe)
        #if DEBUG
        webView.isInspectable = true
        #endif
        context.coordinator.webView = webView
        context.coordinator.installOfflineScript()
        webView.load(URLRequest(url: LibrarySchemeHandler.startURL))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}

    static func dismantleUIView(_ webView: WKWebView, coordinator: Coordinator) {
        webView.configuration.userContentController.removeScriptMessageHandler(forName: Coordinator.messageName)
    }

    @MainActor
    final class Coordinator: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
        static let messageName = "library"
        static let edgeSwipeMinDistance: CGFloat = 60
        /// A page that loaded but never says `ready` (an error page from the server) still gets shown after this.
        private static let readyFallbackDelay: UInt64 = 3_000_000_000

        weak var webView: WKWebView?
        var onPageReady: (() -> Void)?
        private let connection: Connection
        private let store: OfflineStore
        private let onOpenSettings: () -> Void
        private var offlineSubscription: AnyCancellable?

        init(connection: Connection, store: OfflineStore, onOpenSettings: @escaping () -> Void) {
            self.connection = connection
            self.store = store
            self.onOpenSettings = onOpenSettings
            super.init()
            connection.downloader.onLibraryChange = { [weak self] in self?.installOfflineScript() }
            // @Published emits before the property changes, so the script is built on the next main-queue turn
            offlineSubscription = connection.schemeHandler.$isOffline.dropFirst().removeDuplicates()
                .receive(on: DispatchQueue.main)
                .sink { [weak self] _ in self?.installOfflineScript() }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: Self.readyFallbackDelay)
                self?.onPageReady?()
            }
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            NSLog("Page load failed: %@", error.localizedDescription)
            onPageReady?()
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            NSLog("Page load failed before it started: %@", error.localizedDescription)
            onPageReady?()
        }

        @objc func handleEdgeSwipe(_ gesture: UIScreenEdgePanGestureRecognizer) {
            guard gesture.state == .ended, gesture.translation(in: gesture.view).x > Self.edgeSwipeMinDistance else { return }
            webView?.evaluateJavaScript("window.libraryAppBack && window.libraryAppBack()")
        }

        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard let body = message.body as? [String: Any], let action = body["action"] as? String else {
                NSLog("Ignored page message without an action")
                return
            }
            let bookID = (body["bookId"] as? NSNumber)?.intValue
            switch (action, bookID) {
            case ("download", let id?): Task { await connection.downloader.download(bookID: id) }
            case ("remove", let id?): connection.downloader.remove(bookID: id)
            case ("settings", _): onOpenSettings()
            // The server can go offline during the first load, after the page took its user script
            case ("ready", _):
                installOfflineScript()
                onPageReady?()
            default: NSLog("Ignored page message: action=%@", action)
            }
        }

        /// `window.libraryAppOffline` (a Set of downloaded book ids) tells the page it runs in the app and which
        /// books have an offline copy; `window.libraryAppIsOffline` whether the server is unreachable. Both are set
        /// before the page's scripts run and refreshed on every change, when the page's
        /// `libraryAppSetOffline` is also called.
        func installOfflineScript() {
            let ids = store.downloadedBookIDs().map(String.init).joined(separator: ",")
            let isOffline = connection.schemeHandler.isOffline
            let source = "window.libraryAppOffline = new Set([\(ids)]); window.libraryAppIsOffline = \(isOffline);"
                + " if (window.libraryAppSetOffline) window.libraryAppSetOffline(\(isOffline));"
            guard let controller = webView?.configuration.userContentController else { return }
            controller.removeAllUserScripts()
            controller.addUserScript(WKUserScript(source: source, injectionTime: .atDocumentStart, forMainFrameOnly: true))
            webView?.evaluateJavaScript(source) { _, error in
                if let error { NSLog("Offline list update failed: %@", error.localizedDescription) }
            }
        }
    }
}
