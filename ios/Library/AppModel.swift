import Foundation
import Network

/// The pieces that exist while a server is configured; rebuilt when the user changes server.
@MainActor
final class Connection {
    let config: ServerConfig
    let client: ServerClient
    let schemeHandler: LibrarySchemeHandler
    let downloader: BookDownloader

    init(config: ServerConfig, store: OfflineStore) {
        self.config = config
        client = ServerClient(config: config)
        client.onAddressesChange = { ServerConfigStore.saveAddresses($0.addresses) }
        schemeHandler = LibrarySchemeHandler(client: client, store: store)
        downloader = BookDownloader(client: client, store: store)
    }
}

@MainActor
final class AppModel: ObservableObject {
    @Published private(set) var connection: Connection?
    @Published var isShowingSettings = false
    /// The server being replaced while the connect page shows; its saved config stays until a new one connects,
    /// so cancelling (or quitting the app) keeps it.
    @Published private(set) var replacedConfig: ServerConfig?

    let store = OfflineStore.shared
    private let pathMonitor = NWPathMonitor()

    init() {
        if let config = ServerConfigStore.load() { connect(config) }
        // Wi-Fi ↔ cellular changes which address works, so the server is probed again on every network change
        pathMonitor.pathUpdateHandler = { [weak self] _ in
            Task { @MainActor in await self?.refreshConnection() }
        }
        pathMonitor.start(queue: .main)
    }

    deinit { pathMonitor.cancel() }

    func connect(_ config: ServerConfig) {
        ServerConfigStore.save(config)
        replacedConfig = nil
        connection = Connection(config: config, store: store)
    }

    /// Re-picks the server address; called on network changes and when the app comes back to the foreground.
    func refreshConnection() async {
        await connection?.schemeHandler.checkServer()
    }

    /// Shows the connect page for another server; downloaded books stay.
    func startServerChange() {
        replacedConfig = connection?.client.config
        connection = nil
        isShowingSettings = false
    }

    func cancelServerChange() {
        guard let replacedConfig else { return }
        connect(replacedConfig)
    }
}
