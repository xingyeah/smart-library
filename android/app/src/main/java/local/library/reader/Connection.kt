package local.library.reader

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

/** The pieces that exist while a server is configured; rebuilt when the user changes server. */
class Connection(config: ServerConfig, configStore: ServerConfigStore, val store: OfflineStore) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val libraryVersionState = MutableStateFlow(0)

    val client = ServerClient(config).also { client ->
        client.onAddressesChange = { configStore.saveAddresses(it.addresses) }
    }
    val proxy = LibraryProxy(client, store, scope)
    val downloader = BookDownloader(client, store).also { downloader ->
        downloader.onLibraryChange = { libraryVersionState.update { it + 1 } }
    }

    val config: ServerConfig get() = client.config

    /** Changes whenever a book is added or removed, so the page can refresh its download buttons. */
    val libraryVersion: StateFlow<Int> = libraryVersionState

    /** Ids of the books with an offline copy; the page shows its download buttons from this. */
    fun downloadedBookIds(): Collection<Int> = store.downloadedBookIds()

    fun close() {
        scope.cancel()
        client.close()
    }
}
