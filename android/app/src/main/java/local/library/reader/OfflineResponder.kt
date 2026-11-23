package local.library.reader

import org.json.JSONArray
import org.json.JSONObject

private const val SHELF_PATH = "/api/books"
private const val INDEX_PATH = "/index.html"

/** Answers the web page's requests while the server cannot be reached, from what [OfflineStore] holds. */
class OfflineResponder(private val store: OfflineStore) {

    fun respond(request: ProxyRequest): ProxyResponse {
        if (request.method == "GET" && request.path == SHELF_PATH) return shelf(request)
        val bookId = request.bookId
        if (request.method == "GET" && bookId != null && request.path.endsWith("/read") && request.query("chapter") == null) {
            return resumeChapter(bookId)
        }
        val cached = store.get(OfflineStore.key(shellRequest(request)), bookId)
        return cached ?: ProxyResponse.offlineUnavailable()
    }

    /**
     * The shelf offline: downloaded books, then books read online (`offline_partial`), filtered by the search box
     * text against title and author, and by the author link the detail page opened (`author`).
     */
    private fun shelf(request: ProxyRequest): ProxyResponse {
        val needle = (request.query("q") ?: "").trim().lowercase()
        val author = request.query("author").orEmpty()
        val items = (store.details() + store.partialDetails()).filter { book ->
            if (author.isNotEmpty() && !names(book, author)) return@filter false
            needle.isEmpty() || "${book.optString("title")} ${book.optString("author")}".lowercase().contains(needle)
        }
        return ProxyResponse.json(
            JSONObject()
                .put("items", JSONArray(items))
                .put("total", items.size)
                .put("page", 1)
                .put("page_size", maxOf(items.size, 1))
                .put("elapsed_ms", 0),
        )
    }

    /**
     * The reader opens a book with `/read` (no chapter) and the server picks the saved chapter; offline the
     * chapter comes from the last progress this phone saved.
     */
    private fun resumeChapter(bookId: Int): ProxyResponse {
        val progress = store.lastProgress(bookId)
        val chapter = progress?.optInt("chapter_idx", 0) ?: 0
        // A PDF's /read answer is the same for every chapter, so only chapter 0 is downloaded
        val cached = listOf(chapter, 0).firstNotNullOfOrNull { index ->
            val request = ProxyRequest("GET", "/api/books/$bookId/read?chapter=$index")
            store.get(OfflineStore.key(request), bookId)
        } ?: return ProxyResponse.offlineUnavailable()
        val json = try {
            JSONObject(String(cached.body))
        } catch (error: org.json.JSONException) {
            return ProxyResponse.offlineUnavailable()
        }
        json.put("saved_pos", progress ?: JSONObject.NULL)
        return ProxyResponse.json(json)
    }

    companion object {
        /** `/` and `/index.html` are one page; both are stored under `/`. */
        fun shellRequest(request: ProxyRequest): ProxyRequest {
            if (request.path != INDEX_PATH) return request
            return ProxyRequest(request.method, "/", request.body)
        }

        /**
         * Whether [book] names [author]. Details saved since author links carry the server's split `authors`;
         * older ones fall back to the raw author line. Case, spacing and punctuation are ignored; scripts are not
         * folded.
         */
        fun names(book: JSONObject, author: String): Boolean {
            val key = authorKey(author)
            if (key.isEmpty()) return false
            val people = book.optJSONArray("authors") ?: return authorKey(book.optString("author")).contains(key)
            return (0 until people.length()).any { authorKey(people.optJSONObject(it)?.optString("name").orEmpty()) == key }
        }

        private fun authorKey(name: String): String = name.lowercase().filter { it.isLetterOrDigit() }
    }
}
