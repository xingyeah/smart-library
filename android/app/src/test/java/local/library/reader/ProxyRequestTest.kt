package local.library.reader

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ProxyRequestTest {
    @Test
    fun keepsPathAndRawQueryApart() {
        val request = ProxyRequest("GET", "/api/books?q=a%26b&page=2")

        assertEquals("/api/books", request.path)
        assertEquals("q=a%26b&page=2", request.rawQuery)
        assertEquals("/api/books?q=a%26b&page=2", request.pathAndQuery)
    }

    @Test
    fun decodesQueryValues() {
        val request = ProxyRequest("GET", "/api/books?q=%E4%B8%89%E4%BD%93&flag")

        assertEquals("三体", request.query("q"))
        assertEquals("", request.query("flag"))
        assertNull(request.query("missing"))
    }

    @Test
    fun readsBookIdOnlyFromBookPaths() {
        assertEquals(42, ProxyRequest("GET", "/api/books/42/read?chapter=1").bookId)
        assertNull(ProxyRequest("GET", "/api/books").bookId)
        assertNull(ProxyRequest("GET", "/covers/42").bookId)
        assertNull(ProxyRequest("GET", "/api/books/abc").bookId)
    }
}
