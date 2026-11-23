package local.library.reader

import android.content.Context
import android.util.Log
import org.json.JSONArray

private const val LOG_TAG = "ServerConfigStore"
private const val PREFS_NAME = "server"
private const val BASE_URL_KEY = "serverBaseUrl"
private const val ADDRESSES_KEY = "serverAddresses"
private const val TOKEN_KEY = "lanToken"

/** Keeps the base URL and address list in SharedPreferences and the token encrypted by [TokenVault]. */
class ServerConfigStore(context: Context, private val vault: TokenVault = TokenVault()) {
    private val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun load(): ServerConfig? {
        val baseUrl = prefs.getString(BASE_URL_KEY, null) ?: return null
        return ServerConfig.of(baseUrl, readToken(), readAddresses())
    }

    fun save(config: ServerConfig) {
        prefs.edit()
            .putString(BASE_URL_KEY, config.baseUrl)
            .putString(ADDRESSES_KEY, JSONArray(config.addresses).toString())
            .putString(TOKEN_KEY, vault.encrypt(config.token))
            .apply()
    }

    /** The token and pasted address do not change when the server reports new addresses, so only these are saved. */
    fun saveAddresses(addresses: List<String>) {
        prefs.edit().putString(ADDRESSES_KEY, JSONArray(addresses).toString()).apply()
    }

    private fun readAddresses(): List<String> {
        val json = prefs.getString(ADDRESSES_KEY, null) ?: return emptyList()
        val array = JSONArray(json)
        return (0 until array.length()).map { array.getString(it) }
    }

    private fun readToken(): String {
        val stored = prefs.getString(TOKEN_KEY, null) ?: return ""
        return try {
            vault.decrypt(stored)
        } catch (error: java.security.GeneralSecurityException) {
            // The key is gone, so the user has to paste the address again; an empty token is rejected by the gate
            Log.w(LOG_TAG, "Token could not be decrypted", error)
            ""
        }
    }
}
