# Mandat.uzbmb.uz sayti bilan ishlovchi Telegram bot yaratish bo'yicha qo'llanma

Ushbu qo'llanma `mandat.uzbmb.uz` saytidan abituriyentlarning natijalarini olib, ularni Telegram bot orqali foydalanuvchilarga taqdim etuvchi botni qanday yaratishni bosqichma-bosqich tushuntiradi.

## 1. Kirish

`MandatRating_Bot` kabi botlar abituriyentlar uchun o'z natijalarini tez va qulay tarzda bilish imkoniyatini yaratadi. Ushbu bot `mandat.uzbmb.uz` saytining ochiq ma'lumotlaridan foydalanib, foydalanuvchi kiritgan ID raqami bo'yicha natijalarni topadi va Telegram orqali yuboradi.

## 2. Talablar

Botni ishga tushirish uchun quyidagi dasturiy ta'minot va kutubxonalar kerak bo'ladi:

*   **Python 3.x:** Dasturlash tili.
*   **`requests` kutubxonasi:** HTTP so'rovlarini yuborish uchun.
*   **`beautifulsoup4` kutubxonasi:** HTML sahifalarini tahlil qilish (parsing) uchun.
*   **`python-telegram-bot` kutubxonasi:** Telegram bot API bilan ishlash uchun.

Ushbu kutubxonalarni quyidagi buyruqlar orqali o'rnatishingiz mumkin:

```bash
sudo pip3 install requests beautifulsoup4 python-telegram-bot
```

## 3. Scraper.py - Ma'lumot olish mexanizmi

`scraper.py` fayli `mandat.uzbmb.uz` saytidan abituriyent ma'lumotlarini olish uchun javobgardir. U berilgan ID raqami bo'yicha saytga so'rov yuboradi va qaytgan HTML sahifasidan kerakli ma'lumotlarni ajratib oladi.

```python
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
        response = requests.get(url, params=params, headers=headers, timeout=30)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
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
```

**Kodning tushuntirilishi:**

*   `get_student_data(entrant_id)` funksiyasi berilgan `entrant_id` (abituriyent ID raqami) bo'yicha `mandat.uzbmb.uz` saytiga `GET` so'rov yuboradi.
*   `BeautifulSoup` yordamida qaytgan HTML sahifasi tahlil qilinadi.
*   `m3-rescard` klassiga ega `div` elementlari topiladi, chunki har bir abituriyent natijasi shu element ichida joylashgan.
*   Har bir `m3-rescard` ichidan abituriyentning ismi (`m3-rescard__name`), ID raqami (`m3-rescard__id`) va to'plagan balli (`m3-score-val`) ajratib olinadi.
*   Natijalar lug'atlar ro'yxati ko'rinishida qaytariladi. Agar ma'lumot topilmasa yoki xatolik yuz bersa, tegishli xabar qaytariladi.

## 4. Telegram_bot.py - Bot mantiqi

`telegram_bot.py` fayli Telegram botning asosiy mantiqini o'z ichiga oladi. U foydalanuvchidan xabarlarni qabul qiladi, `scraper.py` yordamida ma'lumotlarni oladi va natijalarni foydalanuvchiga qaytaradi.

```python
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from scraper import get_student_data

# Telegram bot tokenini environment variable dan olish
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Assalomu alaykum! Men MandatRating_Bot. Menga abituriyent ID raqamini yuboring, men sizga natijalarni topib beraman."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Menga abituriyent ID raqamini yuboring. Misol: `6156306`"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_input = update.message.text
    if user_input.isdigit() and len(user_input) == 7:
        await update.message.reply_text("Natijalar qidirilmoqda... Iltimos kuting.")
        data = get_student_data(user_input)
        
        if data["status"] == "success":
            response_message = "Abituriyent natijalari:\n"
            for student in data["data"]:
                response_message += f"\nIsm: {student["name"]}\nID: {student["id"]}\nBall: {student["score"]}\n---"
            await update.message.reply_text(response_message)
        elif data["status"] == "not_found":
            await update.message.reply_text("Ushbu ID raqam bo'yicha ma'lumot topilmadi. Iltimos, ID raqamni to'g'ri kiritganingizga ishonch hosil qiling.")
        else:
            await update.message.reply_text(f"Ma'lumotlarni olishda xatolik yuz berdi: {data["message"]}")
    else:
        await update.message.reply_text("Iltimos, 7 xonali abituriyent ID raqamini kiriting. Misol: `6156306`")

def main() -> None:
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
```

