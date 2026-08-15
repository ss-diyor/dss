import os
import re
import time
import threading
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import urlencode
from bs4 import BeautifulSoup

BASE_URL  = "https://mandat.uzbmb.uz"
RESULT_PATH = os.getenv("MANDAT_RESULT_PATH", "/Mandat2025/MainSearch")
HEADERS   = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
CACHE_TTL = 1800  # 30 daqiqa

# Har bir worker thread uchun connection pool'li Session.
# requests.Session bir nechta thread orasida bo'lishilmaydi, shuning uchun thread-local saqlanadi.
_session_local = threading.local()
_retry = Retry(
    total=2,
    connect=2,
    read=2,
    backoff_factor=0.25,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET"}),
    respect_retry_after_header=True,
)
REQUEST_TIMEOUT = (4, 15)


def _get_session() -> requests.Session:
    session = getattr(_session_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=_retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _session_local.session = session
    return session

# total_count cache: {(s4, s5, edlang): (total, timestamp)}
_total_cache: dict = {}


def clean_id(text):
    return re.sub(r'\D', '', text)


def _paginate_params(page_number, page_size, s4subject, s5subject, ed_lang_id):
    p = {'pageNumber': page_number, 'pageSize': page_size, 'lang': 'uz'}
    if s4subject:  p['s4subject'] = s4subject
    if s5subject:  p['s5subject'] = s5subject
    if ed_lang_id: p['edLangId']  = ed_lang_id
    return p


def _page_has_content(page_num, page_size, s4subject, s5subject, ed_lang_id):
    try:
        params = _paginate_params(page_num, page_size, s4subject, s5subject, ed_lang_id)
        resp = _get_session().get(f"{BASE_URL}/Bakalavr/Paginate", params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return False
        soup = BeautifulSoup(resp.text, 'html.parser')
        return bool(soup.find('div', class_='m3-rescard'))
    except Exception:
        return False


def _find_true_last_page(current_page, page_size, s4subject, s5subject, ed_lang_id):
    def has(n):
        return _page_has_content(n, page_size, s4subject, s5subject, ed_lang_id)

    low, step = current_page, 1
    high = current_page + step
    while has(high):
        low   = high
        step *= 2
        high  = low + step
        if high > 500_000:
            return current_page

    while low < high - 1:
        mid = (low + high) // 2
        if has(mid):
            low = mid
        else:
            high = mid
    return low


def _count_cards_on_page(page_num, page_size, s4subject, s5subject, ed_lang_id):
    try:
        params = _paginate_params(page_num, page_size, s4subject, s5subject, ed_lang_id)
        resp = _get_session().get(f"{BASE_URL}/Bakalavr/Paginate", params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return page_size
        soup  = BeautifulSoup(resp.text, 'html.parser')
        count = len(soup.find_all('div', class_='m3-rescard'))
        return count if count > 0 else page_size
    except Exception:
        return page_size


def get_total_count(page_number, page_size, s4subject, s5subject, ed_lang_id):
    """
    Jami abituriyentlar sonini qaytaradi.
    Natija 30 daqiqa cache da saqlanadi — bir yo'nalish bir marta hisoblanadi.
    """
    cache_key = (s4subject, s5subject, ed_lang_id)
    now       = time.time()

    # Cache da bor va muddati o'tmagan bo'lsa
    if cache_key in _total_cache:
        total, ts = _total_cache[cache_key]
        if now - ts < CACHE_TTL:
            return total

    # Yangi hisoblash
    try:
        last_page  = _find_true_last_page(page_number, page_size, s4subject, s5subject, ed_lang_id)
        cards_last = _count_cards_on_page(last_page, page_size, s4subject, s5subject, ed_lang_id)
        total      = (last_page - 1) * page_size + cards_last
        _total_cache[cache_key] = (total, now)
        return total
    except Exception:
        return None


def _text(element) -> str:
    return " ".join(element.get_text(" ", strip=True).split()) if element else ""


def _number(value: str):
    match = re.search(r"[0-9]+(?:[,.][0-9]+)?", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _parse_mandat_table(soup, entrant_id, page_url, hero_status, result_status, score):
    table = soup.select_one("table.m3-details-table") or soup.select_one("table")
    if not table:
        return None
    choices = []
    for row in table.select("tr"):
        cells = [_text(cell) for cell in row.select("th, td")]
        if len(cells) < 7 or cells[0].lower().startswith("yo‘nalish"):
            continue
        choices.append({
            "priority": cells[0],
            "university": cells[1],
            "direction": cells[2],
            "education_form": cells[3],
            "code": cells[4],
            "grant_score": cells[5],
            "contract_score": cells[6],
            "status": "not_selected",
            "status_text": "",
        })
    if not choices:
        return None

    accepted_choice = None
    candidate_score = _number(score)
    threshold_key = "grant_score" if result_status == "grant" else "contract_score"
    if result_status in {"grant", "contract"} and candidate_score is not None:
        for choice in choices:
            threshold = _number(choice.get(threshold_key))
            if threshold is not None and candidate_score >= threshold:
                accepted_choice = choice
                choice["status"] = result_status
                choice["status_text"] = "Davlat granti" if result_status == "grant" else "To‘lov shartnoma"
                break

    return {
        "status": "success",
        "data": {
            "name": _text(soup.select_one(".m3-hero__name")) or "Noma'lum",
            "id": clean_id(str(entrant_id)),
            "score": score or "Noma'lum",
            "pass_status": hero_status or ("Grant" if result_status == "grant" else "Kontrakt" if result_status == "contract" else "Tavsiya etilmadi"),
            "result_status": result_status,
            "is_pass": result_status in {"grant", "contract", "accepted"},
            "accepted_choice": accepted_choice,
            "choices": choices,
            "page_link": page_url,
        },
    }


def _parse_mandat2025_details(soup, entrant_id, page_url):
    hero = soup.select_one(".m3-hero")
    if not hero:
        return None
    cards = soup.select(".m3-tl-card")

    hero_status = _text(soup.select_one(".m3-hero__status"))
    hero_classes = set(hero.get("class") or [])
    status_lower = hero_status.lower()
    if "grant" in hero_classes or "grant" in status_lower or "grant" in " ".join(hero_classes):
        result_status = "grant"
    elif "contract" in hero_classes or "kontrakt" in status_lower or "contract" in status_lower or "contract" in " ".join(hero_classes):
        result_status = "contract"
    elif "qabul" in status_lower or "tavsiya" in status_lower:
        result_status = "accepted"
    else:
        result_status = "not_recommended"

    choices = []
    accepted_choice = None
    for card in cards:
        meta = [_text(item) for item in card.select(".m3-tl-meta")]
        priority = meta[0] if meta else ""
        education_form = meta[1] if len(meta) > 1 else ""
        code = meta[2].replace("Shifri:", "").strip() if len(meta) > 2 else ""
        stats = {}
        for stat in card.select(".m3-tl-stat"):
            label = _text(stat.select_one(".m3-tl-stat-lb"))
            value = _text(stat.select_one(".m3-tl-stat-val"))
            if label:
                stats[label] = value
        tag = card.select_one(".m3-tl-tag")
        tag_text = _text(tag)
        tag_classes = set(tag.get("class") or []) if tag else set()
        if tag:
            if "grant" in tag_classes or "grant" in tag_text.lower():
                choice_status = "grant"
            elif "contract" in tag_classes or "kontrakt" in tag_text.lower():
                choice_status = "contract"
            else:
                choice_status = "accepted"
        else:
            choice_status = "not_selected"
        choice = {
            "priority": priority,
            "university": _text(card.select_one(".m3-tl-sub")),
            "direction": _text(card.select_one(".m3-tl-title")),
            "education_form": education_form,
            "code": code,
            "grant_score": stats.get("Davlat granti", "—"),
            "contract_score": stats.get("To‘lov shartnoma", stats.get("To'lov shartnoma", "—")),
            "status": choice_status,
            "status_text": tag_text,
        }
        choices.append(choice)
        if tag and accepted_choice is None:
            accepted_choice = choice

    hero_score = _text(soup.select_one(".m3-hero__score"))
    score_match = re.search(r"[0-9]+(?:[,.][0-9]+)?", hero_score)
    score = score_match.group(0) if score_match else hero_score
    name = _text(soup.select_one(".m3-hero__name")) or "Noma'lum"
    if accepted_choice:
        final_status = accepted_choice["status"] if accepted_choice["status"] in {"grant", "contract"} else result_status
    else:
        final_status = result_status

    if not cards:
        return _parse_mandat_table(soup, entrant_id, page_url, hero_status, result_status, score)

    return {
        "status": "success",
        "data": {
            "name": name,
            "id": clean_id(str(entrant_id)),
            "score": score or "Noma'lum",
            "pass_status": hero_status or ("Grant" if final_status == "grant" else "Kontrakt" if final_status == "contract" else "Tavsiya etilmadi"),
            "result_status": final_status,
            "is_pass": final_status in {"grant", "contract", "accepted"},
            "accepted_choice": accepted_choice,
            "choices": choices,
            "page_link": page_url,
        },
    }


def get_student_data(entrant_id):
    url    = f"{BASE_URL}{RESULT_PATH}"
    params = {"entrantid": entrant_id, "lang": "uz"}

    try:
        response = _get_session().get(url, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            return {"status": "error", "message": f"Saytga bog'lanib bo'lmadi: {response.status_code}"}

        soup      = BeautifulSoup(response.text, 'html.parser')
        modern_result = _parse_mandat2025_details(soup, entrant_id, response.url)
        if modern_result is not None:
            return modern_result

        all_cards = soup.find_all('div', class_='m3-rescard')

        if not all_cards:
            return {"status": "not_found", "message": "Abituriyent topilmadi yoki ball 56.7 dan past"}

        page_number, page_size = None, 10
        active_item = soup.find('li', class_='page-item active')
        if active_item:
            p_inp = active_item.find('input', {'name': 'pageNumber'})
            s_inp = active_item.find('input', {'name': 'pageSize'})
            if p_inp: page_number = int(p_inp['value'])
            if s_inp: page_size   = int(s_inp['value'])

        s4subject, s5subject, ed_lang_id = None, None, None
        first_form = soup.find('form', action='/Bakalavr/Paginate')
        if first_form:
            s4 = first_form.find('input', {'name': 's4subject'})
            s5 = first_form.find('input', {'name': 's5subject'})
            ed = first_form.find('input', {'name': 'edLangId'})
            if s4: s4subject  = s4['value']
            if s5: s5subject  = s5['value']
            if ed: ed_lang_id = ed['value']

        page_link = None
        if page_number:
            page_link = (
                f"{BASE_URL}/Bakalavr/Paginate?"
                + urlencode(_paginate_params(page_number, page_size, s4subject, s5subject, ed_lang_id))
            )

        target_id = clean_id(str(entrant_id))

        for idx, card in enumerate(all_cards):
            id_tag = card.find('div', class_='m3-rescard__id')
            if not id_tag:
                continue
            if clean_id(id_tag.text) != target_id:
                continue

            rank = (page_number - 1) * page_size + (idx + 1) if page_number else None

            name_tag  = card.find('div', class_='m3-rescard__name')
            name      = name_tag.get_text(strip=True) if name_tag else "Noma'lum"
            score_tag = card.find('span', class_='m3-score-val')
            score     = score_tag.text.strip() if score_tag else "Noma'lum"
            is_pass   = 'm3-rescard--pass' in card.get('class', [])

            return {
                "status": "success",
                "data": {
                    "name":        name,
                    "id":          target_id,
                    "score":       score,
                    "pass_status": "O'tdi" if is_pass else "O'tmadi",
                    "is_pass":     is_pass,
                    "rank":        rank,
                    "page_number": page_number,
                    "page_size":   page_size,
                    "s4subject":   s4subject,
                    "s5subject":   s5subject,
                    "ed_lang_id":  ed_lang_id,
                    "page_link":   page_link,
                }
            }

        return {"status": "not_found", "message": "Abituriyent topilmadi yoki ball 56.7 dan past"}

    except Exception as e:
        return {"status": "error", "message": str(e)}
