package local.library.reader

import java.net.URLDecoder
import org.json.JSONObject

private const val QUERY_CHARSET = "UTF-8"
private const val JSON_CONTENT_TYPE = "application/json; charset=utf-8"
private const val DEFAULT_CONTENT_TYPE = "application/octet-stream"
private const val OFFLINE_UNAVAILABLE_TEXT = "离线不可用：此内容未下载"
private const val STATUS_SERVICE_UNAVAILABLE = 503
private const val BOOK_PATH_MIN_PARTS = 3

/**
 * A request as the web page made it, before it is sent to the server or answered from the offline store.
 * The query stays exactly as the page encoded it, since re-encoding would leave `&` and `+` inside values unescaped.
 */
class ProxyRequest(
    val method: String,
    pathAndQuery: String,
    val body: ByteArray? = null,
    val headers: Map<String, String> = emptyMap(),
) {
    val path: String
    val rawQuery: String?

    init {
        val queryStart = pathAndQuery.indexOf('?')
        val rawPath = if (queryStart < 0) pathAndQuery else pathAndQuery.substring(0, queryStart)
        path = rawPath.ifEmpty { "/" }
        rawQuery = if (queryStart < 0) null else pathAndQuery.substring(queryStart + 1)
    }

    val pathAndQuery: String get() = rawQuery?.let { "$path?$it" } ?: path

    /** Decoded query items in the order the page wrote them. */
    val queryItems: List<Pair<String, String>> by lazy {
        rawQuery.orEmpty().split('&').filter { it.isNotEmpty() }.map { item ->
            val separator = item.indexOf('=')
            val name = if (separator < 0) item else item.substring(0, separator)
            val value = if (separator < 0) "" else item.substring(separator + 1)
            decode(name) to decode(value)
        }
    }

    fun query(name: String): String? = queryItems.firstOrNull { it.first == name }?.second

    /** `/api/books/<id>...` gives the id; any other path gives null. */
    val bookId: Int?
        get() {
            val parts = path.split('/').filter { it.isNotEmpty() }
            if (parts.size < BOOK_PATH_MIN_PARTS || parts[0] != "api" || parts[1] != "books") return null
            return parts[2].toIntOrNull()
        }

    private fun decode(text: String): String = URLDecoder.decode(text, QUERY_CHARSET)
}

class ProxyResponse(val status: Int, val headers: Map<String, String>, val body: ByteArray) {
    val contentType: String get() = headers["Content-Type"] ?: DEFAULT_CONTENT_TYPE

    companion object {
        fun json(payload: JSONObject, status: Int = 200): ProxyResponse =
            ProxyResponse(status, mapOf("Content-Type" to JSON_CONTENT_TYPE), payload.toString().toByteArray())

        fun offlineUnavailable(): ProxyResponse = json(
            JSONObject().put("ok", false).put("error", OFFLINE_UNAVAILABLE_TEXT),
            STATUS_SERVICE_UNAVAILABLE,
        )
    }
}
