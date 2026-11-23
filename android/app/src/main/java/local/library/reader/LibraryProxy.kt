package local.library.reader

import android.util.Log
import java.io.IOException
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.json.JSONException
import org.json.JSONObject

private const val LOG_TAG = "LibraryProxy"
private const val RECOVERY_INTERVAL_MS = 10_000L
private const val STATUS_SERVER_ERROR = 500
/** Whole book files are too big to keep as a side effect of reading; progress is stored separately. */
private val UNCACHED_BOOK_SUFFIXES = listOf("/stream", "/download", "/progress")
private val SHELL_PREFIXES = listOf("/vendor/", "/assets/")

/**
 * Answers the web view's requests for `https://library.app/...`. Each request is forwarded to the server; the page
 * shell, translations and the books read online are stored on the way through, progress is queued while offline,
 * and when the server cannot be reached [OfflineResponder] answers instead.
 *
 * A failed request is not proof the server is gone: the client first probes every address and retries once on
 * the one that answers. Only when no address answers does the app go offline, and then it probes again every
 * [RECOVERY_INTERVAL_MS] until one does. [handle] blocks, so it runs on the web view's own request threads.
 */
class LibraryProxy(
    private val client: ServerClient,
    private val store: OfflineStore,
    private val scope: CoroutineScope,
) {
    private val responder = OfflineResponder(store)
    private val offlineState = MutableStateFlow(false)
    private val checkedState = MutableStateFlow(false)
    private val isFlushing = AtomicBoolean(false)
    private var hasFlushedProgress = false
    private var recoveryJob: Job? = null

    val isOffline: StateFlow<Boolean> = offlineState

    /** False until the first probe has picked an address or found none; the launch screen shows this. */
    val hasCheckedServer: StateFlow<Boolean> = checkedState

    /** The first request waits for this, so a LAN address that cannot be reached from cellular is never tried first. */
    private val startupCheck: Job = scope.launch {
        checkServer()
        checkedState.value = true
    }

    /** Re-picks the address (for example after Wi-Fi to cellular) and updates the online state. */
    suspend fun checkServer() {
        if (client.probe()) markOnline() else markOffline()
    }

    fun handle(request: ProxyRequest): ProxyResponse {
        val bookId = request.bookId
        if (request.method == "POST" && bookId != null && request.path.endsWith("/progress")) {
            return saveProgress(request, bookId)
        }
        runBlocking { startupCheck.join() }
        if (offlineState.value) return responder.respond(request)
        val response = sendWithRetry(request) ?: return responder.respond(request)
        remember(response, request)
        return response
    }

    /** Null means the server cannot be reached and the offline store should answer. */
    private fun sendWithRetry(request: ProxyRequest): ProxyResponse? {
        try {
            return client.send(request).also { markOnline() }
        } catch (error: IOException) {
            Log.w(LOG_TAG, "Request failed, probing addresses: path=${request.path}", error)
        }
        if (!runBlocking { client.probe() }) {
            markOffline()
            return null
        }
        return try {
            client.send(request).also { markOnline() }
        } catch (error: IOException) {
            // The server answered the probe, so one slow request does not make the app offline
            Log.w(LOG_TAG, "Request failed after retry: path=${request.path}", error)
            null
        }
    }

    // MARK: progress

    private fun saveProgress(request: ProxyRequest, bookId: Int): ProxyResponse {
        val body = stampOpenedAt(request.body ?: ByteArray(0))
        store.saveLastProgress(body, bookId)
        val stamped = ProxyRequest("POST", request.pathAndQuery, body, request.headers)
        runBlocking { startupCheck.join() }
        if (!offlineState.value) sendWithRetry(stamped)?.let { return it }
        store.queueProgress(body, bookId)
        return ProxyResponse.json(JSONObject().put("ok", true).put("queued", true))
    }

    private suspend fun flushPendingProgress() {
        if (!isFlushing.compareAndSet(false, true)) return
        try {
            for ((bookId, body) in store.pendingProgress()) {
                val request = ProxyRequest(
                    "POST", "/api/books/$bookId/progress", body, mapOf("Content-Type" to "application/json"),
                )
                try {
                    // A 4xx body will never be accepted; dropping it keeps the queue from sticking
                    if (client.send(request).status < STATUS_SERVER_ERROR) store.clearPendingProgress(bookId, body)
                } catch (error: IOException) {
                    Log.w(LOG_TAG, "Progress replay failed: bookId=$bookId", error)
                    return
                }
            }
        } finally {
            isFlushing.set(false)
        }
    }

    // MARK: online state

    private fun markOnline() {
        val shouldFlush: Boolean
        synchronized(this) {
            val wasOffline = offlineState.value
            offlineState.value = false
            recoveryJob?.cancel()
            recoveryJob = null
            shouldFlush = wasOffline || !hasFlushedProgress
            hasFlushedProgress = true
        }
        if (shouldFlush) scope.launch { flushPendingProgress() }
    }

    private fun markOffline() {
        synchronized(this) {
            if (offlineState.value) return
            Log.i(LOG_TAG, "No server address answered, using offline store")
            offlineState.value = true
            recoveryJob = scope.launch {
                while (isActive) {
                    delay(RECOVERY_INTERVAL_MS)
                    if (client.probe()) {
                        markOnline()
                        return@launch
                    }
                }
            }
        }
    }

    // MARK: storing

    private fun remember(response: ProxyResponse, request: ProxyRequest) {
        if (response.status != 200) return
        val bookId = request.bookId
        if (request.method == "GET" && bookId != null && !store.isDownloaded(bookId) &&
            UNCACHED_BOOK_SUFFIXES.none { request.path.endsWith(it) }
        ) {
            store.putReadThroughAsync(response, request, bookId)
            return
        }
        val isShell = request.path == "/" || request.path == "/index.html" || SHELL_PREFIXES.any { request.path.startsWith(it) }
        val isTranslation = request.method == "POST" && request.path.startsWith("/api/translate")
        // shortcut: translations are kept forever with no size cap; add eviction if the folder grows large
        if (!isShell && !isTranslation) return
        store.putAsync(response, OfflineStore.key(OfflineResponder.shellRequest(request)), null)
    }

    companion object {
        /**
         * The server keeps the newest progress by `opened_at`, so a queued save replayed later must carry the time
         * it was made.
         */
        fun stampOpenedAt(body: ByteArray): ByteArray {
            val json = try {
                JSONObject(String(body))
            } catch (error: JSONException) {
                Log.w(LOG_TAG, "Progress body is not JSON, sent as is", error)
                return body
            }
            if (!json.has("opened_at")) json.put("opened_at", System.currentTimeMillis())
            return json.toString().toByteArray()
        }
    }
}
