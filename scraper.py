import re
import requests
from urllib.parse import urlencode
from bs4 import BeautifulSoup

BASE_URL = "https://mandat.uzbmb.uz"

def clean_id(text):
    """Faqat raqamlarni qoldiradi — '#', bo'sh joy, ko'rinmas belgilarni olib tashlaydi."""
    return re.sub(r'\D', '', text)

def get_student_data(entrant_id):
    url = f"{BASE_URL}/Bakalavr/MainSearch"
    params = {"entrantid": entrant_id, "lang": "uz"}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        if response.status_code != 200:
            return {"status": "error", "message": f"Saytga bog'lanib bo'lmadi: {response.status_code}"}

        soup = BeautifulSoup(response.text, 'html.parser')
        all_cards = soup.find_all('div', class_='m3-rescard')

        if not all_cards:
            return {"status": "not_found", "message": "Ma'lumot topilmadi"}

        # Faol sahifa raqamini topish
        page_number = None
        page_size = 10
        active_item = soup.find('li', class_='page-item active')
        if active_item:
            p_input = active_item.find('input', {'name': 'pageNumber'})
            s_input = active_item.find('input', {'name': 'pageSize'})
            if p_input:
                page_number = int(p_input['value'])
            if s_input:
                page_size = int(s_input['value'])

        # Fanlarni va boshqa parametrlarni topish
        s4subject, s5subject, ed_lang_id = None, None, None
        first_form = soup.find('form', action='/Bakalavr/Paginate')
        if first_form:
            s4 = first_form.find('input', {'name': 's4subject'})
            s5 = first_form.find('input', {'name': 's5subject'})
            ed = first_form.find('input', {'name': 'edLangId'})
            if s4:
                s4subject = s4['value']
            if s5:
                s5subject = s5['value']
            if ed:
                ed_lang_id = ed['value']

        # Sahifa linki
        page_link = None
        if page_number:
            params = {
                'pageNumber': page_number,
                'pageSize': page_size,
                'lang': 'uz',
            }
            if s4subject:
                params['s4subject'] = s4subject
            if s5subject:
                params['s5subject'] = s5subject
            if ed_lang_id:
                params['edLangId'] = ed_lang_id
            page_link = f"{BASE_URL}/Bakalavr/Paginate?{urlencode(params)}"

        # O'quvchini qidirish
        target_id = clean_id(str(entrant_id))

        for idx, card in enumerate(all_cards):
            id_tag = card.find('div', class_='m3-rescard__id')
            if not id_tag:
                continue

            card_id = clean_id(id_tag.text)  # faqat raqamlar

            if card_id == target_id:
                T = idx + 1
                rank = (page_number - 1) * page_size + T if page_number else None

                name_tag = card.find('div', class_='m3-rescard__name')
                name = name_tag.get_text(strip=True) if name_tag else "Noma'lum"

                score_tag = card.find('span', class_='m3-score-val')
                score = score_tag.text.strip() if score_tag else "Noma'lum"

                card_classes = card.get('class', [])
                is_pass = 'm3-rescard--pass' in card_classes
                status = "O'tdi" if is_pass else "O'tmadi"

                return {
                    "status": "success",
                    "data": {
                        "name": name,
                        "id": card_id,
                        "score": score,
                        "pass_status": status,
                        "is_pass": is_pass,
                        "rank": rank,
                        "s4subject": s4subject,
                        "s5subject": s5subject,
                        "page_link": page_link,
                    }
                }

        return {"status": "not_found", "message": "Ma'lumot topilmadi"}

    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    test_id = input("ID kiriting: ").strip()
    result = get_student_data(test_id)
    if result["status"] == "success":
        d = result["data"]
        print(f"Ism         : {d['name']}")
        print(f"ID          : {d['id']}")
        print(f"Ball        : {d['score']}")
        print(f"Holat       : {d['pass_status']}")
        print(f"Umumiy o'rin: {d['rank']}")
        print(f"Fanlar      : {d['s4subject']} | {d['s5subject']}")
    else:
        print(result)
