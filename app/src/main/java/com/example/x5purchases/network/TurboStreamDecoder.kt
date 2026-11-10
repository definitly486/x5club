package com.example.x5purchases.network

import com.example.x5purchases.model.Product
import com.example.x5purchases.model.Receipt
import org.json.JSONArray
import org.json.JSONObject

/** Decodes the Remix Turbo Stream response used by x5club.ru. */
object TurboStreamDecoder {

    private fun asDouble(value: Any?): Double? = when (value) {
        is Number -> value.toDouble()
        is String -> value.trim()
            .replace("\u00A0", "")
            .replace(" ", "")
            .replace(',', '.')
            .toDoubleOrNull()
        else -> null
    }

    private fun asString(value: Any?): String? = when (value) {
        null, JSONObject.NULL -> null
        else -> value.toString()
    }

    fun decode(raw: String): List<Receipt> {
        val table = try {
            JSONArray(raw)
        } catch (e: Exception) {
            throw IllegalStateException("Не удалось разобрать ответ X5 Turbo Stream: ${e.message}", e)
        }

        val cache = mutableMapOf<Int, Any?>()
        val resolving = mutableSetOf<Int>()

        fun resolve(ref: Any?): Any? {
            if (ref !is Int) return ref
            if (ref < 0 || ref >= table.length()) return ref
            if (ref in cache) return cache[ref]
            if (!resolving.add(ref)) return null

            val value = table.get(ref)
            val result: Any? = when (value) {
                is JSONArray -> List(value.length()) { i -> resolve(value.get(i)) }
                is JSONObject -> {
                    val map = mutableMapOf<String, Any?>()
                    val keys = value.keys()
                    while (keys.hasNext()) {
                        val key = keys.next()
                        val child = value.get(key)
                        if (key.startsWith("_") && key.drop(1).isNotEmpty() && key.drop(1).all { it.isDigit() }) {
                            val idx = key.drop(1).toInt()
                            val propertyName = if (idx < table.length()) table.get(idx) else key
                            if (propertyName is String) map[propertyName] = resolve(child)
                        } else {
                            map[key] = resolve(child)
                        }
                    }
                    map
                }
                else -> value
            }

            resolving.remove(ref)
            cache[ref] = result
            return result
        }

        val decoded = resolve(0) as? Map<*, *>
            ?: throw IllegalStateException("Корневой элемент Turbo Stream не является объектом")
        val data = decoded["data"] as? Map<*, *>
            ?: throw IllegalStateException("В ответе Turbo Stream отсутствует объект data")

        var receipts: Any? = data["additionalReceiptsResponse"]
        if (receipts == null) {
            val routes = decoded["routes"] as? Map<*, *>
            val auth = routes?.get("_auth") as? Map<*, *>
            val lk = auth?.get("lk") as? Map<*, *>
            val history = lk?.get("history") as? Map<*, *>
            val historyData = history?.get("data") as? Map<*, *>
            val receiptsResponse = historyData?.get("receiptsResponse") as? Map<*, *>
            receipts = receiptsResponse?.get("data")
        }

        val receiptList: List<*> = when (receipts) {
            is Map<*, *> -> receipts.values.toList()
            is List<*> -> receipts
            else -> emptyList<Any>()
        }

        return receiptList.mapNotNull { item ->
            val receiptMap = (item as? Map<*, *>)?.get("receipt") as? Map<*, *>
                ?: item as? Map<*, *>
                ?: return@mapNotNull null

            if (!receiptMap.containsKey("rtlTxnId") &&
                !receiptMap.containsKey("checkNum") &&
                !receiptMap.containsKey("fiscalNum") &&
                !receiptMap.containsKey("created")
            ) return@mapNotNull null

            val itemsRaw = listOf("items", "positions", "receiptItems", "products", "goods")
                .firstNotNullOfOrNull { key -> receiptMap[key] as? List<*> }

            val products = itemsRaw.orEmpty().mapNotNull { it as? Map<*, *> }.map { product ->
                Product(
                    pluId = asString(product["pluId"]),
                    name = asString(product["name"]),
                    quantity = asDouble(product["quantity"]),
                    weightUomCode = asString(product["weightUomCode"]),
                    priceRegular = asDouble(product["priceRegular"]),
                    pricePromo = asDouble(product["pricePromo"]),
                    priceItem = asDouble(product["priceItem"])
                )
            }

            Receipt(
                rtlTxnId = asString(receiptMap["rtlTxnId"]),
                created = asString(receiptMap["created"]),
                title = asString(receiptMap["title"]),
                nameTc = asString(receiptMap["nameTc"]),
                amountPromo = asDouble(receiptMap["amountPromo"]),
                amountRegular = asDouble(receiptMap["amountRegular"]),
                checkNum = asString(receiptMap["checkNum"]),
                fiscalNum = asString(receiptMap["fiscalNum"]),
                fiscalSignNum = asString(receiptMap["fiscalSignNum"]),
                storeAddress = asString(receiptMap["storeAddress"]),
                ofdurl = asString(receiptMap["ofdurl"]),
                items = products
            )
        }
    }
}
