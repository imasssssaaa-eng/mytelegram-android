#!/usr/bin/env python3
"""
mytg.py — вживляет локальный (без внешних серверов) auth-стаб
в исходники mytelegram-android.

Использование:
    python3 mytg.py /путь/до/mytelegram-android

Что делает:
  1. Создаёт LocalAuthService.kt (сервис на 127.0.0.1:8080 + SQLite)
  2. Добавляет запуск сервиса в Application.onCreate()
  3. Патчит вызов auth.sendCode/auth.signIn на localhost-запрос
     (нужен якорь — см. ANCHOR_* ниже, поправь под свой файл)

Скрипт идемпотентен — повторный запуск не сломает уже пропатченное.
"""

import sys
import re
from pathlib import Path

SERVICE_CODE = '''package org.telegram.messenger

import android.app.Service
import android.content.Intent
import android.os.IBinder
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.PrintWriter
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import kotlin.concurrent.thread

class LocalAuthService : Service() {
    private lateinit var serverSocket: ServerSocket
    private val users = mutableMapOf<String, Long>()
    private var nextId = 1000L

    override fun onCreate() {
        super.onCreate()
        thread {
            serverSocket = ServerSocket(8080, 50, InetAddress.getByName("127.0.0.1"))
            while (true) {
                val client = serverSocket.accept()
                thread { handle(client) }
            }
        }
    }

    private fun handle(socket: Socket) {
        try {
            val input = BufferedReader(InputStreamReader(socket.getInputStream()))
            val output = PrintWriter(socket.getOutputStream(), true)
            val line = input.readLine() ?: return
            val req = JSONObject(line)

            when (req.optString("method")) {
                "auth.sendCode" -> {
                    output.println(JSONObject().put("phone_code_hash", "local").toString())
                }
                "auth.signIn" -> {
                    val resp = JSONObject()
                    if (req.optString("code") == "22222") {
                        val phone = req.optString("phone")
                        val id = users.getOrPut(phone) { nextId++ }
                        resp.put("ok", true).put("user_id", id)
                    } else {
                        resp.put("ok", false)
                    }
                    output.println(resp.toString())
                }
                else -> output.println(JSONObject().put("ok", false).toString())
            }
            socket.close()
        } catch (e: Exception) {
            // локальный сокет, ошибки не критичны — просто закрываем соединение
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
'''

ANCHOR_APP_ONCREATE = "override fun onCreate() {"
APP_START_SERVICE = "        startService(Intent(this, LocalAuthService::class.java))\n"


def find_application_file(root: Path) -> Path | None:
    """Ищет файл с классом, унаследованным от android.app.Application"""
    for f in root.rglob("*.kt"):
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        if ": Application(" in text or ": Application()" in text:
            return f
    for f in root.rglob("*.java"):
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        if "extends Application" in text:
            return f
    return None


def write_service(root: Path):
    # кладём рядом с остальными классами messenger-пакета
    target_dir = None
    for f in root.rglob("ConnectionsManager.*"):
        target_dir = f.parent
        break
    if target_dir is None:
        target_dir = root / "app" / "src" / "main" / "java" / "org" / "telegram" / "messenger"
        target_dir.mkdir(parents=True, exist_ok=True)

    svc_path = target_dir / "LocalAuthService.kt"
    svc_path.write_text(SERVICE_CODE)
    print(f"[+] Сервис записан: {svc_path}")


def patch_application(app_file: Path):
    text = app_file.read_text()

    if "LocalAuthService" in text:
        print(f"[=] {app_file} уже пропатчен, пропускаю")
        return

    if ANCHOR_APP_ONCREATE not in text:
        print(f"[!] Не нашёл 'override fun onCreate()' в {app_file}")
        print("    Добавь вручную: startService(Intent(this, LocalAuthService::class.java))")
        return

    text = text.replace(
        ANCHOR_APP_ONCREATE,
        ANCHOR_APP_ONCREATE + "\n" + APP_START_SERVICE,
        1,
    )

    if "import android.content.Intent" not in text:
        text = text.replace("package org.telegram.messenger",
                             "package org.telegram.messenger\n\nimport android.content.Intent", 1)

    app_file.write_text(text)
    print(f"[+] Пропатчен: {app_file}")


def main():
    if len(sys.argv) != 2:
        print("Использование: python3 mytg.py /путь/до/mytelegram-android")
        sys.exit(1)

    root = Path(sys.argv[1]).resolve()
    if not root.exists():
        print(f"[!] Путь не найден: {root}")
        sys.exit(1)

    write_service(root)

    app_file = find_application_file(root)
    if app_file:
        patch_application(app_file)
    else:
        print("[!] Не нашёл класс Application автоматически.")
        print("    Найди вручную (обычно ApplicationLoader.kt/java) и добавь:")
        print("    " + APP_START_SERVICE.strip())

    print("""
[i] СЛЕДУЮЩИЙ ШАГ — самый важный, скрипт его НЕ делает автоматически:
    Нужно найти в клиенте место, где вызывается реальный auth.sendCode /
    auth.signIn (обычно в ConnectionsManager.java или SendMessagesHelper),
    и заменить сетевой вызов на запрос к 127.0.0.1:8080.

    Пришли мне точные строки из этого метода (грепни):
        grep -rn "auth.sendCode\\|sendRequest.*auth" --include=*.java --include=*.kt .

    Без этого сервис поднимется, но клиент всё ещё будет стучаться
    на старый IP, а не на локальный сервис.
""")


if __name__ == "__main__":
    main()
                               
