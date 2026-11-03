package com.parsabe.alfred

import java.net.URI

/**
 * The Alfred server address: parsing what the user typed, and deciding which pages belong to it.
 * Pure Kotlin (java.net.URI), so it is unit-tested on the JVM.
 */
object ServerConfig {

    /**
     * Turns "alfred.my-tailnet.ts.net", "https://alfred.example.com/", "http://192.168.1.5:8000"
     * into a clean base URL ending in "/", or null if it isn't a usable address.
     */
    fun normalize(input: String): String? {
        var text = input.trim()
        if (text.isEmpty() || text.any { it.isWhitespace() }) return null
        if (!text.contains("://")) text = "https://$text"
        val uri = try {
            URI(text)
        } catch (e: Exception) {
            return null
        }
        val scheme = uri.scheme?.lowercase() ?: return null
        if (scheme != "https" && scheme != "http") return null
        val host = uri.host?.lowercase() ?: return null
        if (host.isEmpty() || (!host.contains('.') && host != "localhost" && !host.contains(':'))) {
            return null
        }
        val port = if (uri.port == -1 || uri.port == defaultPort(scheme)) "" else ":${uri.port}"
        val path = (uri.rawPath ?: "").trimEnd('/')
        return "$scheme://$host$port$path/"
    }

    fun isSecure(base: String): Boolean = base.startsWith("https://")

    /** True if [url] is a page of the Alfred server at [base] (same scheme, host and port). */
    fun sameOrigin(base: String, url: String): Boolean {
        val a = try { URI(base) } catch (e: Exception) { return false }
        val b = try { URI(url) } catch (e: Exception) { return false }
        val schemeA = a.scheme?.lowercase() ?: return false
        val schemeB = b.scheme?.lowercase() ?: return false
        return schemeA == schemeB &&
            a.host.equals(b.host, ignoreCase = true) &&
            effectivePort(a) == effectivePort(b)
    }

    private fun defaultPort(scheme: String) = if (scheme == "https") 443 else 80

    private fun effectivePort(uri: URI) =
        if (uri.port == -1) defaultPort(uri.scheme.lowercase()) else uri.port
}
