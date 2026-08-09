#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TransferFeed.com Telegram Bot — Trading/Sniping Sürümü
----------------------------------------------------------------
transferfeed.com sitesindeki transfer haberlerini periyodik tarar ve
Telegram'a gönderir. Railway gibi bir bulut platformunda 7/24 çalışacak
şekilde tasarlanmıştır ve Telegram üzerinden komutla açılıp kapatılabilir:

    /baslat  -> taramayı başlatır (aktif hale getirir)
    /durdur  -> taramayı durdurur
    /durum   -> aktif mi, kaç haber biriktiğini gösterir

YENİ ÖZELLİKLER:
    - Kulüp isimleri artık mesaja dahil (logo alt-text'lerinden çıkarılıyor)
    - Kesin/Söylenti ayrımı (anahtar kelime bazlı, best-effort)
    - "Fırsat filtresi": büyük kulüpten küçük/bilinmeyen kulübe geçişleri
      🔥 ile öne çıkarır (senin Sorare senaryonda önemli olan haberler)

BOT_TOKEN ve CHAT_ID ortam değişkeni olarak veriliyor (Railway Variables).
"""

import json
import logging
import os
import re
import time
import unicodedata
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

CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "120"))
COMMAND_POLL_SECONDS = 5

STATE_FILE = Path(__file__).parent / "bot_state.json"
REQUEST_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("transferfeed_bot")

TRANSFER_LINK_RE = re.compile(r"^/transfers/([a-z0-9\-]+)/(\d+)$")
STORY_LINK_RE = re.compile(r"^/s/([a-z0-9\-]+)/(\d+)$")

# ----------------------------------------------------------------------------
# SORARE WATCHLIST: sadece bu liglerdeki kulüplerle ilgili transferler
# gönderilecek. 2026-27 sezonu güncel (yükselen/düşen takımlar dahil).
# Bu liste best-effort'tur — eksik/yanlış bir kulüp görürsen kolayca
# düzenleyebilirsin, sadece string ekleyip çıkarman yeterli.
# ----------------------------------------------------------------------------
WATCHLIST_CLUBS = [
    # --- İngiltere: Premier League (2026-27, 20 kulüp) ---
    "Arsenal", "Aston Villa", "Bournemouth", "Brentford",
    "Brighton & Hove Albion", "Chelsea", "Coventry City", "Crystal Palace",
    "Everton", "Fulham", "Hull City", "Ipswich Town", "Leeds United",
    "Liverpool", "Manchester City", "Manchester United", "Newcastle United",
    "Nottingham Forest", "Sunderland", "Tottenham Hotspur",
    # --- İngiltere: Championship (2026-27, 24 kulüp) ---
    "Birmingham City", "Blackburn Rovers", "Bolton Wanderers", "Bristol City",
    "Burnley", "Cardiff City", "Charlton Athletic", "Derby County",
    "Lincoln City", "Middlesbrough", "Millwall", "Norwich City", "Portsmouth",
    "Preston North End", "Queens Park Rangers", "Sheffield United",
    "Southampton", "Stoke City", "Swansea City", "Watford",
    "West Bromwich Albion", "West Ham United", "Wolverhampton Wanderers",
    "Wrexham",
    # --- İspanya: La Liga (2026-27, 20 kulüp) ---
    "FC Barcelona", "Barcelona", "Real Madrid", "Atlético Madrid",
    "Athletic Bilbao", "Athletic Club", "Villarreal", "Real Betis",
    "Celta Vigo", "Rayo Vallecano", "Real Sociedad", "Sevilla", "Getafe",
    "Osasuna", "Elche", "Espanyol", "Levante", "Alavés", "Valencia",
    "Racing Santander", "Deportivo La Coruña", "Málaga",
    # --- Almanya: Bundesliga (2026-27, 18 kulüp) ---
    "Bayern Munich", "FC Bayern München", "Bayer Leverkusen",
    "Bayer 04 Leverkusen", "RB Leipzig", "Eintracht Frankfurt",
    "Borussia Dortmund", "SC Freiburg", "Freiburg", "Mainz 05", "1. FSV Mainz 05",
    "Werder Bremen", "SV Werder Bremen", "Borussia Mönchengladbach",
    "VfB Stuttgart", "Union Berlin", "FC Union Berlin", "FC Augsburg",
    "Augsburg", "TSG Hoffenheim", "Hoffenheim", "FC Köln", "1. FC Köln",
    "Hamburger SV", "Schalke 04", "FC Schalke 04", "SV Elversberg",
    "SC Paderborn", "SC Paderborn 07",
    # --- Almanya: 2. Bundesliga (2026-27, best-effort) ---
    "Hertha BSC", "Arminia Bielefeld", "VfL Bochum", "Eintracht Braunschweig",
    "Energie Cottbus", "Darmstadt 98", "Dynamo Dresden", "Greuther Fürth",
    "SpVgg Greuther Fürth", "Hannover 96", "1. FC Heidenheim", "Heidenheim",
    "1. FC Kaiserslautern", "Kaiserslautern", "FC St. Pauli", "St. Pauli",
    "VfL Wolfsburg", "Wolfsburg", "VfL Osnabrück", "1. FC Nürnberg",
    "Nürnberg", "Karlsruher SC", "Hansa Rostock", "1. FC Magdeburg",
    # --- Fransa: Ligue 1 (2026-27, 18 kulüp) ---
    "Paris Saint Germain", "Paris Saint-Germain", "Marseille",
    "Olympique Marseille", "Monaco", "AS Monaco", "LOSC Lille", "Lille",
    "RC Lens", "Lens", "Lyon", "Olympique Lyonnais", "Rennes", "Stade Rennais",
    "Strasbourg", "Nice", "Toulouse", "Angers", "Auxerre", "Brest",
    "Le Havre", "Lorient", "Paris FC", "Troyes", "Le Mans",
    # --- Fransa: Ligue 2 (2026-27, best-effort) ---
    "Metz", "FC Metz", "Nantes", "FC Nantes", "Guingamp", "Red Star",
    "Annecy", "Nancy", "Dunkerque", "Boulogne", "Pau FC", "Rodez",
    "Laval", "Clermont", "Clermont Foot", "Saint-Étienne", "AS Saint-Étienne",
    "Montpellier", "Reims", "Stade de Reims", "Grenoble", "Caen",
    "Ajaccio", "AC Ajaccio", "Quevilly-Rouen",
    # --- Hollanda: Eredivisie (2026-27, 18 kulüp) ---
    "Ajax", "PSV", "PSV Eindhoven", "Feyenoord", "AZ", "AZ Alkmaar",
    "FC Twente", "FC Utrecht", "Go Ahead Eagles", "Sparta Rotterdam",
    "NEC", "NEC Nijmegen", "Fortuna Sittard", "SC Heerenveen", "Heerenveen",
    "PEC Zwolle", "FC Groningen", "Groningen", "Willem II", "RKC Waalwijk",
    "Excelsior", "ADO Den Haag", "Cambuur", "SC Cambuur",
    # --- Belçika: Pro League (2026-27, best-effort) ---
    "Club Brugge", "Union Saint-Gilloise", "Union SG", "Anderlecht",
    "Genk", "KRC Genk", "Standard Liège", "Standard de Liège", "Gent",
    "KAA Gent", "Royal Antwerp", "Antwerp", "Cercle Brugge", "OH Leuven",
    "Sint-Truiden", "STVV", "Westerlo", "Charleroi", "La Louvière",
    "Zulte Waregem", "Beveren", "Kortrijk", "Lommel",
    # --- Portekiz: Liga Portugal (2026-27, 18 kulüp) ---
    "Benfica", "Porto", "FC Porto", "Sporting CP", "Sporting Lisbon",
    "Braga", "SC Braga", "Vitória Guimarães", "Famalicão", "Moreirense",
    "Casa Pia", "Estoril", "Arouca", "Nacional", "Rio Ave", "Gil Vicente",
    "AVS", "Santa Clara", "Estrela Amadora", "Marítimo", "Académico de Viseu",
    # --- Japonya: J1 League (2026-27, best-effort) ---
    "Kashima Antlers", "Urawa Red Diamonds", "Urawa Reds",
    "Kawasaki Frontale", "Yokohama F. Marinos", "FC Tokyo", "Tokyo Verdy",
    "Kashiwa Reysol", "Machida Zelvia", "Sanfrecce Hiroshima", "Vissel Kobe",
    "Gamba Osaka", "Cerezo Osaka", "Avispa Fukuoka", "Nagoya Grampus",
    "Fagiano Okayama", "Shimizu S-Pulse", "Kyoto Sanga", "JEF United Chiba",
    "V-Varen Nagasaki", "Tochigi City",
    # --- ABD/Kanada: MLS (2026, 30 kulüp) ---
    "Atlanta United", "Austin FC", "CF Montréal", "Charlotte FC",
    "Chicago Fire", "FC Cincinnati", "Colorado Rapids", "Columbus Crew",
    "DC United", "D.C. United", "FC Dallas", "Houston Dynamo", "Inter Miami",
    "LA Galaxy", "Los Angeles FC", "LAFC", "Minnesota United", "Nashville SC",
    "New England Revolution", "New York City FC", "NYCFC",
    "New York Red Bulls", "Orlando City", "Philadelphia Union",
    "Portland Timbers", "Real Salt Lake", "San Diego FC",
    "San Jose Earthquakes", "Seattle Sounders", "Sporting Kansas City",
    "St. Louis City", "Toronto FC", "Vancouver Whitecaps",
    # --- Avusturya: Bundesliga (2026-27, 12 kulüp) ---
    "Sturm Graz", "SK Sturm Graz", "Red Bull Salzburg", "Salzburg",
    "Rapid Wien", "SK Rapid Wien", "LASK", "Austria Wien", "FK Austria Wien",
    "GAK", "Grazer AK", "Wolfsberger AC", "Wolfsberg", "TSV Hartberg",
    "Hartberg", "SCR Altach", "Altach", "SV Ried", "WSG Tirol",
    "Austria Lustenau",
    # --- İskoçya: Premiership (2026-27, 12 kulüp) ---
    "Aberdeen", "Celtic", "Dundee", "Dundee United", "Falkirk",
    "Heart of Midlothian", "Hearts", "Hibernian", "Kilmarnock", "Motherwell",
    "Rangers", "St Johnstone", "St Mirren",
]


def normalize_club(name: str) -> str:
    """Kulüp adını karşılaştırma için sadeleştirir: aksanları kaldırır,
    küçük harfe çevirir, alfanümerik olmayan karakterleri siler."""
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    ascii_str = ascii_str.lower()
    return re.sub(r"[^a-z0-9]", "", ascii_str)


WATCHLIST_NORMALIZED = {normalize_club(c) for c in WATCHLIST_CLUBS}


def is_watchlist_relevant(source_club: str, target_club: str) -> bool:
    return (
        normalize_club(source_club) in WATCHLIST_NORMALIZED
        or normalize_club(target_club) in WATCHLIST_NORMALIZED
    )


# ----------------------------------------------------------------------------
# KESİN / SÖYLENTİ SINIFLANDIRMA
# TransferFeed her transfer detay sayfasının en üstünde kendi etiketini koyuyor
# (örn. "TRANSFER RUMOUR", "TRANSFER NEWS", "OFFICIAL", "CONFIRMED"). Liste
# görünümünde bu etiket yok, bu yüzden yeni bir haber geldiğinde detay
# sayfasına ekstra bir istek atıp sitenin KENDİ etiketini okuyoruz — anahtar
# kelime tahmini yerine gerçek veri kullanmak çok daha güvenilir.
# ----------------------------------------------------------------------------
BADGE_PATTERN = re.compile(
    r"^(TRANSFER (RUMOUR|NEWS)|OFFICIAL(?: TRANSFER)?|CONFIRMED(?: TRANSFER)?|DONE DEAL)$",
    re.IGNORECASE,
)


def fetch_detail_badge(url: str) -> str | None:
    """Transfer detay sayfasını çekip sitenin kendi etiketini (varsa) döndürür."""
    html = fetch_page(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for s in soup.stripped_strings:
        s_clean = s.strip()
        if BADGE_PATTERN.match(s_clean):
            return s_clean.upper()
    return None


def classify_from_badge(badge: str | None) -> str:
    if not badge:
        return "unknown"
    b = badge.upper()
    if "OFFICIAL" in b or "CONFIRMED" in b or "DONE DEAL" in b:
        return "confirmed"
    if "RUMOUR" in b or "RUMOR" in b:
        return "rumour"
    if "NEWS" in b:
        return "news"
    return "unknown"


# ----------------------------------------------------------------------------
# FIRSAT FİLTRESİ: büyük kulüp -> küçük/bilinmeyen kulüp
# Bu liste tam değildir, gerektiğinde genişlet/düzenle.
# ----------------------------------------------------------------------------
BIG_CLUBS = {
    # Premier League
    "arsenal", "chelsea", "liverpool", "manchester city", "manchester united",
    "tottenham hotspur", "aston villa", "newcastle united", "brighton & hove albion",
    "west ham united",
    # La Liga
    "real madrid", "fc barcelona", "atlético madrid",
    # Bundesliga
    "fc bayern münchen", "borussia dortmund", "bayer 04 leverkusen",
    # Ligue 1
    "paris saint germain",
    # Serie A
    "juventus", "inter", "milan", "napoli", "roma",
    # Diğer büyük Avrupa kulüpleri
    "benfica", "porto", "sporting cp", "ajax", "psv", "feyenoord",
    "celtic", "rangers", "club brugge",
    # Türkiye
    "galatasaray", "fenerbahçe", "beşiktaş", "trabzonspor",
}


def is_opportunity(source_club: str, target_club: str) -> bool:
    if not source_club or not target_club:
        return False
    src = source_club.strip().lower()
    tgt = target_club.strip().lower()
    if tgt in ("unknown club", ""):
        return False
    return src in BIG_CLUBS and tgt not in BIG_CLUBS


# ----------------------------------------------------------------------------
# DURUM YÖNETİMİ
# ----------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("bot_state.json okunamadı, sıfırdan başlanıyor.")
    return {"running": True, "seen_ids": [], "last_update_id": 0}


def save_state(state: dict) -> None:
    trimmed = dict(state)
    trimmed["seen_ids"] = state["seen_ids"][-3000:]
    STATE_FILE.write_text(json.dumps(trimmed), encoding="utf-8")


# ----------------------------------------------------------------------------
# TELEGRAM YARDIMCI FONKSİYONLARI
# ----------------------------------------------------------------------------
def _mask(text: str) -> str:
    """Hata mesajlarında token'ın tam olarak loglara yazılmasını engeller."""
    if BOT_TOKEN:
        text = text.replace(BOT_TOKEN, "***MASKED***")
    return text


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
        log.error("Telegram mesajı gönderilemedi: %s", _mask(str(exc)))
        return False


def get_telegram_updates(offset: int) -> list:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": 0}
    try:
        resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("result", [])
    except requests.RequestException as exc:
        log.error("Telegram güncellemeleri alınamadı: %s", _mask(str(exc)))
        return []


def handle_commands(state: dict) -> dict:
    updates = get_telegram_updates(state["last_update_id"] + 1)
    for upd in updates:
        state["last_update_id"] = upd["update_id"]
        msg = upd.get("message", {})
        text = (msg.get("text") or "").strip().lower()
        chat_id = str(msg.get("chat", {}).get("id", ""))

        if CHAT_ID and chat_id != str(CHAT_ID):
            continue

        if text == "/durdur":
            state["running"] = False
            send_telegram_message("⏸ Tarama durduruldu. Tekrar başlatmak için /baslat yaz.")
        elif text == "/baslat":
            state["running"] = True
            send_telegram_message("▶️ Tarama başlatıldı.")
        elif text == "/durum":
            durum = "aktif ✅" if state["running"] else "durduruldu ⏸"
            send_telegram_message(
                f"Durum: {durum}\nBilinen haber sayısı: {len(state['seen_ids'])}\n"
                f"Kontrol aralığı: {CHECK_INTERVAL_SECONDS}s\n"
                f"İzlenen kulüp sayısı: {len(WATCHLIST_CLUBS)}"
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


def extract_full_text(a_tag) -> str:
    """Bağlantı içindeki hem düz metni hem de <img alt="..."> (kulüp logoları)
    metinlerini sırayla birleştirip okunabilir tam metni oluşturur."""
    parts = []
    for el in a_tag.descendants:
        if isinstance(el, str):
            s = el.strip()
            if s:
                parts.append(s)
        elif getattr(el, "name", None) == "img":
            alt = (el.get("alt") or "").strip()
            if alt:
                alt = re.sub(r"\s*logo$", "", alt, flags=re.IGNORECASE)
                parts.append(alt)
    return " ".join(parts)


def split_source_target(full_text: str) -> tuple[str, str]:
    """'PlayerName 5m ago SourceClub → TargetClub' formatından kulüpleri ayırır."""
    if "→" not in full_text:
        return "", ""
    before, after = full_text.split("→", 1)
    # 'before' içinde oyuncu adı + zaman + kulüp karışık; kulüp genelde son kelime öbeği.
    # Basit yaklaşım: 'ago' kelimesinden sonraki kısmı kaynak kulüp kabul et.
    source = before
    if " ago" in before:
        source = before.split(" ago", 1)[1]
    return source.strip(), after.strip()


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

        full_text = extract_full_text(a)
        if not full_text:
            continue

        source_club, target_club = split_source_target(full_text)

        items.append({
            "id": item_id,
            "text": full_text,
            "source_club": source_club,
            "target_club": target_club,
            "url": "https://www.transferfeed.com" + a["href"],
        })
    return items


def format_message(item: dict, kind: str, reliability: str) -> str:
    if reliability == "confirmed":
        rel_tag = "🔴 <b>KESİN</b>"
    elif reliability == "rumour":
        rel_tag = "🟡 Söylenti"
    elif reliability == "news":
        rel_tag = "🟠 Haber"
    else:
        rel_tag = "⚪️ Belirsiz"

    opportunity = is_opportunity(item["source_club"], item["target_club"])
    opp_tag = "\n🔥 <b>FIRSAT: büyük kulüpten küçük kulübe geçiş</b>" if opportunity else ""

    kind_emoji = "🔄" if kind == "transfer" else "📰"

    return (
        f"{kind_emoji} <b>{item['text']}</b>\n"
        f"{rel_tag}{opp_tag}\n"
        f"{item['url']}"
    )


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

            # Sadece izlenen liglerdeki kulüplerle ilgili transferleri
            # gönder (transfer öğeleri için). Story (haber makalesi)
            # öğeleri filtrelenmiyor, zaten sayıca az ve editoryel içerik.
            if kind == "transfer":
                if not is_watchlist_relevant(item["source_club"], item["target_club"]):
                    continue  # watchlist dışı, atla

                badge = fetch_detail_badge(item["url"])
                reliability = classify_from_badge(badge)
            else:
                reliability = "news"

            if send_telegram_message(format_message(item, kind, reliability)):
                log.info("Gönderildi: %s [%s]", item["text"], reliability)
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

    log.info("Bot başlatıldı. Durum: %s | Bilinen haber: %d | Kontrol aralığı: %ds",
              "aktif" if state["running"] else "durduruldu", len(state["seen_ids"]),
              CHECK_INTERVAL_SECONDS)

    if is_first_run:
        state = build_baseline(state)
        save_state(state)
        send_telegram_message(
            "🤖 Bot güncellendi ve başlatıldı.\n"
            "Artık kulüp isimleri, kesin/söylenti etiketi ve fırsat filtresi mesajlarda mevcut.\n"
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
