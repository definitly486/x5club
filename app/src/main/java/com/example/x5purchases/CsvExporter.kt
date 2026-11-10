package com.example.x5purchases

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import com.example.x5purchases.model.Receipt
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStreamWriter
import java.nio.charset.Charset
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object CsvExporter {

    private val columns = listOf(
        "Дата",
        "Магазин",
        "Номер чека",
        "Фискальный номер",
        "Фискальный признак",
        "Адрес магазина",
        "ОФД",
        "Сумма чека",
        "Сумма без скидки",
        "PLU",
        "Название товара",
        "Количество",
        "Единица измерения",
        "Обычная цена",
        "Цена со скидкой",
        "Сумма товара"
    )

    /**
     * Сохраняет CSV в папку Downloads.
     * Возвращает путь / display name файла.
     */
    fun export(context: Context, receipts: List<Receipt>): String {
        if (receipts.isEmpty()) {
            throw IllegalStateException("Нет данных для экспорта")
        }

        val timestamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
        val fileName = "x5_purchases_$timestamp.csv"

        val csvContent = buildString {
            // BOM для корректного открытия в Excel
            append('\uFEFF')
            append(columns.joinToString(";") { escape(it) })
            append("\n")

            for (receipt in receipts) {
                val shop = receipt.nameTc ?: receipt.title ?: ""
                val items = receipt.items.ifEmpty { listOf(null) }

                for (item in items) {
                    val row = listOf(
                        receipt.created ?: "",
                        shop,
                        receipt.checkNum ?: "",
                        receipt.fiscalNum ?: "",
                        receipt.fiscalSignNum ?: "",
                        receipt.storeAddress ?: "",
                        receipt.ofdurl ?: "",
                        receipt.amountPromo?.toString() ?: "",
                        receipt.amountRegular?.toString() ?: "",
                        item?.pluId ?: "",
                        item?.name ?: "",
                        item?.quantity?.toString() ?: "",
                        item?.weightUomCode ?: "",
                        item?.priceRegular?.toString() ?: "",
                        item?.pricePromo?.toString() ?: "",
                        item?.priceItem?.toString() ?: ""
                    )
                    append(row.joinToString(";") { escape(it) })
                    append("\n")
                }
            }
        }

        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            saveViaMediaStore(context, fileName, csvContent)
        } else {
            saveLegacy(context, fileName, csvContent)
        }
    }

    private fun escape(value: String): String {
        return if (value.contains(';') || value.contains('"') || value.contains('\n')) {
            "\"${value.replace("\"", "\"\"")}\""
        } else {
            value
        }
    }

    private fun saveViaMediaStore(context: Context, fileName: String, content: String): String {
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, fileName)
            put(MediaStore.Downloads.MIME_TYPE, "text/csv")
            put(MediaStore.Downloads.IS_PENDING, 1)
        }

        val resolver = context.contentResolver
        val uri: Uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: throw RuntimeException("Не удалось создать файл в Downloads")

        resolver.openOutputStream(uri)?.use { stream ->
            OutputStreamWriter(stream, Charset.forName("UTF-8")).use { writer ->
                writer.write(content)
            }
        } ?: throw RuntimeException("Не удалось открыть поток записи")

        values.clear()
        values.put(MediaStore.Downloads.IS_PENDING, 0)
        resolver.update(uri, values, null, null)

        return "Downloads/$fileName"
    }

    @Suppress("DEPRECATION")
    private fun saveLegacy(context: Context, fileName: String, content: String): String {
        val dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
        if (!dir.exists()) dir.mkdirs()

        val file = File(dir, fileName)
        FileOutputStream(file).use { stream ->
            OutputStreamWriter(stream, Charset.forName("UTF-8")).use { writer ->
                writer.write(content)
            }
        }
        return file.absolutePath
    }
}
