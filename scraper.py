import re
import requests
from urllib.parse import urlencode
from bs4 import BeautifulSoup

BASE_URL = "https://mandat.uzbmb.uz"
HEADERS  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


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
        resp = requests.get(
            f"{BASE_URL}/Bakalavr/Paginate",
            params=params, headers=HEADERS, timeout=15,
        )
        if resp.status_code != 200:
            return False
        soup = BeautifulSoup(resp.text, 'html.parser')
        return bool(soup.find('div', class_='m3-rescard'))
    except Exception:
        return False


def _find_true_last_page(current_page, page_size, s4subject, s5subject, ed_lang_id):
    """Binary search orqali haqiqiy oxirgi sahifani topadi."""
    def has(n):
        return _page_has_content(n, page_size, s4subject, s5subject, ed_lang_id)

    # 1. Eksponent o'sish: yuqori chegara topish
    low  = current_page
    step = 1
    high = current_page + step
    while has(high):
        low   = high
        step *= 2
        high  = low + step
        if high > 500_000:
            return current_page  # fallback

    # 2. Binary search
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
        resp = requests.get(
            f"{BASE_URL}/Bakalavr/Paginate",
            params=params, headers=HEADERS, timeout=15,
        )
        if resp.status_code != 200:
            return page_size
        soup = BeautifulSoup(resp.text, 'html.parser')
        count = len(soup.find_all('div', class_='m3-rescard'))
        return count if count > 0 else page_size
    except Exception:
        return page_size


def get_total_count(page_number, page_size, s4subject, s5subject, ed_lang_id):
    """
    Binary search orqali jami abituriyentlar sonini hisoblaydi.
    Bu funksiya alohida chaqiriladi — natijadan keyin.
    """
    try:
        last_page  = _find_true_last_page(page_number, page_size, s4subject, s5subject, ed_lang_id)
        cards_last = _count_cards_on_page(last_page, page_size, s4subject, s5subject, ed_lang_id)
        return (last_page - 1) * page_size + cards_last
    except Exception:
        return None


def get_student_data(entrant_id):
    """
    Abituriyent ma'lumotlarini qaytaradi.
    total_count bu yerda hisoblanmaydi — get_total_count() alohida chaqiriladi.
    """
    url    = f"{BASE_URL}/Bakalavr/MainSearch"
    params = {"entrantid": entrant_id, "lang": "uz"}

    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=30)
        if response.status_code != 200:
            return {"status": "error", "message": f"Saytga bog'lanib bo'lmadi: {response.status_code}"}

        soup      = BeautifulSoup(response.text, 'html.parser')
        all_cards = soup.find_all('div', class_='m3-rescard')

        if not all_cards:
            return {"status": "not_found", "message": "Ma'lumot topilmadi"}

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

        return {"status": "not_found", "message": "Ma'lumot topilmadi"}

    except Exception as e:
        return {"status": "error", "message": str(e)}
