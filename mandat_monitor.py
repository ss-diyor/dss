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
from html import escape
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
MONITOR_STATUS_ENABLED = os.getenv("MANDAT_MONITOR_STATUS_ENABLED", "1").lower() not in {"0", "false", "no"}

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; MandatBotMonitor/1.0)",
    "Accept-Language": "uz,ru;q=0.9,en;q=0.8",
})


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _navigation_buttons(soup: BeautifulSoup) -> list[dict[str, str]]:
    """Bosh navigatsiya qatoridagi card-header tugmalarini universal ajratadi."""
    buttons = []
    for element in soup.select(".card-header.card-div, [id^='divbtn']"):
        element_id = str(element.get("id") or "").strip()
        classes = " ".join(sorted(str(element.get("class") or "").split()))
        text = _normalize(element.get_text(" ", strip=True))
        anchor = element.find("a", href=True)
        href = urljoin(MONITOR_URL, anchor.get("href", "").strip()) if anchor else ""
        onclick = _normalize(element.get("onclick") or "")
        item = {
            "id": element_id,
            "text": text,
            "href": href,
            "onclick": onclick,
            "classes": classes,
        }
        if item not in buttons:
            buttons.append(item)
    return sorted(buttons, key=lambda item: (item["text"], item["id"], item["href"]))


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
        "navigation_buttons": _navigation_buttons(soup),
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
        "navigation_buttons": "asosiy navigatsiya tugmalari",
        "extra_section_present": "Qo'shimcha bo'limi mavjudligi",
        "extra_links": "Qo'shimcha bo'limi nomlari yoki havolalari",
    }
    return [label for key, label in labels.items() if old.get(key) != new.get(key)]


def manual_check_report() -> str:
    """Saytni darhol tekshiradi; snapshotni o'zgartirmaydi."""
    current = fetch_snapshot()
    saved = _load_saved()
    changed = _changed_fields(saved, current) if saved else []
    title = escape(str(current.get("title") or "Noma'lum"))
    if saved is None:
        change_status = "⚪ <b>O'ZGARISH: SNAPSHOT HALI YARATILMAGAN</b>"
    elif changed:
        change_status = "❗ <b>O'ZGARISH ANIQLANDI!</b>"
    else:
        change_status = "✅ <b>O'ZGARISH ANIQLANMADI</b>"
    lines = [
        change_status,
        "🔎 <b>MANDAT MONITORING MANUAL CHECK</b>",
        f"Holat: <b>{'OK' if current.get('status_code') == 200 else 'XATO'}</b>",
        f"Sahifa: <code>{title}</code>",
        f"HTTP status: <code>{current.get('status_code')}</code>",
        f"Asosiy tugmalar: <b>{len(current.get('navigation_buttons') or [])}</b> ta",
        f"Qo'shimcha xizmatlar: <b>{len(current.get('extra_links') or [])}</b> ta",
    ]
    if saved is None:
        lines.append("Snapshot: hali yaratilmagan")
    elif changed:
        lines.append("Aniqlangan o'zgarishlar:")
        lines.extend(f"• {escape(item)}" for item in changed)
    else:
        lines.append("O'zgarish: aniqlanmadi")

    lines.append("\n<b>Asosiy tugmalar:</b>")
    for item in (current.get("navigation_buttons") or [])[:12]:
        destination = item.get("href") or item.get("onclick") or "ichki bo'lim"
        lines.append(f"• {escape(str(item.get('text') or ''))} — <code>{escape(str(destination))}</code>")
    lines.append("\n<b>Qo'shimcha bo'limi:</b>")
    for item in (current.get("extra_links") or [])[:10]:
        lines.append(f"• {escape(str(item.get('text') or ''))} — {escape(str(item.get('url') or ''))}")
    return "\n".join(lines)[:3900]


def _format_status_report(snapshot: dict[str, Any]) -> str:
    """Har bir muvaffaqiyatli tekshiruvdan keyingi qisqa admin statusi."""
    title = escape(str(snapshot.get("title") or "Noma'lum"))
    return (
        "✅ <b>MANDAT MONITORING HOLATI: OK</b>\n\n"
        f"Sahifa: <code>{title}</code>\n"
        f"HTTP status: <code>{snapshot.get('status_code')}</code>\n"
        f"Asosiy tugmalar: <b>{len(snapshot.get('navigation_buttons') or [])}</b> ta\n"
        f"Qo'shimcha xizmatlar: <b>{len(snapshot.get('extra_links') or [])}</b> ta\n"
        f"Tekshirilgan manzil: {escape(MONITOR_URL)}"
    )


def _format_error_alert(error: Exception) -> str:
    error_text = escape(str(error))[:1200]
    return (
        "❗ <b>SAYT MONITORING XATOSI!</b>\n\n"
        "Mandat saytini tekshirishda xatolik yuz berdi.\n"
        f"Xato: <code>{error_text}</code>\n"
        f"Tekshirilgan manzil: {escape(MONITOR_URL)}"
    )


