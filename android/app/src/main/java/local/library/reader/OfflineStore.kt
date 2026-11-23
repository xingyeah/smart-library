package local.library.reader

import android.util.Log
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.Callable
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import org.json.JSONException
import org.json.JSONObject

private const val LOG_TAG = "OfflineStore"
private const val READ_THROUGH_LIMIT_BYTES = 500L * 1024 * 1024
/** Summing the cache folder costs a directory walk, so the limit is checked every this many writes. */
private const val EVICTION_CHECK_INTERVAL = 50
private val IGNORED_QUERY_NAMES = setOf("v", "retry")
private const val COMPLETE_MARKER = "complete"
private const val DETAIL_FILE = "book.json"
private const val PROGRESS_FILE = "progress.json"

/**
 * Server responses kept on disk for offline use.
 *
 * Layout under `root` (the app's files folder, `Offline`):
 *   shared/                 page shell (`/`, and everything under `/vendor/` and `/assets/`) and translations
 *   books/<id>/             one downloaded book: responses, `book.json`, `progress.json`, `complete`
 *   cache/<id>/             what was read online of a book that is not downloaded: responses, `book.json`;
 *                           whole books are evicted, least recently used first, above [READ_THROUGH_LIMIT_BYTES]
 *   pending-progress/<id>   latest progress POST body not yet accepted by the server
 *
 * All file access runs on one background thread, so readers never see a half-written book and the web view's
 * request threads never write concurrently.
 */
class OfflineStore(private val root: File) {
    private val ioExecutor: ExecutorService = Executors.newSingleThreadExecutor { task ->
        Thread(task, "library-offline-store").apply { priority = Thread.MIN_PRIORITY }
    }
    private var writesSinceEvictionCheck = 0

    private val sharedDir get() = File(root, "shared")
    private val booksRoot get() = File(root, "books")
    private val cacheRoot get() = File(root, "cache")
    private val pendingDir get() = File(root, "pending-progress")
    private fun bookDir(bookId: Int) = File(booksRoot, bookId.toString())
    private fun cacheDir(bookId: Int) = File(cacheRoot, bookId.toString())

    private fun <T> onIoThread(block: () -> T): T = ioExecutor.submit(Callable { block() }).get()

    // MARK: responses

    fun put(response: ProxyResponse, key: String, bookId: Int?) {
        onIoThread { writeResponse(response, key, bookId?.let(::bookDir) ?: sharedDir) }
    }

    fun putAsync(response: ProxyResponse, key: String, bookId: Int?) {
        ioExecutor.execute { writeResponse(response, key, bookId?.let(::bookDir) ?: sharedDir) }
    }

    /**
     * Looks in the shared folder, then in that book's download and read-through cache, or in every downloaded
     * book when the request names no book (covers).
     */
    fun get(key: String, bookId: Int?): ProxyResponse? = onIoThread {
        val name = hexDigest(key.toByteArray())
        val bookDirs = bookId?.let { listOf(bookDir(it), cacheDir(it)) } ?: downloadedIds().map(::bookDir)
        (listOf(sharedDir) + bookDirs).firstNotNullOfOrNull { dir -> readResponse(dir, name) }
    }

    private fun readResponse(dir: File, name: String): ProxyResponse? {
        val body = File(dir, "$name.bin").takeIf { it.isFile }?.readBytes() ?: return null
        if (dir.parentFile == cacheRoot) touch(dir)
        return ProxyResponse(200, readHeaders(File(dir, "$name.json")), body)
    }

    private fun readHeaders(file: File): Map<String, String> {
        if (!file.isFile) return emptyMap()
        return try {
            val meta = JSONObject(file.readText())
            meta.keys().asSequence().associateWith { meta.getString(it) }
        } catch (error: JSONException) {
            Log.w(LOG_TAG, "Stored headers are damaged: path=${file.path}", error)
            emptyMap()
        }
    }

    private fun writeResponse(response: ProxyResponse, key: String, dir: File) {
        val name = hexDigest(key.toByteArray())
        write(File(dir, "$name.bin"), response.body)
        write(File(dir, "$name.json"), JSONObject().put("Content-Type", response.contentType).toString().toByteArray())
    }

    // MARK: read-through cache

    /**
     * Keeps a response the reader fetched online. The detail JSON also lists the book on the offline shelf, and a
     * resume (`/read` without a chapter) is also stored as that chapter, which is how offline reading looks it up.
     */
    fun putReadThroughAsync(response: ProxyResponse, request: ProxyRequest, bookId: Int) {
        ioExecutor.execute { writeReadThrough(response, request, bookId) }
    }

    private fun writeReadThrough(response: ProxyResponse, request: ProxyRequest, bookId: Int) {
        val dir = cacheDir(bookId)
        writeResponse(response, key(request), dir)
        if (request.path == "/api/books/$bookId") write(File(dir, DETAIL_FILE), response.body)
        if (request.path.endsWith("/read") && request.query("chapter") == null) storeResumeChapter(response, request, dir)
        touch(dir)
        writesSinceEvictionCheck += 1
        if (writesSinceEvictionCheck >= EVICTION_CHECK_INTERVAL) {
            writesSinceEvictionCheck = 0
            evictReadThrough(READ_THROUGH_LIMIT_BYTES)
        }
    }

    private fun storeResumeChapter(response: ProxyResponse, request: ProxyRequest, dir: File) {
        val chapter = try {
            JSONObject(String(response.body)).optInt("current_chapter", -1)
        } catch (error: JSONException) {
            Log.w(LOG_TAG, "Resume answer is not JSON: path=${request.path}", error)
            return
        }
        if (chapter < 0) return
        writeResponse(response, key(ProxyRequest("GET", "${request.path}?chapter=$chapter")), dir)
    }

