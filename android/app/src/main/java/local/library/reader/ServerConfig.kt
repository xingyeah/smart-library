package local.library.reader

import java.net.URI
import java.net.URISyntaxException
import java.net.URLDecoder

private const val ADDRESS_ERROR_TEXT = "地址无效，应形如 http://192.168.1.5:8765/?token=…"
private const val TOKEN_QUERY_NAME = "token"
private val ALLOWED_SCHEMES = setOf("http", "https")

class InvalidServerAddressException : Exception(ADDRESS_ERROR_TEXT)

/**
 * Server address and LAN token, parsed from the URL the server prints (`http://<ip>:8765/?token=…`).
 * `baseUrl` is the address the user pasted; `addresses` are the ones the app tries, in the order to prefer them
 * (LAN before Tailscale). They change only when the user connects, syncs or deletes one, never on their own.
 */
data class ServerConfig(val baseUrl: String, val token: String, val addresses: List<String>) {
    init {
        require(addresses.isNotEmpty()) { "A server config needs at least one address: baseUrl=$baseUrl" }
    }

    /** Appends the addresses not already known; a sync never drops one the server left out this time. */
    fun adding(urls: List<String>): ServerConfig {
        val merged = addresses.toMutableList()
        for (url in urls.map(::normalizeAddress)) if (url !in merged) merged.add(url)
        return copy(addresses = merged)
    }

    /**
     * This config with [savedToken] when the pasted address brought no token of its own (the saved token of the
     * server being replaced); a token in the address always wins.
     */
    fun withFallbackToken(savedToken: String): ServerConfig =
        if (token.isEmpty() && savedToken.isNotEmpty()) copy(token = savedToken) else this

    /** Keeps at least one address, so the app always has somewhere to connect. */
    fun removing(url: String): ServerConfig {
        val rest = addresses.filter { it != normalizeAddress(url) }
        return if (rest.isEmpty()) this else copy(addresses = rest)
    }

    companion object {
        fun of(baseUrl: String, token: String, addresses: List<String> = emptyList()): ServerConfig =
            ServerConfig(baseUrl, token, addresses.map(::normalizeAddress).ifEmpty { listOf(baseUrl) })

        /** Throws [InvalidServerAddressException] for anything that is not an http(s) URL with a host. */
        fun parse(text: String): ServerConfig {
            val uri = try {
                URI(text.trim())
            } catch (error: URISyntaxException) {
                throw InvalidServerAddressException().apply { initCause(error) }
            }
            val scheme = uri.scheme?.lowercase()
            val host = uri.host
            if (scheme !in ALLOWED_SCHEMES || host.isNullOrEmpty()) throw InvalidServerAddressException()
            val port = if (uri.port >= 0) ":${uri.port}" else ""
            return of("$scheme://$host$port", tokenIn(uri.rawQuery))
        }

        private fun tokenIn(rawQuery: String?): String {
            val item = rawQuery.orEmpty().split('&').firstOrNull { it.startsWith("$TOKEN_QUERY_NAME=") }
            return item?.let { URLDecoder.decode(it.substringAfter('='), "UTF-8") } ?: ""
        }

        fun normalizeAddress(url: String): String = url.trim().trimEnd('/')
    }
}
