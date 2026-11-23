package local.library.reader

import android.util.Log
import java.io.IOException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking

private const val LOG_TAG = "LibraryProxy"
private const val RECOVERY_INTERVAL_MS = 10_000L

/**
 * Answers the web view's requests for `https://library.app/...`. Each request is forwarded to the server; when
 * the server cannot be reached [OfflineResponder] answers instead.
 *
 * A failed request is not proof the server is gone: the client first probes every address and retries once on
 * the one that answers. Only when no address answers does the app go offline, and then it probes again every
 * [RECOVERY_INTERVAL_MS] until one does. [handle] blocks, so it runs on the web view's own request threads.
 */
class LibraryProxy(private val client: ServerClient, private val scope: CoroutineScope) {
    private val offlineState = MutableStateFlow(false)
    private val checkedState = MutableStateFlow(false)
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
        runBlocking { startupCheck.join() }
        if (offlineState.value) return OfflineResponder.respond(request)
        return sendWithRetry(request) ?: OfflineResponder.respond(request)
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

    private fun markOnline() {
        synchronized(this) {
            offlineState.value = false
            recoveryJob?.cancel()
            recoveryJob = null
        }
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
}
