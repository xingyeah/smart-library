package local.library.reader

import android.app.AlertDialog
import android.content.Context
import android.webkit.JsPromptResult
import android.webkit.JsResult
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.widget.EditText

/**
 * Shows the page's `alert`, `confirm` and `prompt`. Without it the web view drops them and `confirm()` answers
 * false, so deleting a book would do nothing.
 */
class PageChromeClient : WebChromeClient() {

    override fun onJsAlert(view: WebView, url: String?, message: String, result: JsResult): Boolean {
        builder(view.context, message)
            .setPositiveButton(R.string.dialog_ok) { _, _ -> result.confirm() }
            .setOnCancelListener { result.confirm() }
            .show()
        return true
    }

    override fun onJsConfirm(view: WebView, url: String?, message: String, result: JsResult): Boolean {
        builder(view.context, message)
            .setPositiveButton(R.string.dialog_confirm) { _, _ -> result.confirm() }
            .setNegativeButton(R.string.dialog_cancel) { _, _ -> result.cancel() }
            .setOnCancelListener { result.cancel() }
            .show()
        return true
    }

    override fun onJsPrompt(
        view: WebView, url: String?, message: String, defaultValue: String?, result: JsPromptResult,
    ): Boolean {
        val input = EditText(view.context).apply { setText(defaultValue.orEmpty()) }
        builder(view.context, message)
            .setView(input)
            .setPositiveButton(R.string.dialog_confirm) { _, _ -> result.confirm(input.text.toString()) }
            .setNegativeButton(R.string.dialog_cancel) { _, _ -> result.cancel() }
            .setOnCancelListener { result.cancel() }
            .show()
        return true
    }

    private fun builder(context: Context, message: String): AlertDialog.Builder =
        AlertDialog.Builder(context, android.R.style.Theme_DeviceDefault_Dialog_Alert).setMessage(message)
}
