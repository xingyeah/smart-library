package local.library.reader

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch

private val SCREEN_PADDING = 24.dp
private val SPINNER_SIZE = 18.dp

/** First launch: paste the LAN address the server prints; it is checked against `/api/stats` before saving. */
@Composable
fun ConnectScreen(model: AppModel) {
    var address by remember { mutableStateOf("") }
    var isChecking by remember { mutableStateOf(false) }
    var errorText by remember { mutableStateOf<String?>(null) }
    val replacedConfig by model.replacedConfig.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()

    Column(
        modifier = Modifier.fillMaxSize().padding(SCREEN_PADDING),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = stringResource(R.string.connect_title),
                style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.weight(1f),
            )
            if (replacedConfig != null) {
                TextButton(onClick = model::cancelServerChange) { Text(stringResource(R.string.connect_cancel)) }
            }
        }
        Text(
            text = stringResource(R.string.connect_hint),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        if (replacedConfig?.token.orEmpty().isNotEmpty()) {
            Text(
                text = stringResource(R.string.connect_saved_token_hint),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        OutlinedTextField(
            value = address,
            onValueChange = { address = it },
            placeholder = { Text(stringResource(R.string.connect_placeholder)) },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        errorText?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
        Button(
            onClick = {
                scope.launch {
                    errorText = null
                    isChecking = true
                    errorText = model.connectTo(address)?.message
                    isChecking = false
                }
            },
            enabled = address.isNotEmpty() && !isChecking,
            modifier = Modifier.fillMaxWidth(),
        ) {
            if (isChecking) CircularProgressIndicator(Modifier.size(SPINNER_SIZE).padding(end = 0.dp), strokeWidth = 2.dp)
            Text(
                text = stringResource(if (isChecking) R.string.connect_button_busy else R.string.connect_button),
                modifier = Modifier.padding(start = if (isChecking) 8.dp else 0.dp),
            )
        }
    }
}
