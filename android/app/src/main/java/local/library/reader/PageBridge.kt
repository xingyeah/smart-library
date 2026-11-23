package local.library.reader

import android.util.Log
import android.webkit.JavascriptInterface
import java.util.Base64
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import org.json.JSONException
import org.json.JSONObject

private const val LOG_TAG = "PageBridge"

/** What the page asks the app to do: `{action: "download" | "remove", bookId}`, `{action: "settings"}` and `{action: "ready"}`. */
interface PageActionListener {
    fun onPageAction(action: String, bookId: Int?)
}

/**
 * The object the page reaches as `window.LibraryNative`. Its methods run on the web view's bridge thread, so
 * anything slow is handed to [executor] and the answer goes back through [runOnPage].
 */
class PageBridge(
    private val proxy: LibraryProxy,
    private val listener: PageActionListener,
    private val runOnPage: (String) -> Unit,
) {
    private val executor: ExecutorService = Executors.newCachedThreadPool()

    @JavascriptInterface
    fun post(messageJson: String) {
        val message = try {
            JSONObject(messageJson)
        } catch (error: JSONException) {
            Log.w(LOG_TAG, "Ignored page message that is not JSON", error)
            return
        }
        val action = message.optString("action")
        if (action.isEmpty()) {
            Log.w(LOG_TAG, "Ignored page message without an action")
            return
        }
        listener.onPageAction(action, if (message.has("bookId")) message.optInt("bookId") else null)
    }

    @JavascriptInterface
    fun fetch(id: Int, method: String, pathAndQuery: String, headersJson: String, body: String) {
        executor.execute {
            try {
                answer(id, proxy.handle(buildRequest(method, pathAndQuery, headersJson, body)))
            } catch (error: Exception) {
                Log.w(LOG_TAG, "Page request failed: method=$method path=$pathAndQuery", error)
                runOnPage("window.__libraryReject($id, ${jsString(error.message ?: "request failed")});")
            }
        }
    }

    fun close() {
        executor.shutdownNow()
    }

    private fun buildRequest(method: String, pathAndQuery: String, headersJson: String, body: String): ProxyRequest {
        require(pathAndQuery.startsWith("/")) { "Request path must start with /: $pathAndQuery" }
        val headers = JSONObject(headersJson)
        val headerMap = headers.keys().asSequence().associateWith { headers.getString(it) }
            .mapKeys { (name, _) -> canonicalHeaderName(name) }
        val bodyBytes = if (body.isEmpty()) null else body.toByteArray()
        return ProxyRequest(method, pathAndQuery, bodyBytes, headerMap)
    }

    private fun answer(id: Int, response: ProxyResponse) {
        val encodedBody = Base64.getEncoder().encodeToString(response.body)
        val headers = JSONObject(response.headers).toString()
        runOnPage("window.__libraryResolve($id, ${response.status}, $headers, ${jsString(encodedBody)});")
    }

    /** `Headers.forEach` lowercases names, while the proxy looks them up in their usual spelling. */
    private fun canonicalHeaderName(name: String): String =
        name.split('-').joinToString("-") { part -> part.replaceFirstChar { it.uppercase() } }
}
