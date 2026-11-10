package com.example.x5purchases.network

import com.example.x5purchases.model.Product
import com.example.x5purchases.model.Receipt
import org.json.JSONArray
import org.json.JSONObject

object TurboStreamDecoder {

    fun decode(raw: String): List<Receipt> {
        val table = JSONArray(raw)
        val cache = mutableMapOf<Int, Any?>()
        val resolving = mutableSetOf<Int>()

        fun resolve(ref: Any?): Any? {
            if (ref !is Int) return ref
            if (ref < 0 || ref >= table.length()) return ref
            if (ref in cache) return cache[ref]
            if (ref in resolving) return null

            resolving.add(ref)
            val value = table.get(ref)

            val result: Any? = when (value) {
                is JSONArray -> {
                    List(value.length()) { i -> resolve(value.get(i)) }
                }
                is JSONObject -> {
                    val map = mutableMapOf<String, Any?>()
                    val keys = value.keys()
                    while (keys.hasNext()) {
                        val key = keys.next()
                        val child = value.get(key)
                        if (key.startsWith("_") && key.drop(1).all { it.isDigit() }) {
                            val idx = key.drop(1).toInt()
                            val propName = if (idx < table.length()) table.get(idx) else key
                            if (propName is String) {
                                map[propName] = resolve(child)
                            }
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
            ?: throw IllegalStateException("Root is not object")
        val data = decoded["data"] as? Map<*, *>
            ?: throw IllegalStateException("No data object")

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

        val list: List<*> = when (receipts) {
            is Map<*, *> -> receipts.values.toList()
            is List<*> -> receipts
            else -> emptyList<Any>()
        }

        return list.mapNotNull { item ->
            val receiptMap = (item as? Map<*, *>)?.get("receipt") as? Map<*, *>
                ?: item as? Map<*, *>
                ?: return@mapNotNull null

            if (!receiptMap.containsKey("rtlTxnId") &&
                !receiptMap.containsKey("checkNum") &&
                !receiptMap.containsKey("created")
            ) return@mapNotNull null

            val itemsRaw = listOf("items", "positions", "receiptItems", "products", "goods")
                .firstNotNullOfOrNull { key -> receiptMap[key] as? List<*> }

            val products = itemsRaw?.mapNotNull { it as? Map<*, *> }?.map { p ->
                Product(
                    pluId = p["pluId"]?.toString(),
                    name = p["name"]?.toString(),
                    quantity = (p["quantity"] as? Number)?.toDouble(),
                    weightUomCode = p["weightUomCode"]?.toString(),
                    priceRegular = (p["priceRegular"] as? Number)?.toDouble(),
                    pricePromo = (p["pricePromo"] as? Number)?.toDouble(),
                    priceItem = (p["priceItem"] as? Number)?.toDouble()
                )
            } ?: emptyList()

            Receipt(
                rtlTxnId = receiptMap["rtlTxnId"]?.toString(),
                created = receiptMap["created"]?.toString(),
                title = receiptMap["title"]?.toString(),
                nameTc = receiptMap["nameTc"]?.toString(),
                amountPromo = (receiptMap["amountPromo"] as? Number)?.toDouble(),
                amountRegular = (receiptMap["amountRegular"] as? Number)?.toDouble(),
                checkNum = receiptMap["checkNum"]?.toString(),
                fiscalNum = receiptMap["fiscalNum"]?.toString(),
                storeAddress = receiptMap["storeAddress"]?.toString(),
                items = products
            )
        }
    }
}
