import Foundation
import Security

/// Server address and LAN token, parsed from the URL the server prints (`http://<ip>:8765/?token=…`).
/// The token is empty when the server is reached over loopback (the Simulator), which the gate allows.
/// `baseURL` is the address the user pasted; `addresses` are the addresses the app tries, in the order to prefer
/// them (LAN before Tailscale). They change only when the user connects, syncs or deletes one, never on their own.
struct ServerConfig: Equatable {
    let baseURL: URL
    let token: String
    let addresses: [URL]

    init(baseURL: URL, token: String, addresses: [URL] = []) {
        self.baseURL = baseURL
        self.token = token
        self.addresses = addresses.isEmpty ? [baseURL] : addresses
    }

    /// Appends the addresses not already known; a sync never drops one the server left out this time.
    func adding(_ urls: [URL]) -> ServerConfig {
        var merged = addresses
        for url in urls where !merged.contains(url) { merged.append(url) }
        return ServerConfig(baseURL: baseURL, token: token, addresses: merged)
    }

    /// Keeps at least one address, so the app always has somewhere to connect.
    func removing(_ url: URL) -> ServerConfig {
        let rest = addresses.filter { $0 != url }
        return rest.isEmpty ? self : ServerConfig(baseURL: baseURL, token: token, addresses: rest)
    }

    enum ParseError: LocalizedError {
        case invalid
        var errorDescription: String? { "地址无效，应形如 http://192.168.1.5:8765/?token=…" }
    }

    /// Throws `ParseError.invalid` for anything that is not an http(s) URL with a host.
    static func parse(_ text: String) throws -> ServerConfig {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var comps = URLComponents(string: trimmed),
              let scheme = comps.scheme?.lowercased(), ["http", "https"].contains(scheme),
              let host = comps.host, !host.isEmpty else { throw ParseError.invalid }
        let token = comps.queryItems?.first { $0.name == "token" }?.value ?? ""
        comps.path = ""
        comps.query = nil
        comps.fragment = nil
        guard let base = comps.url else { throw ParseError.invalid }
        return ServerConfig(baseURL: base, token: token)
    }
}

/// Keeps the base URL in UserDefaults and the token in the Keychain.
enum ServerConfigStore {
    private static let baseURLKey = "serverBaseURL"
    private static let addressesKey = "serverAddresses"
    private static let keychainService = "library.lan-token"

    static func load() -> ServerConfig? {
        guard let text = UserDefaults.standard.string(forKey: baseURLKey),
              let url = URL(string: text) else { return nil }
        let addresses = (UserDefaults.standard.stringArray(forKey: addressesKey) ?? []).compactMap(URL.init(string:))
        return ServerConfig(baseURL: url, token: readToken() ?? "", addresses: addresses)
    }

    static func save(_ config: ServerConfig) {
        UserDefaults.standard.set(config.baseURL.absoluteString, forKey: baseURLKey)
        saveAddresses(config.addresses)
        deleteToken()
        let item: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: keychainService,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
            kSecValueData as String: Data(config.token.utf8),
        ]
        let status = SecItemAdd(item as CFDictionary, nil)
        if status != errSecSuccess { NSLog("Keychain save failed: status=%d", status) }
    }

    /// The token and pasted address do not change when the server reports new addresses, so only these are saved.
    static func saveAddresses(_ addresses: [URL]) {
        UserDefaults.standard.set(addresses.map(\.absoluteString), forKey: addressesKey)
    }


    private static func readToken() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: keychainService,
            kSecReturnData as String: true,
        ]
        var out: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private static func deleteToken() {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: keychainService]
        SecItemDelete(query as CFDictionary)
    }
}
