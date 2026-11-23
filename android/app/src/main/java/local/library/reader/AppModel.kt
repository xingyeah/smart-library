package local.library.reader

import android.app.Application
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import java.io.File
import java.io.IOException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val LOG_TAG = "AppModel"
private const val STATUS_FORBIDDEN = 403
private const val OFFLINE_FOLDER = "Offline"

/** Why a connect attempt failed, already worded for the connect page. */
class ConnectFailure(val message: String)

class AppModel(application: Application) : AndroidViewModel(application) {
    private val configStore = ServerConfigStore(application)
    val store = OfflineStore(File(application.filesDir, OFFLINE_FOLDER))
    private val connectionState = MutableStateFlow<Connection?>(null)
    private val replacedConfigState = MutableStateFlow<ServerConfig?>(null)
    private val settingsState = MutableStateFlow(false)

    val connection: StateFlow<Connection?> = connectionState

    /**
     * The server being replaced while the connect page shows; its saved config stays until a new one connects,
     * so cancelling (or quitting the app) keeps it.
     */
    val replacedConfig: StateFlow<ServerConfig?> = replacedConfigState
    val isShowingSettings: StateFlow<Boolean> = settingsState

    private val connectivityManager =
        application.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
    // Wi-Fi to cellular changes which address works, so the server is probed again on every network change
    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) = refreshConnection()
        override fun onLost(network: Network) = refreshConnection()
    }

    init {
        configStore.load()?.let(::connect)
        connectivityManager.registerDefaultNetworkCallback(networkCallback)
    }

    override fun onCleared() {
        connectivityManager.unregisterNetworkCallback(networkCallback)
        connectionState.value?.close()
    }

    fun connect(config: ServerConfig) {
        configStore.save(config)
        replacedConfigState.value = null
        connectionState.value?.close()
        connectionState.value = Connection(config, configStore, store)
    }

    /** Re-picks the server address; called on network changes and when the app comes back to the foreground. */
    fun refreshConnection() {
        val current = connectionState.value ?: return
        viewModelScope.launch(Dispatchers.IO) { current.proxy.checkServer() }
    }

    /** Checks the pasted address against `/api/stats` and connects to it; returns why it failed, or null. */
    suspend fun connectTo(address: String): ConnectFailure? {
        val app = getApplication<Application>()
        val config = try {
            ServerConfig.parse(address)
        } catch (error: InvalidServerAddressException) {
            return ConnectFailure(error.message.orEmpty())
        }
        val client = ServerClient(config)
        val response = try {
            withContext(Dispatchers.IO) { client.get("/api/stats") }
        } catch (error: IOException) {
            Log.w(LOG_TAG, "Connect check failed: address=${config.baseUrl}", error)
            return ConnectFailure(app.getString(R.string.connect_error_unreachable, error.message.orEmpty()))
        }
        if (response.status != 200) {
            return ConnectFailure(
                if (response.status == STATUS_FORBIDDEN) app.getString(R.string.connect_error_token)
                else app.getString(R.string.connect_error_status, response.status),
            )
        }
        // The other address is fetched this once; after that the list changes only through the settings page
        val reported = withContext(Dispatchers.IO) { client.fetchReportedAddresses() }.orEmpty()
        connect(ServerConfig.of(config.baseUrl, config.token, reported).adding(listOf(config.baseUrl)))
        return null
    }

    /** Shows the connect page for another server; downloaded books stay. */
    fun startServerChange() {
        replacedConfigState.value = connectionState.value?.config
        connectionState.value?.close()
        connectionState.value = null
        settingsState.value = false
    }

    fun cancelServerChange() {
        replacedConfigState.value?.let(::connect)
    }

    fun showSettings(isShown: Boolean) {
        settingsState.value = isShown
    }
}
