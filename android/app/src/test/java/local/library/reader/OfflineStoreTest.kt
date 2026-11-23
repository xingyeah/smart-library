package local.library.reader

import java.io.File
import java.nio.file.Files
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class OfflineStoreTest {
    private lateinit var root: File
    private lateinit var store: OfflineStore

    @Before
    fun setUp() {
        root = Files.createTempDirectory("offline-store-test").toFile()
        store = OfflineStore(root)
    }

    @After
    fun tearDown() {
        root.deleteRecursively()
    }

    private fun jsonResponse(text: String) =
        ProxyResponse(200, mapOf("Content-Type" to "application/json"), text.toByteArray())

    @Test
    fun keyDropsCacheBustersAndSortsQuery() {
        val first = OfflineStore.key(ProxyRequest("GET", "/api/books/1/pdf_page/3?w=900&v=abc"))
        val second = OfflineStore.key(ProxyRequest("GET", "/api/books/1/pdf_page/3?retry=1&w=900"))

        assertEquals(first, second)
    }

    @Test
    fun keySeparatesPostBodies() {
        val one = OfflineStore.key(ProxyRequest("POST", "/api/translate", "a".toByteArray()))
        val two = OfflineStore.key(ProxyRequest("POST", "/api/translate", "b".toByteArray()))

        assertTrue(one != two)
    }

    @Test
    fun downloadedBookAnswersFromItsFolder() {
        val key = OfflineStore.key(ProxyRequest("GET", "/api/books/7/read?chapter=0"))
        store.put(jsonResponse("""{"ok":true}"""), key, 7)

        assertEquals("""{"ok":true}""", String(store.get(key, 7)!!.body))
        assertEquals("application/json", store.get(key, 7)!!.contentType)
        assertNull(store.get(key, 8))
    }

    @Test
    fun bookCountsAsDownloadedOnlyWhenMarkedComplete() {
        store.saveDetail("""{"id":7,"title":"T"}""".toByteArray(), 7)
        assertFalse(store.isDownloaded(7))

        store.markComplete(7)

        assertEquals(listOf(7), store.downloadedBookIds())
        assertEquals("T", store.details().single().getString("title"))
    }

    @Test
    fun removeBookDeletesItsFiles() {
        store.saveDetail("{}".toByteArray(), 7)
        store.markComplete(7)

        store.removeBook(7)

        assertTrue(store.downloadedBookIds().isEmpty())
    }

    @Test
    fun readThroughBooksListAsPartialAndStoreResumeChapter() {
        val detail = ProxyRequest("GET", "/api/books/9")
        store.putReadThroughAsync(jsonResponse("""{"id":9,"title":"Partial"}"""), detail, 9)
        val resume = ProxyRequest("GET", "/api/books/9/read")
        store.putReadThroughAsync(jsonResponse("""{"ok":true,"current_chapter":4}"""), resume, 9)

        val partial = store.partialDetails().single()
        assertTrue(partial.getBoolean("offline_partial"))
        val chapterKey = OfflineStore.key(ProxyRequest("GET", "/api/books/9/read?chapter=4"))
        assertNotNull(store.get(chapterKey, 9))
        assertTrue(store.readThroughSize() > 0)

        store.clearReadThrough()
        assertTrue(store.partialDetails().isEmpty())
    }

    @Test
    fun pendingProgressKeepsOnlyTheLatestAndClearsOnlyWhenUnchanged() {
        store.queueProgress("old".toByteArray(), 5)
        store.queueProgress("new".toByteArray(), 5)

        store.clearPendingProgress(5, "old".toByteArray())
        assertArrayEquals("new".toByteArray(), store.pendingProgress().single().second)

        store.clearPendingProgress(5, "new".toByteArray())
        assertTrue(store.pendingProgress().isEmpty())
    }

    @Test
    fun lastProgressRoundTrips() {
        store.saveLastProgress("""{"chapter_idx":3}""".toByteArray(), 5)

        assertEquals(3, store.lastProgress(5)!!.getInt("chapter_idx"))
        assertNull(store.lastProgress(6))
    }

    @Test
    fun offlineShelfFiltersBySearchAndAuthor() {
        store.saveDetail("""{"id":1,"title":"Alpha","author":"Ann Lee"}""".toByteArray(), 1)
        store.markComplete(1)
        store.saveDetail("""{"id":2,"title":"Beta","authors":[{"name":"Bo Chan"}]}""".toByteArray(), 2)
        store.markComplete(2)
        val responder = OfflineResponder(store)

        val all = JSONObject(String(responder.respond(ProxyRequest("GET", "/api/books")).body))
        val search = JSONObject(String(responder.respond(ProxyRequest("GET", "/api/books?q=beta")).body))
        val byAuthor = JSONObject(String(responder.respond(ProxyRequest("GET", "/api/books?author=ann%20lee")).body))

        assertEquals(2, all.getInt("total"))
        assertEquals("Beta", search.getJSONArray("items").getJSONObject(0).getString("title"))
        assertEquals("Alpha", byAuthor.getJSONArray("items").getJSONObject(0).getString("title"))
    }

    @Test
    fun uncachedEndpointAnswers503() {
        val response = OfflineResponder(store).respond(ProxyRequest("GET", "/api/stats"))

        assertEquals(503, response.status)
    }

    @Test
    fun assetPathsAreFoundAndAmpersandsDecoded() {
        val html = """<img src="/api/books/3/epub_asset?path=a.png&amp;v=1"> <img src='/api/books/3/mobi_image?i=2'>"""

        assertEquals(
            setOf("/api/books/3/epub_asset?path=a.png&v=1", "/api/books/3/mobi_image?i=2"),
            BookDownloader.assetPaths(html),
        )
    }

    @Test
    fun stampOpenedAtKeepsAnExistingTime() {
        val stamped = JSONObject(String(LibraryProxy.stampOpenedAt("""{"offset":0.5}""".toByteArray())))
        val kept = JSONObject(String(LibraryProxy.stampOpenedAt("""{"opened_at":42}""".toByteArray())))

        assertTrue(stamped.getLong("opened_at") > 0)
        assertEquals(42, kept.getInt("opened_at"))
    }
}
