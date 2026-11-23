package local.library.reader

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle

private const val PAGE_BACKGROUND_ARGB = 0xFF0F1117.toInt()

/** Same as the page's `--bg` colour in frontend/index.html, so the system bars and safe areas match the page. */
val PAGE_BACKGROUND = Color(PAGE_BACKGROUND_ARGB)

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
    val colors = darkColorScheme(
        primary = ACCENT_COLOR,
        onPrimary = Color.White,
        background = PAGE_BACKGROUND,
        surface = PAGE_BACKGROUND,
    )
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
        if (current == null) ConnectScreen(model) else key(current) { BrowserScreen(current, model) }
    }
}
