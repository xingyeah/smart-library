package local.library.reader

import android.provider.Settings
import androidx.annotation.StringRes
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Same as --accent in frontend/assets/css/main.css. */
val ACCENT_COLOR = Color(0xFF0E7C70)
private const val PULSE_DURATION_MS = 1600
private const val RING_COUNT = 2
private const val RING_MAX_SCALE = 1.9f
private val ICON_DIAMETER = 84.dp
private val OFFLINE_FILL = Color.Gray.copy(alpha = 0.35f)

/** What the launch screen says while the app finds a server address and the page draws. */
enum class LaunchStage(@StringRes val title: Int, @StringRes val detail: Int, val symbol: String) {
    CONNECTING(R.string.launch_connecting_title, R.string.launch_connecting_detail, "📡"),
    LOADING_SHELF(R.string.launch_loading_title, R.string.launch_loading_detail, "📚"),
    OPENING_OFFLINE(R.string.launch_offline_title, R.string.launch_offline_detail, "📴"),
}

/** Covers the web view until the page has drawn, so the first seconds show what the app is doing. */
@Composable
fun LaunchStatusScreen(stage: LaunchStage) {
    Column(
        modifier = Modifier.fillMaxSize().padding(horizontal = 32.dp),
        verticalArrangement = Arrangement.spacedBy(28.dp, Alignment.CenterVertically),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(Modifier.size(ICON_DIAMETER), contentAlignment = Alignment.Center) {
            if (stage != LaunchStage.OPENING_OFFLINE && !isMotionDisabled()) PulseRings()
            Box(
                Modifier.fillMaxSize().clip(CircleShape)
                    .background(if (stage == LaunchStage.OPENING_OFFLINE) OFFLINE_FILL else ACCENT_COLOR),
                contentAlignment = Alignment.Center,
            ) { Text(stage.symbol, fontSize = 30.sp) }
        }
        Column(verticalArrangement = Arrangement.spacedBy(8.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(stringResource(stage.title), style = MaterialTheme.typography.titleMedium, color = Color.White)
            Text(
                text = stringResource(stage.detail),
                style = MaterialTheme.typography.bodyMedium,
                color = Color.White.copy(alpha = 0.6f),
                textAlign = TextAlign.Center,
            )
        }
    }
}

@Composable
private fun PulseRings() {
    val transition = rememberInfiniteTransition(label = "pulse")
    repeat(RING_COUNT) { ring ->
        val progress = transition.animateFloat(
            initialValue = 0f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(
                animation = tween(PULSE_DURATION_MS, easing = LinearEasing),
                repeatMode = RepeatMode.Restart,
                initialStartOffset = androidx.compose.animation.core.StartOffset(ring * PULSE_DURATION_MS / RING_COUNT),
            ),
            label = "ring$ring",
        )
        Canvas(Modifier.fillMaxSize().scale(1f + (RING_MAX_SCALE - 1f) * progress.value)) {
            drawCircle(ACCENT_COLOR.copy(alpha = 0.6f * (1f - progress.value)), style = Stroke(width = 2.dp.toPx()))
        }
    }
}

/** Honors the system "remove animations" setting, as iOS honors Reduce Motion. */
@Composable
private fun isMotionDisabled(): Boolean {
    val resolver = LocalContext.current.contentResolver
    return remember { Settings.Global.getFloat(resolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f }
}
