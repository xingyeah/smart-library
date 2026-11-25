package local.library.reader

import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.ApplicationInfo
import android.graphics.Color
import android.view.ViewGroup
import android.webkit.WebView
import androidx.activity.compose.BackHandler
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/**
 * The library's own web page, loaded from [PAGE_URL] and answered by [LibraryProxy]. The page talks back over
 * `window.LibraryNative`, see [PageBridge].
 */
@Composable
fun LibraryWebView(
    connection: Connection,
    actionListener: PageActionListener,
    onPageReady: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var webView by remember { mutableStateOf<WebView?>(null) }
    var canGoBack by remember { mutableStateOf(false) }
    val isOffline by connection.proxy.isOffline.collectAsStateWithLifecycle()
    val libraryVersion by connection.libraryVersion.collectAsStateWithLifecycle()

    // The state also goes into the page's HTML, but a change after the page loaded has to be pushed
    LaunchedEffect(webView, isOffline, libraryVersion) {
        val downloadedIds = withContext(Dispatchers.IO) { connection.downloadedBookIds() }
        webView?.evaluateJavascript(offlineStateScript(downloadedIds, isOffline), null)
    }

    // The page keeps its open sheets and the reader in history entries, so back closes the top layer first
    BackHandler(enabled = canGoBack) { webView?.goBack() }

    AndroidView(
        modifier = modifier,
        factory = { context ->
            val events = PageEvents(actionListener, onPageReady) { canGoBack = it }
            createWebView(context, connection, events).also { webView = it }
        },
        onRelease = { view ->
            (view.tag as? PageBridge)?.close()
            view.destroy()
        },
    )
}

/** What the page tells the screen around it. */
private class PageEvents(
    val actionListener: PageActionListener,
    val onPageReady: () -> Unit,
    val onCanGoBackChange: (Boolean) -> Unit,
)

@SuppressLint("SetJavaScriptEnabled")
private fun createWebView(context: Context, connection: Connection, events: PageEvents): WebView {
    // Without explicit params the web view runs in wrap-content mode, where Chromium resolves every vh unit to 0
    val webView = WebView(context).apply {
        layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
    }
    val bridge = PageBridge(
        proxy = connection.proxy,
        listener = events.actionListener,
        runOnPage = { script -> webView.post { webView.evaluateJavascript(script, null) } },
    )
    webView.tag = bridge
    webView.setBackgroundColor(Color.TRANSPARENT)
    webView.settings.apply {
        javaScriptEnabled = true
        domStorageEnabled = true
        mediaPlaybackRequiresUserGesture = false
        allowFileAccess = false
        allowContentAccess = false
    }
    webView.addJavascriptInterface(bridge, BRIDGE_NAME)
    webView.webViewClient = LibraryWebClient(
        proxy = connection.proxy,
        offlineScript = { offlineStateScript(connection.downloadedBookIds(), connection.proxy.isOffline.value) },
        onPageReady = events.onPageReady,
        onCanGoBackChange = events.onCanGoBackChange,
    )
    webView.webChromeClient = PageChromeClient()
    val isDebuggable = context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0
    WebView.setWebContentsDebuggingEnabled(isDebuggable)
    webView.loadUrl(PAGE_URL)
    return webView
}