def _diff_value(old_value: Any, new_value: Any) -> str:
    old_text = escape(_normalize(json.dumps(old_value, ensure_ascii=False) if isinstance(old_value, (dict, list)) else str(old_value or "—")))
    new_text = escape(_normalize(json.dumps(new_value, ensure_ascii=False) if isinstance(new_value, (dict, list)) else str(new_value or "—")))
    return f"<code>{old_text}</code> → <code>{new_text}</code>"


def _collection_diff(old_items: list[dict], new_items: list[dict], label: str) -> list[str]:
    old_map = {str(item.get("id") or item.get("url") or item.get("text")): item for item in old_items}
    new_map = {str(item.get("id") or item.get("url") or item.get("text")): item for item in new_items}
    lines: list[str] = []
    for key in sorted(set(old_map) | set(new_map)):
        old_item = old_map.get(key)
        new_item = new_map.get(key)
        if old_item is None:
            lines.append(f"• {label} qo'shildi: <b>{escape(str(new_item.get('text') or key))}</b>")
        elif new_item is None:
            lines.append(f"• {label} o'chirildi: <b>{escape(str(old_item.get('text') or key))}</b>")
        elif old_item != new_item:
            old_text = old_item.get("text") or old_item.get("url") or key
            new_text = new_item.get("text") or new_item.get("url") or key
            lines.append(f"• {label} o'zgardi: <code>{escape(str(old_text))}</code> → <code>{escape(str(new_text))}</code>")
            old_url = old_item.get("href") or old_item.get("url") or old_item.get("onclick")
            new_url = new_item.get("href") or new_item.get("url") or new_item.get("onclick")
            if old_url != new_url:
                lines.append(f"  Manzil: <code>{escape(str(old_url or '—'))}</code> → <code>{escape(str(new_url or '—'))}</code>")
    return lines


def _format_alert(old: dict[str, Any] | None, new: dict[str, Any], changed: list[str]) -> str:
    old = old or {}
    lines = [
        "❗ <b>O'ZGARISH ANIQLANDI!</b>",
        "🔔 <b>MANDAT SAYTI O'ZGARDI</b>",
        f"O'zgarishlar: <b>{', '.join(changed)}</b>",
        "\n<b>Aniq farqlar:</b>",
    ]
    scalar_fields = {
        "status_code": "HTTP status",
        "title": "Sahifa nomi",
        "forms": "Forma endpointlari",
        "important_ids": "Muhim DOM elementlari",
        "extra_section_present": "Qo'shimcha bo'limi",
    }
    for key, label in scalar_fields.items():
        if old.get(key) != new.get(key):
            lines.append(f"• {label}: {_diff_value(old.get(key), new.get(key))}")
    if "asosiy navigatsiya tugmalari" in changed:
        lines.extend(_collection_diff(old.get("navigation_buttons") or [], new.get("navigation_buttons") or [], "Navigatsiya tugmasi"))
    if "Qo'shimcha bo'limi nomlari yoki havolalari" in changed:
        lines.extend(_collection_diff(old.get("extra_links") or [], new.get("extra_links") or [], "Qo'shimcha xizmati"))
    lines.append(f"\nTekshirish: {escape(MONITOR_URL)}")
    return "\n".join(lines)[:3900]


async def monitor_site(bot: Any) -> None:
    """Bot ishlayotgan paytda saytni fonda kuzatadi."""
    old = _load_saved()
    last_alert_at = 0.0
    last_error_alert_at = 0.0
    while True:
        try:
            current = await asyncio.to_thread(fetch_snapshot)
            change_detected = False
            admin_id = int(os.getenv("ADMIN_ID", "0"))
            if old is None:
                _save(current)
                old = current
                print("[mandat-monitor] boshlang'ich snapshot saqlandi")
            elif current.get("fingerprint") != old.get("fingerprint"):
                change_detected = True
                changed = _changed_fields(old, current)
                now = time.monotonic()
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

            # O'zgarish bo'lmagan muvaffaqiyatli tekshiruvda status yuboriladi.
            # O'zgarish bo'lsa, shu siklda status emas, faqat yuqori ustuvor alert yuboriladi.
            if MONITOR_STATUS_ENABLED and not change_detected and admin_id > 0:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=_format_status_report(current),
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                except Exception as status_error:
                    print(f"[mandat-monitor] status xabari yuborilmadi: {status_error}")
        except Exception as error:
            print(f"[mandat-monitor] tekshiruv xatosi: {error}")
            admin_id = int(os.getenv("ADMIN_ID", "0"))
            now = time.monotonic()
            if admin_id > 0 and now - last_error_alert_at >= MONITOR_ALERT_COOLDOWN:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=_format_error_alert(error),
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                    )
                    last_error_alert_at = now
                except Exception as alert_error:
                    print(f"[mandat-monitor] xato alert yuborilmadi: {alert_error}")
        await asyncio.sleep(MONITOR_INTERVAL)
