"""mandat.uzbmb.uz monitoringi.

Bosh sahifaning muhim fingerprintlarini, jumladan Qo'shimcha bo'limidagi
nomlar va havolalarni tekshiradi. Birinchi tekshiruv faqat snapshot yaratadi;
keyingi haqiqiy o'zgarishlarda admin Telegram xabardor qilinadi.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

MONITOR_URL = os.getenv("MANDAT_MONITOR_URL", "https://mandat.uzbmb.uz/")
MONITOR_INTERVAL = max(60, int(os.getenv("MANDAT_MONITOR_INTERVAL", "300")))
MONITOR_TIMEOUT = max(5, int(os.getenv("MANDAT_MONITOR_TIMEOUT", "20")))
MONITOR_SNAPSHOT = Path(os.getenv("MANDAT_MONITOR_SNAPSHOT", "mandat_monitor_snapshot.json"))
MONITOR_ALERT_COOLDOWN = max(300, int(os.getenv("MANDAT_MONITOR_ALERT_COOLDOWN", "900")))

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; MandatBotMonitor/1.0)",
    "Accept-Language": "uz,ru;q=0.9,en;q=0.8",
})


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _extra_links(soup: BeautifulSoup) -> list[dict[str, str]]:
    root = soup.select_one("#collapseAdvancedSearch2")
    if root is None:
        # ID kelajakda o'zgarsa, mavjud xizmat URL'lari orqali fallback.
        root = soup

    known_paths = {
        "/BallVuzVakant2025", "/Imtiyoz2025", "/BallVuz2025",
        "/SAT2026", "/Bakalavr/BallInfoByResult", "/IqTest",
    }
    links: list[dict[str, str]] = []
    for anchor in root.select("a[href]"):
        href = urljoin(MONITOR_URL, anchor.get("href", "").strip())
        text = _normalize(anchor.get_text(" ", strip=True))
        path = href.split("?", 1)[0].rstrip("/")
        if text and (root is not soup or path in known_paths):
            links.append({"text": text, "url": href})
    return sorted(links, key=lambda item: (item["text"], item["url"]))


def collect_snapshot(html: str, status_code: int = 200) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    forms = sorted({
        (str(form.get("method", "get")).lower(), urljoin(MONITOR_URL, form.get("action", "")))
        for form in soup.select("form")
    })
    important_ids = [
        "divbtn1", "divbtn2", "divbtn3", "collapseSearchById",
        "AbiturID", "SearchBtn1", "collapseAdvancedSearch",
        "S4Subjects", "S5Subjects", "edLangs", "SearchBtn2",
        "collapseAdvancedSearch2", "textUrl",
    ]
    present_ids = sorted(item for item in important_ids if soup.find(id=item))
    extra = _extra_links(soup)
    payload: dict[str, Any] = {
        "status_code": int(status_code),
        "title": _normalize(soup.title.get_text(" ", strip=True) if soup.title else ""),
        "forms": [list(item) for item in forms],
        "important_ids": present_ids,
        "extra_section_present": bool(soup.find(id="divbtn3") or soup.find(id="collapseAdvancedSearch2")),
        "extra_links": extra,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["fingerprint"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def fetch_snapshot() -> dict[str, Any]:
    response = _SESSION.get(MONITOR_URL, timeout=MONITOR_TIMEOUT)
    return collect_snapshot(response.text, response.status_code)


def _load_saved() -> dict[str, Any] | None:
    try:
        return json.loads(MONITOR_SNAPSHOT.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _save(snapshot: dict[str, Any]) -> None:
    MONITOR_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    temp = MONITOR_SNAPSHOT.with_suffix(MONITOR_SNAPSHOT.suffix + ".tmp")
    temp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(MONITOR_SNAPSHOT)


def _changed_fields(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    labels = {
        "status_code": "HTTP status",
        "title": "sahifa nomi",
        "forms": "forma endpointlari",
        "important_ids": "asosiy DOM elementlari",
        "extra_section_present": "Qo'shimcha bo'limi mavjudligi",
        "extra_links": "Qo'shimcha bo'limi nomlari yoki havolalari",
    }
    return [label for key, label in labels.items() if old.get(key) != new.get(key)]


def _format_alert(old: dict[str, Any] | None, new: dict[str, Any], changed: list[str]) -> str:
    title = new.get("title") or "Noma'lum"
    lines = [
        "🔔 <b>Mandat sayti o'zgardi</b>",
        f"O'zgarishlar: <b>{', '.join(changed)}</b>",
        f"Sahifa: <code>{title}</code>",
        f"HTTP status: <code>{new.get('status_code')}</code>",
    ]
    if "Qo'shimcha bo'limi nomlari yoki havolalari" in changed:
        links = new.get("extra_links") or []
        lines.append(f"Qo'shimcha xizmatlar soni: <b>{len(links)}</b>")
        for item in links[:10]:
            lines.append(f"• {_normalize(item.get('text'))}: {item.get('url')}")
    lines.append(f"Tekshirish: {MONITOR_URL}")
    return "\n".join(lines)


async def monitor_site(bot: Any) -> None:
    """Bot ishlayotgan paytda saytni fonda kuzatadi."""
    old = _load_saved()
    last_alert_at = 0.0
    while True:
        try:
            current = await asyncio.to_thread(fetch_snapshot)
            if old is None:
                _save(current)
                old = current
                print("[mandat-monitor] boshlang'ich snapshot saqlandi")
            elif current.get("fingerprint") != old.get("fingerprint"):
                changed = _changed_fields(old, current)
                now = time.monotonic()
                admin_id = int(os.getenv("ADMIN_ID", "0"))
                if changed and admin_id > 0 and now - last_alert_at >= MONITOR_ALERT_COOLDOWN:
                    try:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=_format_alert(old, current, changed),
                            parse_mode="HTML",
                            disable_web_page_preview=True,
                        )
                        last_alert_at = now
                    except Exception as error:
                        print(f"[mandat-monitor] Telegram alert xatosi: {error}")
                _save(current)
                old = current
                print(f"[mandat-monitor] o'zgarish aniqlandi: {changed}")
        except Exception as error:
            print(f"[mandat-monitor] tekshiruv xatosi: {error}")
        await asyncio.sleep(MONITOR_INTERVAL)
