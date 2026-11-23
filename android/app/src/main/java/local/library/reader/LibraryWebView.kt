package local.library.reader

import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.ApplicationInfo
import android.graphics.Color
import android.webkit.WebView
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
    val isOffline by connection.proxy.isOffline.collectAsStateWithLifecycle()
    val libraryVersion by connection.libraryVersion.collectAsStateWithLifecycle()

    // The state also goes into the page's HTML, but a change after the page loaded has to be pushed
    LaunchedEffect(webView, isOffline, libraryVersion) {
        val downloadedIds = withContext(Dispatchers.IO) { connection.downloadedBookIds() }
        webView?.evaluateJavascript(offlineStateScript(downloadedIds, isOffline), null)
    }

    AndroidView(
        modifier = modifier,
        factory = { context ->
            createWebView(context, connection, actionListener, onPageReady).also { webView = it }
        },
        onRelease = { view ->
            (view.tag as? PageBridge)?.close()
            view.destroy()
        },
    )
}

@SuppressLint("SetJavaScriptEnabled")
private fun createWebView(
    context: Context,
    connection: Connection,
    actionListener: PageActionListener,
    onPageReady: () -> Unit,
): WebView {
    val webView = WebView(context)
    val bridge = PageBridge(
        proxy = connection.proxy,
        listener = actionListener,
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
        onPageReady = onPageReady,
    )
    webView.webChromeClient = PageChromeClient()
    val isDebuggable = context.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0
    WebView.setWebContentsDebuggingEnabled(isDebuggable)
    webView.loadUrl(PAGE_URL)
    return webView
}
