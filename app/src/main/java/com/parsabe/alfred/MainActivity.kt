package com.parsabe.alfred

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.os.Bundle
import android.text.InputType
import android.view.WindowManager
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.Toast
import androidx.activity.addCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat

/**
 * Alfred for Android: the ribbon interface from your Alfred server, full screen, with the
 * microphone bridged to Android's permission system so hands-free "Alfred, …" works.
 *
 * Everything Alfred does happens on the server; this app shows his page, keeps the screen on
 * while it's open, and remembers which server to talk to.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private var server: String? = null
    private var pendingMicRequest: PermissionRequest? = null

    private val micPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            val request = pendingMicRequest ?: return@registerForActivityResult
            pendingMicRequest = null
            if (granted) {
                request.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE))
            } else {
                request.deny()
                Toast.makeText(this, R.string.mic_denied, Toast.LENGTH_LONG).show()
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Alfred listens while he's on screen; don't let the screen turn off under him.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        webView = WebView(this)
        webView.setBackgroundColor(Color.BLACK)
        setContentView(webView, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
        configureWebView()

        onBackPressedDispatcher.addCallback(this) {
            if (webView.canGoBack()) webView.goBack() else finish()
        }

        server = Prefs.server(this)
        when {
            savedInstanceState != null -> webView.restoreState(savedInstanceState)
            server == null || intent?.action == ACTION_CHANGE_SERVER -> askForServer()
            else -> open(server!!)
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.action == ACTION_CHANGE_SERVER) askForServer()
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun configureWebView() {
        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        with(webView.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true // the token and preferences live in the page's storage
            mediaPlaybackRequiresUserGesture = false // Alfred answers aloud without a tap
            allowFileAccess = false
            allowContentAccess = false
        }

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val base = server ?: return false
                if (ServerConfig.sameOrigin(base, request.url.toString())) return false
                // links to other sites open in the browser, not inside Alfred
                startActivity(Intent(Intent.ACTION_VIEW, request.url))
                return true
            }

            override fun onReceivedError(
                view: WebView, request: WebResourceRequest, error: WebResourceError,
            ) {
                if (request.isForMainFrame) showUnreachable(error.description?.toString() ?: "")
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest) {
                val base = server
                val wantsMic = PermissionRequest.RESOURCE_AUDIO_CAPTURE in request.resources
                // only Alfred's own server may use the microphone
                if (!wantsMic || base == null || !ServerConfig.sameOrigin(base, request.origin.toString())) {
                    request.deny()
                    return
                }
                val granted = ContextCompat.checkSelfPermission(
                    this@MainActivity, Manifest.permission.RECORD_AUDIO,
                ) == PackageManager.PERMISSION_GRANTED
                if (granted) {
                    request.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE))
                } else {
                    pendingMicRequest?.deny()
                    pendingMicRequest = request
                    micPermission.launch(Manifest.permission.RECORD_AUDIO)
                }
            }
        }
    }

    private fun open(base: String) {
        server = base
        webView.loadUrl(base)
    }

    private fun askForServer(error: String? = null) {
        val input = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            hint = getString(R.string.server_hint)
            setText(server ?: "")
            setSingleLine()
        }
        val padding = (20 * resources.displayMetrics.density).toInt()
        val frame = FrameLayout(this).apply { setPadding(padding, padding / 2, padding, 0); addView(input) }
        val builder = AlertDialog.Builder(this)
            .setTitle(R.string.server_title)
            .setMessage(error ?: getString(R.string.server_message))
            .setView(frame)
            .setCancelable(server != null)
            .setPositiveButton(R.string.connect) { _, _ ->
                val base = ServerConfig.normalize(input.text.toString())
                if (base == null) {
                    askForServer(getString(R.string.invalid_server))
                    return@setPositiveButton
                }
                Prefs.setServer(this, base)
                if (!ServerConfig.isSecure(base)) {
                    Toast.makeText(this, R.string.insecure_server, Toast.LENGTH_LONG).show()
                }
                open(base)
            }
        if (server != null) builder.setNegativeButton(R.string.cancel, null)
        builder.show()
    }

    private fun showUnreachable(reason: String) {
        if (isFinishing) return
        AlertDialog.Builder(this)
            .setTitle(R.string.unreachable_title)
            .setMessage(getString(R.string.unreachable_message, server ?: "", reason))
            .setPositiveButton(R.string.retry) { _, _ -> server?.let { open(it) } }
            .setNegativeButton(R.string.change_server) { _, _ -> askForServer() }
            .setCancelable(false)
            .show()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }

    override fun onResume() {
        super.onResume()
        webView.onResume()
    }

    override fun onPause() {
        webView.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        pendingMicRequest?.deny()
        webView.destroy()
        super.onDestroy()
    }

    companion object {
        const val ACTION_CHANGE_SERVER = "com.parsabe.alfred.CHANGE_SERVER"
    }
}

/** The chosen server, kept in the app's private preferences. */
object Prefs {
    private const val FILE = "alfred"
    private const val SERVER = "server"

    fun server(context: Context): String? =
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).getString(SERVER, null)

    fun setServer(context: Context, base: String) {
        context.getSharedPreferences(FILE, Context.MODE_PRIVATE).edit().putString(SERVER, base).apply()
    }
}
