"""Live 2025 passing-score lookup via mandat.uzbmb.uz.

This module deliberately has no result cache: every call sends a fresh HTTP
request to the official endpoint, as the bot's UI promises current data.
"""
from __future__ import annotations

import re
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://mandat.uzbmb.uz"
ENDPOINT = f"{BASE_URL}/Bakalavr/BallInfoByResultJson"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MandatApplicantRatingBot/2025)",
    "Accept": "application/json, text/plain, */*",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}
TIMEOUT = (8, 25)

_RETRY = Retry(
    total=2,
    connect=2,
    read=2,
    backoff_factor=0.3,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET"}),
    respect_retry_after_header=True,
)


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=_RETRY)
    session.mount("https://", adapter)
    return session


def _number(value: Any) -> float | None:
    match = re.search(r"[0-9]+(?:[,.][0-9]+)?", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def find_live_directions(entrant_id: str) -> dict[str, Any]:
    """Fetch the entrant's current 2025 matching directions from the official site.

    No application cache is used. The endpoint is contacted on every invocation.
    """
    clean_id = re.sub(r"\D", "", str(entrant_id))
    if len(clean_id) != 7:
        return {"status": "validation_error", "message": "ID 7 xonali bo‘lishi kerak."}

    try:
        # A new GET is made for every function call. _ts also prevents an
        # intermediate HTTP cache from returning an older response.
        response = _session().get(
            ENDPOINT,
            params={"entrantId": clean_id, "_ts": str(time.time_ns())},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.Timeout:
        return {"status": "error", "message": "Rasmiy sayt javobi kechikdi. Birozdan so‘ng qayta urinib ko‘ring."}
    except requests.RequestException as exc:
        return {"status": "error", "message": f"Rasmiy saytga ulanishda xatolik: {exc}"}
    except ValueError:
        return {"status": "error", "message": "Rasmiy sayt JSON javob qaytarmadi."}

    if not payload.get("success"):
        return {"status": "not_found", "message": payload.get("message") or "Abituriyent topilmadi."}

    data = payload.get("data") or {}
    score = _number(data.get("result"))
    raw_details = data.get("details") or []
    details: list[dict[str, Any]] = []
    for row in raw_details:
        passing = _number(row.get("ballK"))
        details.append({
            "region": row.get("regionName") or "—",
            "university": row.get("universityName") or "—",
            "direction": row.get("facultyName") or "—",
            "education_form": row.get("educLanguage") or row.get("educLanguageId") or "—",
            "passing_score": row.get("ballK"),
            "is_match": score is not None and passing is not None and score >= passing,
        })

    return {
        "status": "success",
        "data": {
            "id": clean_id,
            "name": data.get("fullName") or "Noma’lum",
            "score": data.get("result"),
            "language": data.get("edlang") or "—",
            "details": details,
        },
    }
