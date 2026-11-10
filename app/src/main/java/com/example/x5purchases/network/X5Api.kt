package com.example.x5purchases.network

import com.example.x5purchases.model.Receipt
import okhttp3.FormBody
import okhttp3.Headers
import okhttp3.OkHttpClient
import okhttp3.Request
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.util.concurrent.TimeUnit

object X5Api {

    private const val BASE_URL = "https://x5club.ru/lk/history.data"

    private val client = OkHttpClient.Builder()
        .connectTimeout(40, TimeUnit.SECONDS)
        .readTimeout(40, TimeUnit.SECONDS)
        .build()

    private val commonHeaders = Headers.Builder()
        .add(
            "User-Agent",
            "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 " +
                "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        )
        .add("Accept", "*/*")
        .add("Accept-Language", "ru")
        .add("Referer", "https://x5club.ru/")
        .add("Origin", "https://x5club.ru")
        .add("Sec-Fetch-Dest", "empty")
        .add("Sec-Fetch-Mode", "cors")
        .add("Sec-Fetch-Site", "same-origin")
        .build()

    fun fetchReceipts(cookieHeader: String, days: Int = 30): List<Receipt> {
        val today = LocalDate.now()
        val from = today.minusDays((days - 1).toLong())
        val formatter = DateTimeFormatter.ISO_LOCAL_DATE

        val all = mutableListOf<Receipt>()
        val seen = mutableSetOf<String>()

        for (page in 1..50) {
            val formBuilder = FormBody.Builder()
                .add("from", from.format(formatter))
                .add("to", today.format(formatter))
                .add("type", "receipts")
                .add("codeTc", "D,S,VK,SL,VP")

            if (page == 1) {
                formBuilder.add("isNewData", "true")
            } else {
                formBuilder.add("page", page.toString())
            }

            val request = Request.Builder()
                .url(BASE_URL)
                .headers(commonHeaders)
                .header("Cookie", cookieHeader)
                .post(formBuilder.build())
                .build()

            client.newCall(request).execute().use { response ->
                if (response.code == 401 || response.code == 403) {
                    throw Exception("Сессия истекла. Нужно войти заново.")
                }
                if (!response.isSuccessful) {
                    throw Exception("HTTP ${response.code}")
                }

                val raw = response.body?.string()?.trim()
                    ?: throw Exception("Пустой ответ сервера")

                if (raw.startsWith("<")) {
                    throw Exception("Получен HTML — сессия истекла")
                }

                val pageReceipts = TurboStreamDecoder.decode(raw)
                if (pageReceipts.isEmpty()) return all

                var added = 0
                for (r in pageReceipts) {
                    val key = "${r.rtlTxnId}|${r.checkNum}|${r.created}"
                    if (key !in seen) {
                        seen.add(key)
                        all.add(r)
                        added++
                    }
                }
                if (added == 0) return all
            }
        }
        return all.sortedByDescending { it.created }
    }
}
