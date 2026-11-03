package com.parsabe.alfred

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerConfigTest {

    @Test
    fun normalizesWhatPeopleType() {
        assertEquals("https://alfred.tail1234.ts.net/", ServerConfig.normalize("alfred.tail1234.ts.net"))
        assertEquals("https://alfred.example.com/", ServerConfig.normalize("  https://Alfred.Example.com/ "))
        assertEquals("http://192.168.1.5:8000/", ServerConfig.normalize("http://192.168.1.5:8000"))
        assertEquals("https://example.com/alfred/", ServerConfig.normalize("https://example.com/alfred/"))
        assertEquals("https://example.com/", ServerConfig.normalize("https://example.com:443"))
        assertEquals("http://localhost:8000/", ServerConfig.normalize("http://localhost:8000"))
    }

    @Test
    fun rejectsNonsense() {
        assertNull(ServerConfig.normalize(""))
        assertNull(ServerConfig.normalize("not an address"))
        assertNull(ServerConfig.normalize("ftp://example.com"))
        assertNull(ServerConfig.normalize("https://"))
        assertNull(ServerConfig.normalize("javascript:alert(1)"))
        assertNull(ServerConfig.normalize("alfred"))
    }

    @Test
    fun sameOriginOnlyForTheServer() {
        val base = "https://alfred.tail1234.ts.net/"
        assertTrue(ServerConfig.sameOrigin(base, "https://alfred.tail1234.ts.net/index.html"))
        assertTrue(ServerConfig.sameOrigin(base, "https://ALFRED.tail1234.ts.net:443/"))
        assertFalse(ServerConfig.sameOrigin(base, "http://alfred.tail1234.ts.net/"))
        assertFalse(ServerConfig.sameOrigin(base, "https://evil.example/"))
        assertFalse(ServerConfig.sameOrigin(base, "https://alfred.tail1234.ts.net.evil.example/"))
        assertFalse(ServerConfig.sameOrigin(base, "https://alfred.tail1234.ts.net:8443/"))
    }

    @Test
    fun knowsWhichAddressesCanUseTheMicrophone() {
        assertTrue(ServerConfig.isSecure("https://alfred.example.com/"))
        assertFalse(ServerConfig.isSecure("http://192.168.1.5:8000/"))
    }
}
