import re
import requests
from urllib.parse import urlencode
from bs4 import BeautifulSoup

BASE_URL = "https://mandat.uzbmb.uz"
HEADERS  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def clean_id(text):
    """Faqat raqamlarni qoldiradi."""
    return re.sub(r'\D', '', text)


def _paginate_params(page_number, page_size, s4subject, s5subject, ed_lang_id):
    p = {'pageNumber': page_number, 'pageSize': page_size, 'lang': 'uz'}
    if s4subject: p['s4subject'] = s4subject
    if s5subject: p['s5subject'] = s5subject
    if ed_lang_id: p['edLangId'] = ed_lang_id
    return p


def _get_last_page(soup, current_page):
    """Paginatsiyadagi barcha sahifa raqamlaridan maksimalini topadi."""
    last = current_page or 1
    for item in soup.find_all('li', class_='page-item'):
        p_input = item.find('input', {'name': 'pageNumber'})
        if p_input:
            try:
                pn = int(p_input['value'])
                if pn > last:
                    last = pn
            except (ValueError, TypeError):
                pass
    return last


def _fetch_total_count(last_page, page_size, s4subject, s5subject, ed_lang_id):
    """Oxirgi sahifani yuklab aniq jami abituriyentlar sonini hisoblaydi."""
    try:
        params = _paginate_params(last_page, page_size, s4subject, s5subject, ed_lang_id)
        resp = requests.get(
            f"{BASE_URL}/Bakalavr/Paginate",
            params=params,
            headers=HEADERS,
            timeout=30,
        )
        if resp.status_code != 200:
            return last_page * page_size  # taxminiy
        soup = BeautifulSoup(resp.text, 'html.parser')
        cards_on_last = len(soup.find_all('div', class_='m3-rescard'))
        if cards_on_last == 0:
            return last_page * page_size  # fallback
        return (last_page - 1) * page_size + cards_on_last
    except Exception:
        return last_page * page_size  # xato bo'lsa taxminiy qaytaramiz


def get_student_data(entrant_id):
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

        # Faol sahifa raqami va hajmi
        page_number, page_size = None, 10
        active_item = soup.find('li', class_='page-item active')
        if active_item:
            p_inp = active_item.find('input', {'name': 'pageNumber'})
            s_inp = active_item.find('input', {'name': 'pageSize'})
            if p_inp: page_number = int(p_inp['value'])
            if s_inp: page_size   = int(s_inp['value'])

        # Fanlar va til parametrlari
        s4subject, s5subject, ed_lang_id = None, None, None
        first_form = soup.find('form', action='/Bakalavr/Paginate')
        if first_form:
            s4 = first_form.find('input', {'name': 's4subject'})
            s5 = first_form.find('input', {'name': 's5subject'})
            ed = first_form.find('input', {'name': 'edLangId'})
            if s4: s4subject  = s4['value']
            if s5: s5subject  = s5['value']
            if ed: ed_lang_id = ed['value']

        # Sahifa linki
        page_link = None
        if page_number:
            page_link = (
                f"{BASE_URL}/Bakalavr/Paginate?"
                + urlencode(_paginate_params(page_number, page_size, s4subject, s5subject, ed_lang_id))
            )

        # O'quvchini qidirish
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

            # Jami abituriyentlar soni (oxirgi sahifaga qo'shimcha so'rov)
            total_count = None
            if page_number:
                last_page   = _get_last_page(soup, page_number)
                total_count = _fetch_total_count(
                    last_page, page_size, s4subject, s5subject, ed_lang_id
                )

            return {
                "status": "success",
                "data": {
                    "name":        name,
                    "id":          target_id,
                    "score":       score,
                    "pass_status": "O'tdi" if is_pass else "O'tmadi",
                    "is_pass":     is_pass,
                    "rank":        rank,
                    "total_count": total_count,
                    "s4subject":   s4subject,
                    "s5subject":   s5subject,
                    "page_link":   page_link,
                }
            }

        return {"status": "not_found", "message": "Ma'lumot topilmadi"}

    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    test_id = input("ID kiriting: ").strip()
    result  = get_student_data(test_id)
    if result["status"] == "success":
        d = result["data"]
        print(f"Ism         : {d['name']}")
        print(f"ID          : {d['id']}")
        print(f"Ball        : {d['score']}")
        print(f"Holat       : {d['pass_status']}")
        print(f"O'rin       : {d['rank']} / {d['total_count']}")
        print(f"Fanlar      : {d['s4subject']} | {d['s5subject']}")
    else:
        print(result)
