package local.library.reader

import android.content.ActivityNotFoundException
import android.content.Intent
import android.util.Log
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import java.io.ByteArrayInputStream

private const val LOG_TAG = "LibraryWebClient"
private const val STATUS_METHOD_NOT_ALLOWED = 405
private const val STATUS_BAD_GATEWAY = 502
private const val FIRST_REDIRECT_STATUS = 300
private const val FIRST_NON_REDIRECT_STATUS = 400
private const val STATUS_PARTIAL_CONTENT = 206
private const val DEFAULT_MIME_TYPE = "application/octet-stream"
private const val NO_CACHE = "no-cache"
private val SHELL_PATHS = setOf("/", "/index.html")

/** The fake host the page is loaded from; it never reaches the network because every request is answered here. */
const val PAGE_HOST = "library.app"
const val PAGE_URL = "https://$PAGE_HOST/"

/** A page that loaded but never says `ready` (an error page from the server) still gets shown after this. */
const val READY_FALLBACK_MS = 3_000L

/**
 * Serves the page to the web view through [LibraryProxy] (the role `LibrarySchemeHandler` has on iOS), and keeps
 * navigation inside the library: any other address opens in the phone's own browser.
 */
class LibraryWebClient(
    private val proxy: LibraryProxy,
    private val offlineScript: () -> String,
    private val onPageReady: () -> Unit,
    private val onCanGoBackChange: (Boolean) -> Unit,
) : WebViewClient() {

    override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
        if (request.url.host != PAGE_HOST) return null
        if (request.method != "GET") {
            Log.w(LOG_TAG, "Only GET reaches the interceptor, the page's fetch sends the rest: method=${request.method}")
            return errorResponse(STATUS_METHOD_NOT_ALLOWED)
        }
        val path = request.url.encodedPath.orEmpty().ifEmpty { "/" }
        val pathAndQuery = request.url.encodedQuery?.let { "$path?$it" } ?: path
        return try {
            toWebResponse(path, proxy.handle(ProxyRequest("GET", pathAndQuery, null, request.requestHeaders)))
        } catch (error: Exception) {
            Log.w(LOG_TAG, "Request could not be answered: path=$pathAndQuery", error)
            errorResponse(STATUS_BAD_GATEWAY)
        }
    }

    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
        if (request.url.host == PAGE_HOST) return false
        try {
            view.context.startActivity(Intent(Intent.ACTION_VIEW, request.url).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (error: ActivityNotFoundException) {
            Log.w(LOG_TAG, "No app can open the link: url=${request.url}", error)
        }
        return true
    }

    override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) {
        onCanGoBackChange(view.canGoBack())
    }

    override fun onPageFinished(view: WebView, url: String?) {
        view.postDelayed(onPageReady, READY_FALLBACK_MS)
    }

    override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
        if (!request.isForMainFrame) return
        Log.w(LOG_TAG, "Page load failed: code=${error.errorCode} description=${error.description}")
        onPageReady()
    }

    private fun toWebResponse(path: String, response: ProxyResponse): WebResourceResponse {
        val status = usableStatus(path, response.status)
        if (status >= FIRST_NON_REDIRECT_STATUS) Log.w(LOG_TAG, "Answering with an error: path=$path status=$status")
        val body = if (path in SHELL_PATHS && status == 200) injectShell(response.body) else response.body
        val headers = response.headers.filterKeys { it != "Content-Type" }.toMutableMap()
        headers["Content-Length"] = body.size.toString()
        // Keep the shell and app assets fresh on update; honor the server's cache headers elsewhere
        val isAppBundle = path in SHELL_PATHS || path.startsWith("/assets/")
        if (isAppBundle || headers["Cache-Control"] == null) headers["Cache-Control"] = NO_CACHE
        val (mimeType, charset) = splitContentType(response.contentType)
        return WebResourceResponse(mimeType, charset, status, reasonFor(status), headers, ByteArrayInputStream(body))
    }

    private fun injectShell(body: ByteArray): ByteArray {
        val html = String(body, Charsets.UTF_8)
        return injectPageScripts(html, offlineScript()).toByteArray(Charsets.UTF_8)
    }

    /** `WebResourceResponse` rejects the 3xx range; the client follows redirects itself, so one here is an error. */
    private fun usableStatus(path: String, status: Int): Int {
        if (status !in FIRST_REDIRECT_STATUS until FIRST_NON_REDIRECT_STATUS) return status
        Log.w(LOG_TAG, "Unexpected redirect status: path=$path status=$status")
        return STATUS_BAD_GATEWAY
    }

    private fun errorResponse(status: Int): WebResourceResponse =
        WebResourceResponse("text/plain", "utf-8", status, reasonFor(status), emptyMap(), ByteArrayInputStream(ByteArray(0)))

    private fun reasonFor(status: Int): String = when {
        status == STATUS_PARTIAL_CONTENT -> "Partial Content"
        status < FIRST_NON_REDIRECT_STATUS -> "OK"
        else -> "Error"
    }

    private fun splitContentType(contentType: String): Pair<String, String?> {
        val parts = contentType.split(';').map { it.trim() }
        val charset = parts.drop(1).firstOrNull { it.startsWith("charset=", ignoreCase = true) }
        return parts[0].ifEmpty { DEFAULT_MIME_TYPE } to charset?.substringAfter('=')?.trim('"')
    }
}
