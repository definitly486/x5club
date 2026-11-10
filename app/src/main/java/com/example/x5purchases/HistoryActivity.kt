package com.example.x5purchases

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.webkit.CookieManager
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.example.x5purchases.adapter.ReceiptsAdapter
import com.example.x5purchases.model.Receipt
import com.example.x5purchases.network.X5Api
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class HistoryActivity : AppCompatActivity() {

    private lateinit var tvStatus: TextView
    private lateinit var recycler: RecyclerView
    private lateinit var spinnerPeriod: Spinner
    private lateinit var btnExportCsv: MaterialButton
    private lateinit var btnLogout: MaterialButton

    private var currentReceipts: List<Receipt> = emptyList()
    private var cookies: String = ""
    private var isLoading = false

    // Соответствует string-array period_labels
    private val periodDays = intArrayOf(7, 30, 60, 90, 180)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_history)

        tvStatus = findViewById(R.id.tvStatus)
        recycler = findViewById(R.id.recycler)
        spinnerPeriod = findViewById(R.id.spinnerPeriod)
        btnExportCsv = findViewById(R.id.btnExportCsv)
        btnLogout = findViewById(R.id.btnLogout)

        recycler.layoutManager = LinearLayoutManager(this)

        cookies = getSharedPreferences("x5", MODE_PRIVATE)
            .getString("cookies", null).orEmpty()

        if (cookies.isBlank()) {
            Toast.makeText(this, "Нет cookies. Войдите заново.", Toast.LENGTH_LONG).show()
            goToLogin()
            return
        }

        setupPeriodSpinner()
        setupButtons()

        // По умолчанию — 30 дней (индекс 1)
        spinnerPeriod.setSelection(1)
        loadHistory(periodDays[1])
    }

    private fun setupPeriodSpinner() {
        val labels = resources.getStringArray(R.array.period_labels)
        val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_item, labels)
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        spinnerPeriod.adapter = adapter

        spinnerPeriod.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(
                parent: AdapterView<*>?,
                view: View?,
                position: Int,
                id: Long
            ) {
                if (!isLoading) {
                    loadHistory(periodDays[position])
                }
            }

            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }
    }

    private fun setupButtons() {
        btnExportCsv.setOnClickListener {
            if (currentReceipts.isEmpty()) {
                Toast.makeText(this, R.string.export_empty, Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            exportCsv()
        }

        btnLogout.setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle("Выход")
                .setMessage("Очистить сессию и выйти?")
                .setPositiveButton("Выйти") { _, _ -> logout() }
                .setNegativeButton("Отмена", null)
                .show()
        }
    }

    private fun loadHistory(days: Int) {
        if (isLoading) return
        isLoading = true

        btnExportCsv.isEnabled = false
        currentReceipts = emptyList()
        recycler.adapter = null
        tvStatus.text = "Загружаю историю за $days дн…"

        lifecycleScope.launch {
            try {
                val receipts = withContext(Dispatchers.IO) {
                    X5Api.fetchReceipts(cookies, days = days)
                }
                currentReceipts = receipts

                if (receipts.isEmpty()) {
                    tvStatus.text = "Чеков не найдено за $days дн."
                } else {
                    val total = receipts.sumOf { it.amountPromo ?: 0.0 }
                    val itemsCount = receipts.sumOf { it.items.size }
                    tvStatus.text = "Чеков: ${receipts.size}  |  Товаров: $itemsCount  |  Сумма: %.2f ₽"
                        .format(total)
                    recycler.adapter = ReceiptsAdapter(receipts)
                    btnExportCsv.isEnabled = true
                }
            } catch (e: Exception) {
                tvStatus.text = "Ошибка: ${e.message}"
                Toast.makeText(this@HistoryActivity, e.message, Toast.LENGTH_LONG).show()
            } finally {
                isLoading = false
            }
        }
    }

    private fun exportCsv() {
        lifecycleScope.launch {
            try {
                val path = withContext(Dispatchers.IO) {
                    CsvExporter.export(this@HistoryActivity, currentReceipts)
                }
                Toast.makeText(
                    this@HistoryActivity,
                    getString(R.string.export_success, path),
                    Toast.LENGTH_LONG
                ).show()
            } catch (e: Exception) {
                Toast.makeText(
                    this@HistoryActivity,
                    getString(R.string.export_error, e.message),
                    Toast.LENGTH_LONG
                ).show()
            }
        }
    }

    private fun logout() {
        // Очищаем cookies приложения
        getSharedPreferences("x5", MODE_PRIVATE).edit().clear().apply()

        // Очищаем cookies WebView
        CookieManager.getInstance().apply {
            removeAllCookies(null)
            flush()
        }

        goToLogin()
    }

    private fun goToLogin() {
        startActivity(Intent(this, LoginActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        })
        finish()
    }
}
