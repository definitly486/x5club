package com.example.x5purchases.network

import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * Разбирает строковое поле created чека в момент времени (epoch millis).
 * Сортировать строку даты нельзя: "31.10.2026" > "01.12.2026" лексикографически,
 * из-за этого при выборе периода через календарь чеки показывались вразнобой.
 */
object ReceiptDate {

    private val zone: ZoneId = ZoneId.systemDefault()

    private val dateTimeFormats = listOf(
        "dd.MM.yyyy HH:mm:ss",
        "dd.MM.yyyy HH:mm",
        "d.M.yyyy HH:mm:ss",
        "d.M.yyyy HH:mm",
        "yyyy-MM-dd HH:mm:ss",
        "yyyy-MM-dd HH:mm"
    ).map { DateTimeFormatter.ofPattern(it, Locale.US) }

    private val dateFormats = listOf(
        "dd.MM.yyyy",
        "d.M.yyyy",
        "yyyy-MM-dd"
    ).map { DateTimeFormatter.ofPattern(it, Locale.US) }

    fun toEpochMillis(raw: String?): Long? {
        val s = raw?.trim().orEmpty()
        if (s.isEmpty()) return null

        // Unix-время: секунды (10 цифр) или миллисекунды (13 цифр)
        if (s.all { it.isDigit() }) {
            return when (s.length) {
                13 -> s.toLongOrNull()
                10 -> s.toLongOrNull()?.let { it * 1000L }
                else -> null
            }
        }

        // ISO с часовым поясом: 2026-11-12T18:24:00+03:00 / 2026-11-12T18:24:00Z
        runCatching { return OffsetDateTime.parse(s).toInstant().toEpochMilli() }
        runCatching { return Instant.parse(s).toEpochMilli() }
        // ISO без пояса: 2026-11-12T18:24:00
        runCatching { return LocalDateTime.parse(s).atZone(zone).toInstant().toEpochMilli() }

        // "12.11.2026, 18:24" -> "12.11.2026 18:24"
        val normalized = s.replace(',', ' ').replace(Regex("\\s+"), " ").trim()

        for (f in dateTimeFormats) {
            runCatching {
                return LocalDateTime.parse(normalized, f).atZone(zone).toInstant().toEpochMilli()
            }
        }
        for (f in dateFormats) {
            runCatching {
                return LocalDate.parse(normalized, f).atStartOfDay(zone).toInstant().toEpochMilli()
            }
        }
        return null
    }
}
