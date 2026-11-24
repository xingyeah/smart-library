package local.library.reader

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class ServerConfigTest {
    @Test
    fun parseSplitsAddressAndToken() {
        val config = ServerConfig.parse(" http://192.168.1.5:8765/?token=abc%20def ")

        assertEquals("http://192.168.1.5:8765", config.baseUrl)
        assertEquals("abc def", config.token)
        assertEquals(listOf("http://192.168.1.5:8765"), config.addresses)
    }

    @Test
    fun parseAcceptsAddressWithoutToken() {
        val config = ServerConfig.parse("http://10.0.2.2:8765")

        assertEquals("", config.token)
    }

    @Test
    fun parseRejectsNonHttpAndHostless() {
        assertThrows(InvalidServerAddressException::class.java) { ServerConfig.parse("ftp://host/") }
        assertThrows(InvalidServerAddressException::class.java) { ServerConfig.parse("not a url") }
        assertThrows(InvalidServerAddressException::class.java) { ServerConfig.parse("http:///?token=x") }
    }

    @Test
    fun savedTokenFillsOnlyAnAddressWithoutOne() {
        val bare = ServerConfig.parse("http://10.0.0.2:8765")
        val own = ServerConfig.parse("http://10.0.0.2:8765/?token=abc")

        assertEquals("saved", bare.withFallbackToken("saved").token)
        assertEquals("abc", own.withFallbackToken("saved").token)
        assertEquals("", bare.withFallbackToken("").token)
    }

    @Test
    fun addingKeepsKnownAddressesAndSkipsDuplicates() {
        val config = ServerConfig.of("http://a:1", "t").adding(listOf("http://b:2/", "http://a:1"))

        assertEquals(listOf("http://a:1", "http://b:2"), config.addresses)
    }

    @Test
    fun removingNeverEmptiesTheList() {
        val two = ServerConfig.of("http://a:1", "t", listOf("http://a:1", "http://b:2"))

        assertEquals(listOf("http://a:1"), two.removing("http://b:2").addresses)
        assertEquals(listOf("http://a:1"), two.removing("http://b:2").removing("http://a:1").addresses)
    }
}
