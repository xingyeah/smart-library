package local.library.reader

/** Answers the web page's requests while the server cannot be reached. Phase B fills it from the offline store. */
object OfflineResponder {
    fun respond(request: ProxyRequest): ProxyResponse = ProxyResponse.offlineUnavailable()
}
