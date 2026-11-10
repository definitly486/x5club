package com.example.x5purchases

import android.os.Bundle
import android.view.LayoutInflater
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.example.x5purchases.model.Product
import com.example.x5purchases.model.Receipt
import java.util.Locale

class ReceiptDetailActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_RECEIPT = "extra_receipt"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_receipt_detail)

        @Suppress("DEPRECATION")
        val receipt = intent.getSerializableExtra(EXTRA_RECEIPT) as? Receipt
        if (receipt == null) {
            Toast.makeText(this, "Не удалось открыть чек", Toast.LENGTH_SHORT).show()
            finish()
            return
        }

        findViewById<TextView>(R.id.btnBack).setOnClickListener { finish() }
        findViewById<TextView>(R.id.detailDate).text = receipt.created ?: "Дата неизвестна"
        findViewById<TextView>(R.id.detailShop).text = receipt.nameTc ?: receipt.title ?: "Магазин не указан"
        findViewById<TextView>(R.id.detailTotal).text = money(receipt.amountPromo)
        findViewById<TextView>(R.id.detailRegularTotal).text =
            "Без скидки: ${money(receipt.amountRegular)}"

        val metadata = buildList {
            receipt.checkNum?.takeIf { it.isNotBlank() }?.let { add("Чек № $it") }
            receipt.storeAddress?.takeIf { it.isNotBlank() }?.let { add(it) }
            receipt.fiscalNum?.takeIf { it.isNotBlank() }?.let { add("Фискальный номер: $it") }
        }
        findViewById<TextView>(R.id.detailMetadata).text = metadata.joinToString("\n")

        val productsContainer = findViewById<LinearLayout>(R.id.detailProducts)
        findViewById<TextView>(R.id.detailProductsCount).text = "Товары (${receipt.items.size})"

        if (receipt.items.isEmpty()) {
            findViewById<TextView>(R.id.detailEmpty).visibility = android.view.View.VISIBLE
        } else {
            receipt.items.forEachIndexed { index, product ->
                val row = LayoutInflater.from(this)
                    .inflate(R.layout.item_product_detail, productsContainer, false)
                bindProduct(row, product, index + 1)
                productsContainer.addView(row)
            }
        }
    }

    private fun bindProduct(row: android.view.View, product: Product, number: Int) {
        row.findViewById<TextView>(R.id.productNumber).text = number.toString()
        row.findViewById<TextView>(R.id.productName).text = product.name ?: "Товар без названия"

        val quantity = product.quantity?.let { formatQuantity(it) } ?: "—"
        val unit = product.weightUomCode?.takeIf { it.isNotBlank() }?.let { " $it" }.orEmpty()
        row.findViewById<TextView>(R.id.productQuantity).text = "Количество: $quantity$unit"

        val priceLines = buildList {
            product.pricePromo?.let { add("Цена со скидкой: ${money(it)}") }
            if (product.pricePromo == null) {
                product.priceRegular?.let { add("Обычная цена: ${money(it)}") }
            } else {
                product.priceRegular?.let { add("Обычная цена: ${money(it)}") }
            }
        }
        row.findViewById<TextView>(R.id.productUnitPrices).text =
            priceLines.joinToString("   •   ").ifBlank { "Цена не указана" }
        row.findViewById<TextView>(R.id.productLineTotal).text =
            "Сумма: ${money(product.priceItem ?: product.pricePromo ?: product.priceRegular)}"
    }

    private fun money(value: Double?): String =
        value?.let { String.format(Locale("ru", "RU"), "%.2f ₽", it) } ?: "—"

    private fun formatQuantity(value: Double): String =
        if (value % 1.0 == 0.0) value.toLong().toString()
        else String.format(Locale("ru", "RU"), "%.3f", value).trimEnd('0').trimEnd(',')
}
