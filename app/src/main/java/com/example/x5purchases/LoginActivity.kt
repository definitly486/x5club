package com.example.x5purchases

import android.annotation.SuppressLint
import android.content.Intent
import android.os.Bundle
import android.webkit.CookieManager
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

class LoginActivity : AppCompatActivity() {

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_login)

        val webView = findViewById<WebView>(R.id.webView)
        val btnDone = findViewById<Button>(R.id.btnDone)

        val cookieManager = CookieManager.getInstance()
        cookieManager.setAcceptCookie(true)
        cookieManager.setAcceptThirdPartyCookies(webView, true)

        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.settings.userAgentString =
            "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 " +
                "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"

        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                // Можно автоматически определять успешный вход по URL
            }
        }

        webView.loadUrl("https://x5club.ru/lk")

        btnDone.setOnClickListener {
            val cookies = cookieManager.getCookie("https://x5club.ru")

            if (cookies.isNullOrBlank()) {
                Toast.makeText(
                    this,
                    "Cookies не найдены. Войдите в аккаунт на сайте.",
                    Toast.LENGTH_LONG
                ).show()
                return@setOnClickListener
            }

            getSharedPreferences("x5", MODE_PRIVATE)
                .edit()
                .putString("cookies", cookies)
                .apply()

            startActivity(Intent(this, HistoryActivity::class.java))
            finish()
        }
    }
}
