package com.example.x5purchases.adapter

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.content.Intent
import com.example.x5purchases.ReceiptDetailActivity
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.example.x5purchases.R
import com.example.x5purchases.model.Receipt

class ReceiptsAdapter(
    private val items: List<Receipt>
) : RecyclerView.Adapter<ReceiptsAdapter.VH>() {

    class VH(view: View) : RecyclerView.ViewHolder(view) {
        val tvDate: TextView = view.findViewById(R.id.tvDate)
        val tvShop: TextView = view.findViewById(R.id.tvShop)
        val tvSum: TextView = view.findViewById(R.id.tvSum)
        val tvItems: TextView = view.findViewById(R.id.tvItems)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_receipt, parent, false)
        return VH(view)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val r = items[position]
        holder.tvDate.text = r.created ?: "—"
        holder.tvShop.text = r.nameTc ?: r.title ?: "—"
        holder.tvSum.text = "Сумма: ${
            r.amountPromo?.let { String.format("%.2f ₽", it) } ?: "—"
        }"

       val products = r.items.joinToString("\n") { p ->
    val price = p.priceItem ?: p.pricePromo ?: p.priceRegular
    "• ${p.name ?: "?"} × ${p.quantity ?: "?"} = ${
        price?.let { String.format("%.2f ₽", it) } ?: "—"
    }"
}
        holder.tvItems.text = products.ifBlank { "Нет товаров" }

        // Открываем полный экран подробностей по нажатию на любую часть чека.
        holder.itemView.setOnClickListener {
            val context = holder.itemView.context
            context.startActivity(Intent(context, ReceiptDetailActivity::class.java).apply {
                putExtra(ReceiptDetailActivity.EXTRA_RECEIPT, r)
            })
        }
    }

    override fun getItemCount(): Int = items.size
}
