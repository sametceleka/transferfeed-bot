#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TransferFeed.com Telegram Bot — Bulut Sürümü (kontrol komutlu)
----------------------------------------------------------------
transferfeed.com sitesindeki transfer haberlerini periyodik tarar ve
Telegram'a gönderir. Railway gibi bir bulut platformunda 7/24 çalışacak
şekilde tasarlanmıştır ve Telegram üzerinden komutla açılıp kapatılabilir:

    /baslat  -> taramayı başlatır (aktif hale getirir)
    /durdur  -> taramayı durdurur (bot çalışmaya devam eder ama site taramaz)
    /durum   -> şu an aktif mi değil mi, kaç haber biriktiğini gösterir

BOT_TOKEN ve CHAT_ID artık kod içine yazılmıyor; ortam değişkeni (environment
variable) olarak veriliyor. Bu hem güvenlik için hem de Railway'de kolayca
değiştirebilmen için.

Yerelde test etmek istersen (Railway'e atmadan önce), terminalde:
    export BOT_TOKEN="123456:AA..."
    export CHAT_ID="123456789"
    python transferfeed_bot.py
"""

import json
import logging
import os
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# AYARLAR
# ----------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

SOURCE_URLS = [
    "https://www.transferfeed.com/",
    "https://www.transferfeed.com/rumours",
]

CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "300"))
# Telegram komutlarını ne sıklıkla kontrol edeceğiz (haber taramasından bağımsız,
# daha sık olmalı ki /durdur yazınca hemen tepki versin)
COMMAND_POLL_SECONDS = 5

STATE_FILE = Path(__file__).parent / "bot_state.json"
REQUEST_TIMEOUT = 15
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("transferfeed_bot")

TRANSFER_LINK_RE = re.compile(r"^/transfers/([a-z0-9\-]+)/(\d+)$")
STORY_LINK_RE = re.compile(r"^/s/([a-z0-9\-]+)/(\d+)$")


# ----------------------------------------------------------------------------
# DURUM YÖNETİMİ (running flag + seen_ids + telegram update_id tek dosyada)
# ----------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("bot_state.json okunamadı, sıfırdan başlanıyor.")
    return {"running": True, "seen_ids": [], "last_update_id": 0}


def save_state(state: dict) -> None:
    # seen_ids'in sonsuza kadar büyümesini önlemek için son 3000 kaydı tut
    trimmed = dict(state)
    trimmed["seen_ids"] = state["seen_ids"][-3000:]
    STATE_FILE.write_text(json.dumps(trimmed), encoding="utf-8")


# ----------------------------------------------------------------------------
# TELEGRAM YARDIMCI FONKSİYONLARI
# ----------------------------------------------------------------------------
def send_telegram_message(text: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, data=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        log.error("Telegram mesajı gönderilemedi: %s", exc)
        return False


def get_telegram_updates(offset: int) -> list:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": 0}
    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("result", [])
    except requests.RequestException as exc:
        log.error("Telegram güncellemeleri alınamadı: %s", exc)
        return []


def handle_commands(state: dict) -> dict:
    updates = get_telegram_updates(state["last_update_id"] + 1)
    for upd in updates:
        state["last_update_id"] = upd["update_id"]
        msg = upd.get("message", {})
        text = (msg.get("text") or "").strip().lower()
        chat_id = str(msg.get("chat", {}).get("id", ""))

        # Güvenlik: sadece senin CHAT_ID'nden gelen komutları kabul et
        if CHAT_ID and chat_id != str(CHAT_ID):
            continue

        if text == "/durdur":
            state["running"] = False
            send_telegram_message("⏸ Tarama durduruldu. Tekrar başlatmak için /baslat yaz.")
            log.info("Kullanıcı /durdur komutu gönderdi.")
        elif text == "/baslat":
            state["running"] = True
            send_telegram_message("▶️ Tarama başlatıldı.")
            log.info("Kullanıcı /baslat komutu gönderdi.")
        elif text == "/durum":
            durum = "aktif ✅" if state["running"] else "durduruldu ⏸"
            send_telegram_message(
                f"Durum: {durum}\nBilinen haber sayısı: {len(state['seen_ids'])}"
            )
    return state


# ----------------------------------------------------------------------------
# SITE TARAMA
# ----------------------------------------------------------------------------
def fetch_page(url: str) -> str | None:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        log.error("Sayfa alınamadı (%s): %s", url, exc)
        return None


def clean_text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split())


def parse_items(html: str, pattern: re.Pattern, prefix: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen_in_page = set()
    for a in soup.find_all("a", href=True):
        m = pattern.match(a["href"])
        if not m:
            continue
        item_id = f"{prefix}-{m.group(2)}"
        if item_id in seen_in_page:
            continue
        seen_in_page.add(item_id)
        text = clean_text(a)
        if not text:
            continue
        items.append({"id": item_id, "text": text, "url": "https://www.transferfeed.com" + a["href"]})
    return items


def format_message(item: dict, kind: str) -> str:
    emoji = "🔄" if kind == "transfer" else "📰"
    return f"{emoji} <b>{item['text']}</b>\n{item['url']}"


def check_transfers(state: dict) -> dict:
    seen_ids = set(state["seen_ids"])
    new_count = 0

    for url in SOURCE_URLS:
        html = fetch_page(url)
        if not html:
            continue

        combined = [(i, "transfer") for i in parse_items(html, TRANSFER_LINK_RE, "transfer")]
        combined += [(i, "story") for i in parse_items(html, STORY_LINK_RE, "story")]

        for item, kind in combined:
            if item["id"] in seen_ids:
                continue
            seen_ids.add(item["id"])
            if send_telegram_message(format_message(item, kind)):
                log.info("Gönderildi: %s", item["text"])
                new_count += 1
                time.sleep(1)

    state["seen_ids"] = list(seen_ids)
    log.info("Yeni haber yok." if new_count == 0 else f"{new_count} yeni haber gönderildi.")
    return state


def build_baseline(state: dict) -> dict:
    log.info("İlk çalıştırma: mevcut haberler baseline olarak kaydediliyor.")
    seen_ids = set(state["seen_ids"])
    for url in SOURCE_URLS:
        html = fetch_page(url)
        if html:
            for i in parse_items(html, TRANSFER_LINK_RE, "transfer"):
                seen_ids.add(i["id"])
            for i in parse_items(html, STORY_LINK_RE, "story"):
                seen_ids.add(i["id"])
    state["seen_ids"] = list(seen_ids)
    return state


# ----------------------------------------------------------------------------
# ANA DÖNGÜ
# ----------------------------------------------------------------------------
def main() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        log.error("BOT_TOKEN ve CHAT_ID ortam değişkenleri tanımlı değil. "
                   "Railway'de Variables sekmesinden ekle.")
        return

    state = load_state()
    is_first_run = not state["seen_ids"]

    log.info("Bot başlatıldı. Durum: %s | Bilinen haber: %d",
              "aktif" if state["running"] else "durduruldu", len(state["seen_ids"]))

    if is_first_run:
        state = build_baseline(state)
        save_state(state)
        send_telegram_message(
            "🤖 Bot başlatıldı. Bundan sonraki yeni transfer haberlerini bildireceğim.\n"
            "Komutlar: /durdur /baslat /durum"
        )

    last_check = 0
    while True:
        try:
            state = handle_commands(state)

            if state["running"] and (time.time() - last_check) >= CHECK_INTERVAL_SECONDS:
                state = check_transfers(state)
                last_check = time.time()
                save_state(state)

        except Exception as exc:
            log.exception("Beklenmeyen hata: %s", exc)

        time.sleep(COMMAND_POLL_SECONDS)


if __name__ == "__main__":
    main()
