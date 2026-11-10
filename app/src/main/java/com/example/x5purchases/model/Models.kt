package com.example.x5purchases.model

data class Receipt(
    val rtlTxnId: String? = null,
    val created: String? = null,
    val title: String? = null,
    val nameTc: String? = null,
    val amountPromo: Double? = null,
    val amountRegular: Double? = null,
    val checkNum: String? = null,
    val fiscalNum: String? = null,
    val fiscalSignNum: String? = null,
    val storeAddress: String? = null,
    val ofdurl: String? = null,
    val items: List<Product> = emptyList()
)

data class Product(
    val pluId: String? = null,
    val name: String? = null,
    val quantity: Double? = null,
    val weightUomCode: String? = null,
    val priceRegular: Double? = null,
    val pricePromo: Double? = null,
    val priceItem: Double? = null
)
