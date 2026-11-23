package local.library.reader

import android.util.Log
import java.io.IOException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.withContext
import org.json.JSONException
import org.json.JSONObject

private const val LOG_TAG = "BookDownloader"
private const val STATUS_OK = 200
private const val STATUS_CONFLICT = 409
/** COVER_THUMB_WIDTH and COVER_DETAIL_WIDTH in frontend/index.html. */
private val COVER_WIDTHS = listOf(360, 720)
private val ASSET_PATTERN = Regex("""/api/books/\d+/(?:epub_asset|mobi_image)\?[^"'\s<>)]+""")

/** Raised when the server refuses or garbles one of a book's files; carries the path for the banner. */
class DownloadException(message: String) : IOException(message)

/** Progress shown while a book downloads; [error] is set when it stopped. */
data class DownloadStatus(val title: String, val done: Int, val total: Int, val error: String? = null)

/**
 * Downloads everything the reader requests for one book so it opens offline: detail JSON and covers, every
 * chapter with its images, or every PDF page image and its text blocks.
 */
class BookDownloader(private val client: ServerClient, private val store: OfflineStore) {
    private val statusState = MutableStateFlow<DownloadStatus?>(null)
    val status: StateFlow<DownloadStatus?> = statusState

    /** Called after a book is added or removed, so the page can update its offline buttons. */
    var onLibraryChange: (() -> Unit)? = null

    suspend fun download(bookId: Int) = withContext(Dispatchers.IO) {
        if (statusState.value?.error == null && statusState.value != null) return@withContext
        statusState.value = DownloadStatus("准备下载…", 0, 1)
        try {
            val detail = fetchJson("/api/books/$bookId", bookId)
            statusState.update { it?.copy(title = detail.json.optString("title", "第 $bookId 本书")) }
            downloadCovers(detail.json, bookId)
            downloadContent(bookId)
            store.saveDetail(detail.raw, bookId)
            store.markComplete(bookId)
            statusState.value = null
            onLibraryChange?.invoke()
        } catch (error: CancellationException) {
            store.removeBook(bookId)
            throw error
        } catch (error: IOException) {
            fail(bookId, error)
        } catch (error: JSONException) {
            fail(bookId, error)
        }
    }

    fun remove(bookId: Int) {
        store.removeBook(bookId)
        onLibraryChange?.invoke()
    }

    fun dismissError() {
        statusState.value = null
    }

    private fun fail(bookId: Int, error: Exception) {
        Log.w(LOG_TAG, "Offline download failed: bookId=$bookId", error)
        store.removeBook(bookId)
        statusState.update { it?.copy(error = "下载失败：${error.message}") }
    }

    // MARK: steps

    private fun downloadCovers(detail: JSONObject, bookId: Int) {
        val cover = detail.optString("cover_path")
        if (cover.isEmpty()) return
        for (width in COVER_WIDTHS) fetch("$cover?w=$width", bookId)
    }

    private fun downloadContent(bookId: Int) {
        val first = fetchJson("/api/books/$bookId/read?chapter=0", bookId).json
        if (!first.optBoolean("ok")) throw DownloadException("服务器返回 $STATUS_CONFLICT（${first.optString("error", "read")}）")
        if (first.optString("mode") == "pdf") downloadPdf(bookId) else downloadChapters(first, bookId)
    }

    private fun downloadPdf(bookId: Int) {
        // 409 means the server cannot render this PDF; the reader then falls back to pdf.js on the whole file
        val layoutResponse = client.get("/api/books/$bookId/pdf_layout")
        val pageCount = if (layoutResponse.status == STATUS_OK) pageCountOf(layoutResponse.body) else null
        if (pageCount == null) {
            fetch("/api/books/$bookId/stream", bookId)
            return
        }
        val layoutRequest = ProxyRequest("GET", "/api/books/$bookId/pdf_layout")
        store.put(layoutResponse, OfflineStore.key(layoutRequest), bookId)
        statusState.update { it?.copy(total = pageCount * 2 + 2) }
        for (page in 1..maxOf(pageCount, 1)) {
            fetch("/api/books/$bookId/pdf_page/$page", bookId)
            fetch("/api/books/$bookId/pdf_text/$page", bookId)
        }
    }

    private fun pageCountOf(body: ByteArray): Int? = try {
        JSONObject(String(body)).takeIf { it.has("page_count") }?.getInt("page_count")
    } catch (error: JSONException) {
        Log.w(LOG_TAG, "PDF layout is not JSON", error)
        null
    }

    private fun downloadChapters(first: JSONObject, bookId: Int) {
        val total = maxOf(first.optInt("total_chapters", 1), 1)
        statusState.update { it?.copy(total = total + 2) }
        val assets = assetPaths(first.optString("html")).toMutableSet()
        for (chapter in 1 until total) {
            val json = fetchJson("/api/books/$bookId/read?chapter=$chapter", bookId).json
            assets += assetPaths(json.optString("html"))
        }
        statusState.update { it?.copy(total = it.total + assets.size) }
        for (path in assets.sorted()) fetch(path, bookId)
    }

    // MARK: fetching

    private fun fetch(path: String, bookId: Int): ProxyResponse {
        val request = ProxyRequest("GET", path)
        val response = client.send(request)
        if (response.status != STATUS_OK) throw DownloadException("服务器返回 ${response.status}（$path）")
        store.put(response, OfflineStore.key(request), bookId)
        statusState.update { it?.copy(done = it.done + 1) }
        return response
    }

    private class FetchedJson(val json: JSONObject, val raw: ByteArray)

    private fun fetchJson(path: String, bookId: Int): FetchedJson {
        val response = fetch(path, bookId)
        val json = try {
            JSONObject(String(response.body))
        } catch (error: JSONException) {
            throw DownloadException("响应格式不对（$path）").apply { initCause(error) }
        }
        return FetchedJson(json, response.body)
    }

    companion object {
        /** Image and stylesheet URLs the server wrote into a chapter's HTML (`&amp;` decoded). */
        fun assetPaths(html: String): Set<String> =
            ASSET_PATTERN.findAll(html).map { it.value.replace("&amp;", "&") }.toSet()
    }
}
