package local.library.reader

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel

/** The pieces that exist while a server is configured; rebuilt when the user changes server. */
class Connection(config: ServerConfig, configStore: ServerConfigStore) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    val client = ServerClient(config).also { client ->
        client.onAddressesChange = { configStore.saveAddresses(it.addresses) }
    }
    val proxy = LibraryProxy(client, scope)

    val config: ServerConfig get() = client.config

    /** Ids of the books with an offline copy; the page shows its download buttons from this. */
    fun downloadedBookIds(): Collection<Int> = emptyList()

    fun close() {
        scope.cancel()
        client.close()
    }
}
