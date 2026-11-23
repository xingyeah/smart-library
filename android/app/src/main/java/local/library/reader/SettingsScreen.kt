package local.library.reader

import android.content.Context
import android.text.format.Formatter
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private class DownloadedBook(val id: Int, val title: String?, val bytes: Long)

private data class SettingsData(
    val addresses: List<String> = emptyList(),
    val activeAddress: String? = null,
    val readThroughBytes: Long = 0,
    val books: List<DownloadedBook> = emptyList(),
    val isSyncing: Boolean = false,
    val syncMessage: String? = null,
)

private class SettingsActions(
    val sync: () -> Unit,
    val removeAddress: (String) -> Unit,
    val changeServer: () -> Unit,
    val clearCache: () -> Unit,
    val removeBook: (Int) -> Unit,
)

/** Server addresses and downloaded books, opened from the "App 设置" button the page shows inside the app. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(model: AppModel, connection: Connection) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf(SettingsData()) }

    fun reload() {
        scope.launch { data = loadSettings(connection).copy(isSyncing = data.isSyncing, syncMessage = data.syncMessage) }
    }
    LaunchedEffect(connection) { reload() }

    val actions = SettingsActions(
        sync = {
            scope.launch {
                data = data.copy(isSyncing = true)
                val message = syncAddresses(connection.client, context)
                data = data.copy(isSyncing = false, syncMessage = message)
                reload()
            }
        },
        removeAddress = { address -> connection.client.removeAddress(address); reload() },
        changeServer = model::startServerChange,
        clearCache = { scope.launch { withContext(Dispatchers.IO) { connection.store.clearReadThrough() }; reload() } },
        removeBook = { id -> scope.launch { withContext(Dispatchers.IO) { connection.downloader.remove(id) }; reload() } },
    )

    ModalBottomSheet(
        onDismissRequest = { model.showSettings(false) },
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        LazyColumn(contentPadding = PaddingValues(horizontal = 20.dp, vertical = 8.dp)) {
            item { SettingsHeader(onDone = { model.showSettings(false) }) }
            serverSection(data, actions)
            cacheSection(data, actions, context)
            downloadedSection(data, actions, context)
        }
    }
}

@Composable
private fun SettingsHeader(onDone: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(stringResource(R.string.settings_title), style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
        TextButton(onClick = onDone) { Text(stringResource(R.string.settings_done)) }
    }
}

private fun LazyListScope.serverSection(data: SettingsData, actions: SettingsActions) {
    item { SectionTitle(R.string.settings_servers) }
    items(data.addresses) { address ->
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(address, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            if (address == data.activeAddress) {
                Text(stringResource(R.string.settings_in_use), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (data.addresses.size > 1) {
                TextButton(onClick = { actions.removeAddress(address) }) { Text(stringResource(R.string.settings_delete)) }
            }
        }
    }
    item {
        TextButton(onClick = actions.sync, enabled = !data.isSyncing) {
            Text(stringResource(if (data.isSyncing) R.string.settings_syncing else R.string.settings_sync))
        }
    }
    item {
        TextButton(onClick = actions.changeServer) {
            Text(stringResource(R.string.settings_change_server), color = MaterialTheme.colorScheme.error)
        }
    }
    item { Footnote(data.syncMessage ?: stringResource(R.string.settings_servers_footer)) }
}

private fun LazyListScope.cacheSection(data: SettingsData, actions: SettingsActions, context: Context) {
    item {
        val size = Formatter.formatFileSize(context, data.readThroughBytes)
        TextButton(onClick = actions.clearCache, enabled = data.readThroughBytes > 0) {
            Text(stringResource(R.string.settings_clear_cache, size))
        }
    }
    item {
        val limit = Formatter.formatFileSize(context, OfflineStore.READ_THROUGH_LIMIT)
        Footnote(stringResource(R.string.settings_cache_footer, limit))
    }
}

private fun LazyListScope.downloadedSection(data: SettingsData, actions: SettingsActions, context: Context) {
    item {
        val total = Formatter.formatFileSize(context, data.books.sumOf { it.bytes })
        Text(
            stringResource(R.string.settings_downloaded, data.books.size, total),
            style = MaterialTheme.typography.titleSmall,
            modifier = Modifier.padding(top = 16.dp),
        )
    }
    if (data.books.isEmpty()) item { Footnote(stringResource(R.string.settings_downloaded_empty)) }
    items(data.books, key = { it.id }) { book ->
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = book.title ?: stringResource(R.string.settings_book_fallback_title, book.id),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            Text(Formatter.formatFileSize(context, book.bytes), color = MaterialTheme.colorScheme.onSurfaceVariant)
            TextButton(onClick = { actions.removeBook(book.id) }) { Text(stringResource(R.string.settings_delete)) }
        }
    }
}

@Composable
private fun SectionTitle(titleRes: Int) {
    Text(stringResource(titleRes), style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 12.dp))
}

@Composable
private fun Footnote(text: String) {
    Text(text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

private suspend fun loadSettings(connection: Connection): SettingsData = withContext(Dispatchers.IO) {
    val store = connection.store
    SettingsData(
        addresses = connection.client.config.addresses,
        activeAddress = connection.client.activeBaseUrl,
        readThroughBytes = store.readThroughSize(),
        books = store.details().map { detail ->
            val id = detail.optInt("id")
            DownloadedBook(id, detail.optString("title").ifEmpty { null }, store.sizeOfBook(id))
        },
    )
}

private suspend fun syncAddresses(client: ServerClient, context: Context): String = withContext(Dispatchers.IO) {
    if (!client.probe()) return@withContext context.getString(R.string.settings_sync_unreachable)
    val reported = client.fetchReportedAddresses()
        ?: return@withContext context.getString(R.string.settings_sync_no_addresses)
    val added = client.addAddresses(reported)
    if (added > 0) context.getString(R.string.settings_sync_added, added) else context.getString(R.string.settings_sync_unchanged)
}
