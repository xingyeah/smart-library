package local.library.reader

import android.util.Log
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val LOG_TAG = "BrowserScreen"
/** The connection notice shows this long; while offline the page itself keeps a small marker. */
private const val NOTICE_DURATION_MS = 3_000L
private const val SURFACE_ALPHA = 0.92f
private val OVERLAY_BOTTOM_PADDING = 72.dp

private enum class ConnectionNotice { OFFLINE, RECONNECTED }

/** The library page over the launch screen, with the download banner, connection notice and settings sheet. */
@Composable
fun BrowserScreen(connection: Connection, model: AppModel) {
    var isPageReady by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val hasChecked by connection.proxy.hasCheckedServer.collectAsStateWithLifecycle()
    val isOffline by connection.proxy.isOffline.collectAsStateWithLifecycle()
    val isShowingSettings by model.isShowingSettings.collectAsStateWithLifecycle()
    val listener = remember(connection) {
        object : PageActionListener {
            override fun onPageAction(action: String, bookId: Int?) {
                scope.launch { handleAction(action, bookId, connection, model) { isPageReady = true } }
            }
        }
    }

    Box(Modifier.fillMaxSize()) {
        LibraryWebView(connection, listener, onPageReady = { isPageReady = true }, modifier = Modifier.fillMaxSize())
        BrowserOverlays(connection, isOffline, Modifier.align(Alignment.BottomCenter))
        AnimatedVisibility(visible = !isPageReady, exit = fadeOut()) {
            Box(Modifier.fillMaxSize().background(PAGE_BACKGROUND)) {
                LaunchStatusScreen(launchStage(hasChecked, isOffline))
            }
        }
    }
    if (isShowingSettings) SettingsScreen(model, connection)
}

@Composable
private fun BrowserOverlays(connection: Connection, isOffline: Boolean, modifier: Modifier) {
    val downloadStatus by connection.downloader.status.collectAsStateWithLifecycle()
    var notice by remember { mutableStateOf<ConnectionNotice?>(null) }
    var lastIsOffline by remember { mutableStateOf(isOffline) }

    // Only a change shows a notice, not the state the screen starts in
    LaunchedEffect(isOffline) {
        if (isOffline == lastIsOffline) return@LaunchedEffect
        lastIsOffline = isOffline
        notice = if (isOffline) ConnectionNotice.OFFLINE else ConnectionNotice.RECONNECTED
        delay(NOTICE_DURATION_MS)
        notice = null
    }

    Column(
        modifier = modifier.padding(bottom = OVERLAY_BOTTOM_PADDING),
        verticalArrangement = Arrangement.spacedBy(8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        downloadStatus?.let { DownloadBanner(it, onDismiss = connection.downloader::dismissError) }
        notice?.let { ConnectionNoticeBadge(it) }
    }
}

@Composable
private fun ConnectionNoticeBadge(notice: ConnectionNotice) {
    Surface(
        shape = MaterialTheme.shapes.extraLarge,
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = SURFACE_ALPHA),
    ) {
        Text(
            text = stringResource(if (notice == ConnectionNotice.OFFLINE) R.string.notice_offline else R.string.notice_reconnected),
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
        )
    }
}

private fun launchStage(hasCheckedServer: Boolean, isOffline: Boolean): LaunchStage = when {
    !hasCheckedServer -> LaunchStage.CONNECTING
    isOffline -> LaunchStage.OPENING_OFFLINE
    else -> LaunchStage.LOADING_SHELF
}

private suspend fun handleAction(
    action: String,
    bookId: Int?,
    connection: Connection,
    model: AppModel,
    onReady: () -> Unit,
) {
    when {
        action == "settings" -> model.showSettings(true)
        action == "ready" -> onReady()
        action == "download" && bookId != null -> connection.downloader.download(bookId)
        action == "remove" && bookId != null -> withContext(Dispatchers.IO) { connection.downloader.remove(bookId) }
        else -> Log.w(LOG_TAG, "Ignored page message: action=$action bookId=$bookId")
    }
}
