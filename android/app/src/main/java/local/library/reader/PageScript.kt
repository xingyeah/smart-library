package local.library.reader

import org.json.JSONObject

/** Name the native bridge object is exposed under in the page. */
const val BRIDGE_NAME = "LibraryNative"

/**
 * Written into the shell page's `<head>` so it runs before the page's own scripts. It gives the page the iOS
 * contract: `window.webkit.messageHandlers.library.postMessage(...)`, and a `fetch` that sends every non-GET
 * request through the native bridge, since `shouldInterceptRequest` cannot read request bodies.
 */
private const val BRIDGE_SHIM = """
(function () {
  if (window.__libraryShim) return;
  window.__libraryShim = true;
  var bridge = window.$BRIDGE_NAME;
  window.webkit = { messageHandlers: { library: {
    postMessage: function (message) { bridge.post(JSON.stringify(message)); }
  } } };
  var pending = {};
  var nextId = 1;
  window.__libraryResolve = function (id, status, headers, bodyBase64) {
    var entry = pending[id];
    if (!entry) return;
    delete pending[id];
    var binary = atob(bodyBase64);
    var bytes = new Uint8Array(binary.length);
    for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    var isEmpty = status === 204 || status === 205 || status === 304;
    entry.resolve(new Response(isEmpty ? null : bytes, { status: status, headers: headers }));
  };
  window.__libraryReject = function (id, message) {
    var entry = pending[id];
    if (!entry) return;
    delete pending[id];
    entry.reject(new TypeError(message));
  };
  var realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    if (method === 'GET' || method === 'HEAD') return realFetch(input, init);
    var url = new URL(typeof input === 'string' ? input : input.url, location.href);
    var headers = {};
    new Headers((init && init.headers) || {}).forEach(function (value, name) { headers[name] = value; });
    var body = init && typeof init.body === 'string' ? init.body : '';
    return new Promise(function (resolve, reject) {
      var id = nextId++;
      pending[id] = { resolve: resolve, reject: reject };
      var signal = init && init.signal;
      if (signal) {
        var abort = function () {
          if (!pending[id]) return;
          delete pending[id];
          reject(new DOMException('Aborted', 'AbortError'));
        };
        if (signal.aborted) { abort(); return; }
        signal.addEventListener('abort', abort);
      }
      bridge.fetch(id, method, url.pathname + url.search, JSON.stringify(headers), body);
    });
  };
})();
"""

/**
 * `window.libraryAppOffline` (a Set of downloaded book ids) tells the page it runs in the app and which books
 * have an offline copy; `window.libraryAppIsOffline` says whether the server is unreachable. Both are set before
 * the page's scripts run and refreshed on every change, when the page's `libraryAppSetOffline` is also called.
 */
fun offlineStateScript(downloadedBookIds: Collection<Int>, isOffline: Boolean): String =
    "window.libraryAppOffline = new Set([${downloadedBookIds.joinToString(",")}]);" +
        " window.libraryAppIsOffline = $isOffline;" +
        " if (window.libraryAppSetOffline) window.libraryAppSetOffline($isOffline);"

/** The shell page with the shim and the offline state in front of its own scripts; unchanged if it has no `<head>`. */
fun injectPageScripts(html: String, offlineState: String): String {
    val headEnd = html.indexOf("<head>", ignoreCase = true)
    if (headEnd < 0) return html
    val insertAt = headEnd + "<head>".length
    return html.substring(0, insertAt) + "<script>$BRIDGE_SHIM$offlineState</script>" + html.substring(insertAt)
}

/** A string as a JavaScript literal. */
fun jsString(text: String): String = JSONObject.quote(text)
