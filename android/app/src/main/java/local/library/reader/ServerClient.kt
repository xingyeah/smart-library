package local.library.reader

import android.util.Log
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONException
import org.json.JSONObject

private const val LOG_TAG = "ServerClient"
/** Long enough for a slow PDF page render over Tailscale on cellular; a probe decides reachability instead. */
private const val REQUEST_TIMEOUT_MS = 30_000
private const val PROBE_TIMEOUT_MS = 4_000
/** Each later address starts this much after the one before, so a LAN address that answers wins over Tailscale. */
private const val PROBE_STAGGER_MS = 300L
/** Answers without a token, so the token is never sent to an address not (yet) known to be this server. */
private const val PING_PATH = "/api/ping"
private const val PING_APP_NAME = "smart-library"
private const val CONNECTION_PATH = "/api/connection"
private const val TOKEN_COOKIE_NAME = "lib_token"
private val PASSED_HEADERS = listOf("Range", "Content-Type", "Accept", "If-None-Match")
private val RETURNED_HEADERS =
    listOf("Content-Type", "Content-Range", "Accept-Ranges", "Content-Disposition", "ETag", "Cache-Control")

/**
 * Talks to the library server. The LAN token goes in the `lib_token` cookie the gate expects, so the web page
 * never sees it. Requests go to [activeBaseUrl]; [probe] picks it from the configured addresses.
 * [send] blocks, so call it from a background thread.
 */
class ServerClient(initialConfig: ServerConfig) {
    @Volatile var config: ServerConfig = initialConfig
        private set
    @Volatile var activeBaseUrl: String = initialConfig.addresses.first()
        private set

    /** Called when the address list changes (sync or delete), so it can be saved. */
    var onAddressesChange: ((ServerConfig) -> Unit)? = null

    private val probeScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var runningProbe: Deferred<Boolean>? = null

    /** Throws [IOException] when the server cannot be reached; HTTP error statuses are returned, not thrown. */
    @Throws(IOException::class)
    fun send(request: ProxyRequest): ProxyResponse =
        send(request, activeBaseUrl, config.token, REQUEST_TIMEOUT_MS)

    @Throws(IOException::class)
    fun get(pathAndQuery: String): ProxyResponse = send(ProxyRequest("GET", pathAndQuery))

    /**
     * Tries every address (staggered, first answer wins) and switches to the one that answered. Returns false
     * when none did. Calls made while a probe runs wait for that probe.
     */
    suspend fun probe(): Boolean {
        val shared = synchronized(this) {
            runningProbe ?: probeScope.async { runProbe() }.also { runningProbe = it }
        }
        try {
            return shared.await()
        } finally {
            synchronized(this) { if (runningProbe === shared && shared.isCompleted) runningProbe = null }
        }
    }

    private suspend fun runProbe(): Boolean {
        val addresses = config.addresses
        val winner = CompletableDeferred<String?>()
        val remaining = AtomicInteger(addresses.size)
        // Blocking sockets cannot be cancelled, so the attempts run outside this scope and are left to time out
        val attempts = addresses.mapIndexed { index, base ->
            probeScope.launch {
                if (index > 0) delay(index * PROBE_STAGGER_MS)
                if (answersAsLibrary(base)) winner.complete(base)
                else if (remaining.decrementAndGet() == 0) winner.complete(null)
            }
        }
        val base = winner.await()
        attempts.forEach { it.cancel() }
        if (base != null) activeBaseUrl = base
        return base != null
    }

    private fun answersAsLibrary(base: String): Boolean {
        val response = try {
            send(ProxyRequest("GET", PING_PATH), base, "", PROBE_TIMEOUT_MS)
        } catch (error: IOException) {
            Log.d(LOG_TAG, "Probe failed: address=$base error=${error.message}")
            return false
        }
        // Anything else answering on this address (a router page, another device) is not the library
        return response.status == 200 && isLibraryPing(response.body)
    }

    private fun isLibraryPing(body: ByteArray): Boolean = try {
        JSONObject(String(body)).optString("app") == PING_APP_NAME
    } catch (error: JSONException) {
        false
    }

    /**
     * The addresses the server reports at `/api/connection`; null when it cannot be reached or does not report
     * any (an older server answers 404).
     */
    fun fetchReportedAddresses(): List<String>? {
        val response = try {
            get(CONNECTION_PATH)
        } catch (error: IOException) {
            Log.w(LOG_TAG, "Could not fetch reported addresses: address=$activeBaseUrl", error)
            return null
        }
        if (response.status != 200) return null
        val reported = try {
            JSONObject(String(response.body)).optJSONArray("addresses")
        } catch (error: JSONException) {
            Log.w(LOG_TAG, "Reported addresses are not JSON: address=$activeBaseUrl", error)
            null
        } ?: return null
        return (0 until reported.length()).map { reported.getString(it) }
    }

    fun close() {
        probeScope.cancel()
    }

    /** Adds the addresses not known yet and returns how many were added. */
    fun addAddresses(urls: List<String>): Int {
        val updated = config.adding(urls)
        val added = updated.addresses.size - config.addresses.size
        if (added > 0) apply(updated)
        return added
    }

    fun removeAddress(url: String) {
        apply(config.removing(url))
        if (activeBaseUrl !in config.addresses) activeBaseUrl = config.addresses.first()
    }

    private fun apply(updated: ServerConfig) {
        if (updated == config) return
        config = updated
        onAddressesChange?.invoke(updated)
    }

    @Throws(IOException::class)
    private fun send(request: ProxyRequest, base: String, token: String, timeoutMs: Int): ProxyResponse {
        val connection = URL(base + request.pathAndQuery).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = request.method
            connection.connectTimeout = timeoutMs
            connection.readTimeout = timeoutMs
            for (name in PASSED_HEADERS) request.headers[name]?.let { connection.setRequestProperty(name, it) }
            if (token.isNotEmpty()) connection.setRequestProperty("Cookie", "$TOKEN_COOKIE_NAME=$token")
            request.body?.let { body ->
                connection.doOutput = true
                connection.outputStream.use { it.write(body) }
            }
            return readResponse(connection)
        } catch (error: IOException) {
            connection.disconnect()
            throw error
        }
    }

    private fun readResponse(connection: HttpURLConnection): ProxyResponse {
        val status = connection.responseCode
        val stream = if (status >= HttpURLConnection.HTTP_BAD_REQUEST) connection.errorStream else connection.inputStream
        val body = stream?.use { it.readBytes() } ?: ByteArray(0)
        val headers = RETURNED_HEADERS.mapNotNull { name ->
            connection.getHeaderField(name)?.let { name to it }
        }.toMap()
        return ProxyResponse(status, headers, body)
    }
}
