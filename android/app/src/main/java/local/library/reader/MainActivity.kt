package local.library.reader

import android.graphics.Color as AndroidColor
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val LOG_TAG = "MainActivity"
private val BANNER_BOTTOM_PADDING = 72.dp
private const val PAGE_BACKGROUND_ARGB = 0xFF0F1117.toInt()

/** Same as --bg in frontend/index.html, so the system bars and safe areas match the page. */
private val PAGE_BACKGROUND = Color(PAGE_BACKGROUND_ARGB)

class MainActivity : ComponentActivity() {
    private val model: AppModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val barStyle = SystemBarStyle.dark(PAGE_BACKGROUND_ARGB)
        enableEdgeToEdge(statusBarStyle = barStyle, navigationBarStyle = barStyle)
        setContent { LibraryTheme { RootScreen(model) } }
    }
}

@Composable
private fun LibraryTheme(content: @Composable () -> Unit) {
    val colors = darkColorScheme(primary = ACCENT_COLOR, onPrimary = Color.White, background = PAGE_BACKGROUND, surface = PAGE_BACKGROUND)
    MaterialTheme(colorScheme = colors) {
        // Without a Surface, Text falls back to black on the dark page
        Surface(color = PAGE_BACKGROUND, contentColor = colors.onBackground, content = content)
    }
}

@Composable
private fun RootScreen(model: AppModel) {
    val connection by model.connection.collectAsStateWithLifecycle()
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { model.refreshConnection() }

    Box(Modifier.fillMaxSize().background(PAGE_BACKGROUND).systemBarsPadding().imePadding()) {
        val current = connection
        if (current == null) {
            ConnectScreen(model)
        } else {
            androidx.compose.runtime.key(current) { BrowserScreen(current, model) }
        }
    }
}

@Composable
private fun BrowserScreen(connection: Connection, model: AppModel) {
    var isPageReady by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val hasChecked by connection.proxy.hasCheckedServer.collectAsStateWithLifecycle()
    val isOffline by connection.proxy.isOffline.collectAsStateWithLifecycle()
    val downloadStatus by connection.downloader.status.collectAsStateWithLifecycle()
    val listener = remember(connection) {
        object : PageActionListener {
            override fun onPageAction(action: String, bookId: Int?) {
                scope.launch { handleAction(action, bookId, connection, model) { isPageReady = true } }
            }
        }
    }

    Box(Modifier.fillMaxSize()) {
        LibraryWebView(connection, listener, onPageReady = { isPageReady = true }, modifier = Modifier.fillMaxSize())
        downloadStatus?.let { status ->
            DownloadBanner(
                status = status,
                onDismiss = connection.downloader::dismissError,
                modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = BANNER_BOTTOM_PADDING),
            )
        }
        AnimatedVisibility(visible = !isPageReady, exit = fadeOut()) {
            Box(Modifier.fillMaxSize().background(PAGE_BACKGROUND)) {
                LaunchStatusScreen(launchStage(hasChecked, isOffline))
            }
        }
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
