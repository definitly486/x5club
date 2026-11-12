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
    private const val MAX_PAGES = 100

    private val client = OkHttpClient.Builder()
        .connectTimeout(40, TimeUnit.SECONDS)
        .readTimeout(40, TimeUnit.SECONDS)
        .writeTimeout(40, TimeUnit.SECONDS)
        .followRedirects(true)
        .followSslRedirects(true)
        .build()

    // Keep the same request headers as the known-working test_x5.py script.
    private val commonHeaders = Headers.Builder()
        .add("User-Agent", "Mozilla/5.0 (X11; Linux x86_64; rv:159.0) Gecko/20100101 Firefox/159.0")
        .add("Accept", "*/*")
        .add("Accept-Language", "ru")
        .add("Referer", "https://x5club.ru/")
        .add("Origin", "https://x5club.ru")
        .add("Sec-Fetch-Dest", "empty")
        .add("Sec-Fetch-Mode", "cors")
        .add("Sec-Fetch-Site", "same-origin")
        .add("Cache-Control", "no-cache")
        .add("Pragma", "no-cache")
        .build()

    // Existing preset-period API is kept unchanged.
    fun fetchReceipts(cookieHeader: String, days: Int = 30): List<Receipt> {
        require(days >= 1) { "Период должен быть больше нуля" }
        val dateTo = LocalDate.now()
        val dateFrom = dateTo.minusDays((days - 1).toLong())
        return fetchReceipts(cookieHeader, dateFrom, dateTo)
    }

    // Fetch receipts for an explicit inclusive date range.
    fun fetchReceipts(
        cookieHeader: String,
        dateFrom: LocalDate,
        dateTo: LocalDate
    ): List<Receipt> {
        require(!dateFrom.isAfter(dateTo)) { "Начальная дата не может быть позже конечной" }
        require(cookieHeader.isNotBlank()) { "Отсутствуют cookies авторизации" }

        val formatter = DateTimeFormatter.ISO_LOCAL_DATE
        val allReceipts = mutableListOf<Receipt>()
        val seen = mutableSetOf<String>()

        for (page in 1..MAX_PAGES) {
            val form = FormBody.Builder()
                .add("from", dateFrom.format(formatter))
                .add("to", dateTo.format(formatter))
                .add("type", "receipts")
                .add("codeTc", "D,S,VK,SL,VP")
                .apply {
                    if (page == 1) add("isNewData", "true")
                    else add("page", page.toString())
                }
                .build()

            val request = Request.Builder()
                .url(BASE_URL)
                .headers(commonHeaders)
                .header("Cookie", cookieHeader)
                .header("Content-Type", "application/x-www-form-urlencoded;charset=UTF-8")
                .post(form)
                .build()

            client.newCall(request).execute().use { response ->
                if (response.code == 401 || response.code == 403) {
                    throw IllegalStateException("Ошибка авторизации: HTTP ${response.code}. Обновите cookies, войдя в аккаунт заново.")
                }
                if (response.request.url.encodedPath.contains("login", ignoreCase = true)) {
                    throw IllegalStateException("X5 перенаправил на страницу входа. Сессия истекла — войдите заново.")
                }
                if (!response.isSuccessful) {
                    throw IllegalStateException("Ошибка X5: HTTP ${response.code}")
                }

                val raw = response.body?.string()?.trim().orEmpty()
                if (raw.isBlank()) throw IllegalStateException("Сервер X5 вернул пустой ответ")
                if (raw.startsWith("<")) {
                    throw IllegalStateException("Вместо истории получен HTML. Возможно, сессия X5 истекла — войдите заново.")
                }

                val pageReceipts = TurboStreamDecoder.decode(raw)
                if (pageReceipts.isEmpty()) return allReceipts.sortedByCreatedDesc()

                var added = 0
                for (receipt in pageReceipts) {
                    val key = "${receipt.rtlTxnId}|${receipt.checkNum}|${receipt.created}"
                    if (seen.add(key)) {
                        allReceipts.add(receipt)
                        added++
                    }
                }

                if (added == 0) return allReceipts.sortedByCreatedDesc()
            }
        }

        return allReceipts.sortedByCreatedDesc()
    }

    // Сортировка по реальному времени чека, а не по строке даты.
    // Сортировка стабильная: чеки с нераспознанной датой уходят в конец
    // и сохраняют порядок сервера.
    private fun List<Receipt>.sortedByCreatedDesc(): List<Receipt> =
        sortedByDescending { ReceiptDate.toEpochMillis(it.created) ?: Long.MIN_VALUE }

}
