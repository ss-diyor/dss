import requests
from bs4 import BeautifulSoup

def get_student_data(entrant_id):
    url = "https://mandat.uzbmb.uz/Bakalavr/MainSearch"
    params = {
        "entrantid": entrant_id,
        "lang": "uz"
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        print(f"So'rov yuborilmoqda: {url} {params}")
        response = requests.get(url, params=params, headers=headers, timeout=30)
        print(f"Javob kodi: {response.status_code}")
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Natijalarni qidirish
            student_cards = soup.find_all('div', class_='m3-rescard')
            
            results = []
            for card in student_cards:
                name_tag = card.find('div', class_='m3-rescard__name')
                id_tag = card.find('div', class_='m3-rescard__id')
                score_tag = card.find('span', class_='m3-score-val')
                
                name = name_tag.text.strip() if name_tag else "Noma'lum"
                id_val = id_tag.text.replace('#', '').strip() if id_tag else entrant_id
                score = score_tag.text.strip() if score_tag else "Noma'lum"
                
                results.append({
                    "name": name,
                    "id": id_val,
                    "score": score
                })
            
            if results:
                return {"status": "success", "data": results}
            else:
                return {"status": "not_found", "message": "Ma'lumot topilmadi"}
        else:
            return {"status": "error", "message": f"Saytga bog'lanib bo'lmadi: {response.status_code}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    test_id = "6156306"
    print(f"ID {test_id} bo'yicha qidirilmoqda...")
    data = get_student_data(test_id)
    print(data)
