# OkHttp/Okio may contain optional references to platform APIs.
-dontwarn okhttp3.**
-dontwarn okio.**

# Receipt and Product are passed between Activities using Java Serializable.
# Preserve their fields so saved receipt data remains compatible after shrinking.
-keep class com.example.x5purchases.model.** implements java.io.Serializable { *; }