**Kodning tushuntirilishi:**

*   `TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")`: Bot tokenini muhit o'zgaruvchisidan oladi. Bu token BotFather orqali olinadi.
*   `start` funksiyasi: Bot `/start` buyrug'iga javob beradi.
*   `help_command` funksiyasi: Bot `/help` buyrug'iga javob beradi.
*   `handle_message` funksiyasi: Foydalanuvchi yuborgan matnli xabarlarni qayta ishlaydi. Agar xabar 7 xonali raqam bo'lsa, `get_student_data` funksiyasini chaqiradi va natijalarni foydalanuvchiga yuboradi.
*   `main` funksiyasi: Botni ishga tushiradi va xabarlarni tinglaydi.

## 5. Botni sozlash (Telegram BotFather)

Botni ishga tushirishdan oldin sizga Telegram bot tokeni kerak bo'ladi. Uni olish uchun quyidagi qadamlarni bajaring:

1.  Telegramda `@BotFather` ni toping va u bilan suhbatni boshlang.
2.  `/newbot` buyrug'ini yuboring.
3.  Botingiz uchun nom tanlang (masalan, 
`MandatNatijaBot`).
4.  Botingiz uchun username tanlang (masalan, `MandatNatijaBot_bot`). Username `_bot` bilan tugashi shart.
5.  `BotFather` sizga bot tokenini beradi. Bu token `123456789:ABCDefg123456789ABCDefg123456789` formatida bo'ladi. Uni saqlab qo'ying.

## 6. Botni ishga tushirish

Botni ishga tushirish uchun quyidagi qadamlarni bajaring:

1.  `scraper.py` va `telegram_bot.py` fayllarini bir xil papkaga joylashtiring.
2.  Terminalda shu papkaga o'ting.
3.  `TELEGRAM_BOT_TOKEN` muhit o'zgaruvchisini o'zingizning bot tokeningiz bilan o'rnating. Masalan:

    ```bash
    export TELEGRAM_BOT_TOKEN="YOUR_BOT_TOKEN_HERE"
    ```

    `YOUR_BOT_TOKEN_HERE` o'rniga `BotFather` dan olgan tokeningizni qo'ying.
4.  Botni ishga tushirish uchun quyidagi buyruqni bajaring:

    ```bash
    python3 telegram_bot.py
    ```

Endi sizning Telegram botingiz ishga tushadi va foydalanuvchilardan xabarlarni qabul qilishga tayyor bo'ladi.

## 7. Xulosa

Ushbu qo'llanma yordamida siz `mandat.uzbmb.uz` saytidan ma'lumot oluvchi va ularni Telegram orqali taqdim etuvchi oddiy botni yaratishni o'rgandingiz. Botning funksionalligini yanada kengaytirish uchun siz qo'shimcha buyruqlar, ma'lumotlarni saqlash uchun baza (masalan, SQLite) yoki kengaytirilgan qidiruv funksiyalarini qo'shishingiz mumkin.

**Muhim eslatma:** Web scraping usuli saytning tuzilishi o'zgarganda ishlamay qolishi mumkin. Bunday holatlarda `scraper.py` faylini yangilash talab qilinadi. Agar sayt rasmiy API taqdim etsa, undan foydalanish yanada barqaror yechim bo'ladi.