    /** Detail JSON of books read online but not downloaded, marked `offline_partial` for the shelf. */
    fun partialDetails(): List<JSONObject> = onIoThread {
        cachedIds().filter { !isDownloadedOnIo(it) }.mapNotNull { id ->
            readJson(File(cacheDir(id), DETAIL_FILE))?.put("offline_partial", true)
        }
    }

    fun readThroughSize(): Long = onIoThread { cachedIds().sumOf { folderSize(cacheDir(it)) } }

    fun clearReadThrough() {
        onIoThread { remove(cacheRoot) }
    }

    /** Removes whole books, least recently read first, until the cache is at most [limit] bytes. */
    private fun evictReadThrough(limit: Long) {
        val books = cachedIds().map { id -> Triple(id, folderSize(cacheDir(id)), cacheDir(id).lastModified()) }
        var total = books.sumOf { it.second }
        for ((id, size, _) in books.sortedBy { it.third }) {
            if (total <= limit) break
            remove(cacheDir(id))
            total -= size
        }
    }

    private fun cachedIds(): List<Int> = cacheRoot.list().orEmpty().mapNotNull { it.toIntOrNull() }

    /** The folder's modification date is its last use, which eviction orders by. */
    private fun touch(dir: File) {
        if (dir.exists() && !dir.setLastModified(System.currentTimeMillis())) {
            Log.w(LOG_TAG, "Could not update last use: path=${dir.path}")
        }
    }

    // MARK: books

    fun downloadedBookIds(): List<Int> = onIoThread { downloadedIds() }

    private fun downloadedIds(): List<Int> =
        booksRoot.list().orEmpty().mapNotNull { it.toIntOrNull() }.filter(::isDownloadedOnIo).sorted()

    fun isDownloaded(bookId: Int): Boolean = onIoThread { isDownloadedOnIo(bookId) }

    private fun isDownloadedOnIo(bookId: Int): Boolean = File(bookDir(bookId), COMPLETE_MARKER).exists()

    fun markComplete(bookId: Int) {
        onIoThread { write(File(bookDir(bookId), COMPLETE_MARKER), ByteArray(0)) }
    }

    fun removeBook(bookId: Int) {
        onIoThread { remove(bookDir(bookId)) }
    }

    fun saveDetail(json: ByteArray, bookId: Int) {
        onIoThread { write(File(bookDir(bookId), DETAIL_FILE), json) }
    }

    /** Detail JSON (`/api/books/<id>`) of every downloaded book. */
    fun details(): List<JSONObject> = onIoThread {
        downloadedIds().mapNotNull { readJson(File(bookDir(it), DETAIL_FILE)) }
    }

    fun sizeOfBook(bookId: Int): Long = onIoThread { folderSize(bookDir(bookId)) }

    private fun folderSize(dir: File): Long = dir.listFiles().orEmpty().filter { it.isFile }.sumOf { it.length() }

    // MARK: progress

    fun saveLastProgress(body: ByteArray, bookId: Int) {
        onIoThread { write(File(bookDir(bookId), PROGRESS_FILE), body) }
    }

    fun lastProgress(bookId: Int): JSONObject? = onIoThread { readJson(File(bookDir(bookId), PROGRESS_FILE)) }

    fun queueProgress(body: ByteArray, bookId: Int) {
        onIoThread { write(File(pendingDir, bookId.toString()), body) }
    }

    fun pendingProgress(): List<Pair<Int, ByteArray>> = onIoThread {
        pendingDir.list().orEmpty().mapNotNull { name ->
            val id = name.toIntOrNull() ?: return@mapNotNull null
            id to File(pendingDir, name).readBytes()
        }
    }

    /** Clears the queued save only if it is still the one that was sent, so a newer save is not lost. */
    fun clearPendingProgress(bookId: Int, ifMatching: ByteArray) {
        onIoThread {
            val file = File(pendingDir, bookId.toString())
            if (file.isFile && file.readBytes().contentEquals(ifMatching)) remove(file)
        }
    }

    // MARK: files

    private fun readJson(file: File): JSONObject? {
        if (!file.isFile) return null
        return try {
            JSONObject(file.readText())
        } catch (error: JSONException) {
            Log.w(LOG_TAG, "Stored JSON is damaged: path=${file.path}", error)
            null
        }
    }

    /** Writes through a temporary file, so a crash never leaves a half-written answer that looks complete. */
    private fun write(file: File, data: ByteArray) {
        try {
            file.parentFile?.mkdirs()
            val temporary = File(file.parentFile, file.name + ".tmp")
            temporary.writeBytes(data)
            if (!temporary.renameTo(file)) throw IOException("rename failed: ${temporary.path}")
        } catch (error: IOException) {
            Log.w(LOG_TAG, "Offline store write failed: path=${file.path}", error)
        }
    }

    private fun remove(file: File) {
        if (file.exists() && !file.deleteRecursively()) Log.w(LOG_TAG, "Offline store remove failed: path=${file.path}")
    }

    companion object {
        const val READ_THROUGH_LIMIT = READ_THROUGH_LIMIT_BYTES

        /**
         * Cache key: method, path and query with the `v` / `retry` cache-busters dropped and items sorted, plus a
         * body hash for POSTs.
         */
        fun key(request: ProxyRequest): String {
            val query = request.queryItems
                .filter { it.first !in IGNORED_QUERY_NAMES }
                .map { "${it.first}=${it.second}" }
                .sorted()
                .joinToString("&")
            val bodyHash = request.body?.let(::hexDigest).orEmpty()
            return "${request.method} ${request.path}?$query#$bodyHash"
        }

        private fun hexDigest(data: ByteArray): String =
            MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }
    }
}
