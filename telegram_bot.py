import os
import json
import asyncio
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
from scraper import get_student_data

TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SHEET_ID = os.getenv("SHEET_ID", "")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")

SCOPES  = ["https://www.googleapis.com/auth/spreadsheets"]

# Yangi 11 ustunli format
HEADERS = [
    "user_id", "tg_first_name", "tg_last_name", "username", "phone",
    "first_seen", "last_seen", "query_count",
    "last_query_id", "last_student_name", "last_subjects",
]

USERS_PER_PAGE = 5   # har biri ko'p qator egallaydi


# ── Google Sheets ─────────────────────────────────────────────────────────────

_sheet = None


def _get_sheet() -> gspread.Worksheet:
    global _sheet
    if _sheet is not None:
        return _sheet
    creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    ws = client.open_by_key(SHEET_ID).sheet1
    # Sarlavha qatorini tekshirib yangilaymiz (eski 7 ustundan yangi 11 ga)
    first_row = ws.row_values(1)
    if first_row != HEADERS:
        ws.update(range_name="A1", values=[HEADERS])
    _sheet = ws
    return _sheet


def _all_records() -> list[dict]:
    return _get_sheet().get_all_records()


def _find_row(records: list[dict], uid: str):
    for i, rec in enumerate(records):
        if str(rec.get("user_id")) == uid:
            return i + 2  # +1 header, +1 0-index
    return None


def _he(text) -> str:
    """HTML uchun xavfli belgilarni escape qilish."""
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── Ma'lumot funksiyalari ──────────────────────────────────────────────────────

def record_user(user, query_data: dict | None = None) -> None:
    """
    query_data = {"id": "1234567", "name": "Ism Familiya", "subjects": "Fan1 + Fan2"}
    """
    try:
        uid  = str(user.id)
        now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        uname = ("@" + user.username) if user.username else ""
        sheet   = _get_sheet()
        records = _all_records()
        row     = _find_row(records, uid)

        if row is None:
            sheet.append_row([
                uid,
                user.first_name or "",
                user.last_name  or "",
                uname, "",           # phone bo'sh
                now, now,
                1 if query_data else 0,
                query_data["id"]       if query_data else "",
                query_data["name"]     if query_data else "",
                query_data["subjects"] if query_data else "",
            ])
        else:
            ex    = next(r for r in records if str(r.get("user_id")) == uid)
            count = int(ex.get("query_count") or 0) + (1 if query_data else 0)
            sheet.update(
                range_name=f"B{row}:K{row}",
                values=[[
                    user.first_name or "",
                    user.last_name  or "",
                    uname,
                    ex.get("phone", ""),
                    ex.get("first_seen", now),
                    now,
                    count,
                    query_data["id"]       if query_data else ex.get("last_query_id", ""),
                    query_data["name"]     if query_data else ex.get("last_student_name", ""),
                    query_data["subjects"] if query_data else ex.get("last_subjects", ""),
                ]]
            )
    except Exception as e:
        print(f"[record_user xato] {e}")


def save_phone(user, phone: str) -> None:
    try:
        uid     = str(user.id)
        now     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet   = _get_sheet()
        records = _all_records()
        row     = _find_row(records, uid)
        if row:
            sheet.update(range_name=f"E{row}", values=[[phone]])
        else:
            sheet.append_row([
                uid,
                user.first_name or "",
                user.last_name  or "",
                ("@" + user.username) if user.username else "",
                phone,
                now, now, 0, "", "", "",
            ])
    except Exception as e:
        print(f"[save_phone xato] {e}")


def get_stats() -> dict:
    try:
        records       = _all_records()
        total_users   = len(records)
        total_queries = sum(int(r.get("query_count") or 0) for r in records)
        last_seen     = ""
        if records:
            latest = max(records, key=lambda r: r.get("last_seen", ""))
            raw    = latest.get("last_seen", "")
            if raw:
                try:
                    last_seen = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
                except ValueError:
                    last_seen = raw
        return {"total_users": total_users, "total_queries": total_queries, "last_seen": last_seen}
    except Exception as e:
        print(f"[get_stats xato] {e}")
        return {"total_users": 0, "total_queries": 0, "last_seen": ""}


def get_users_page(page: int) -> tuple[list, int]:
    try:
        records = sorted(_all_records(), key=lambda r: r.get("first_seen", ""))
        total   = len(records)
        start   = (page - 1) * USERS_PER_PAGE
        return records[start : start + USERS_PER_PAGE], total
    except Exception as e:
        print(f"[get_users_page xato] {e}")
        return [], 0


# ── Formatlash ────────────────────────────────────────────────────────────────

def format_result(d: dict) -> str:
    status_emoji = "\u2705" if d["is_pass"] else "\u274c"
    lines = [
        f"\U0001f464 *{d['name']}*",
        f"\U0001f194 ID: `{d['id']}`",
        f"\U0001f4ca Ball: *{d['score']}*",
        f"\U0001f4cc Holat: *{d['pass_status']}* {status_emoji}",
    ]
    if d.get("rank"):
        if d.get("page_link"):
            lines.append(
                f"\U0001f3c6 Umumiy o'rin: *{d['rank']}-o'rin*"
                f" \u2014 [Saytda ko'rish]({d['page_link']})"
            )
        else:
            lines.append(f"\U0001f3c6 Umumiy o'rin: *{d['rank']}-o'rin*")
    subjects = []
    if d.get("s4subject"): subjects.append(d["s4subject"])
    if d.get("s5subject"): subjects.append(d["s5subject"])
    if subjects:
        lines.append(f"\U0001f4da Fanlar: {' | '.join(subjects)}")
    return "\n".join(lines)


def format_user_entry(u: dict) -> str:
    last_id      = _he(u.get("last_query_id", ""))    or "\u2014"
    student_name = _he(u.get("last_student_name", "")) or "\u2014"
    subjects     = _he(u.get("last_subjects", ""))     or "\u2014"
    username     = _he(u.get("username", ""))           or "yo'q"
    phone        = _he(u.get("phone", ""))              or "ulashilmagan"

    raw_last = u.get("last_seen", "")
    try:
        last_time = datetime.strptime(raw_last, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError):
        last_time = "\u2014"

    return (
        f"\U0001f194 Abituriyent qidirgan oxirgi ID: <code>{last_id}</code>\n"
        f"\U0001f464 Ism: {student_name}\n"
        f"\U0001f3af Yo'nalish: {subjects}\n"
        f"\n"
        f"\U0001f517 Telegram:\n"
        f"\U0001f464 Username: <code>{username}</code>\n"
        f"\U0001f4de Telefon: <code>{phone}</code>\n"
        f"\U0001f4c5 Oxirgi so'rov vaqti: {last_time}"
    )


# ── Handlers ──────────────────────────────────────────────────────────────────

def _contact_keyboard():
    kb = [[KeyboardButton("\U0001f4f1 Telefon raqamni ulashish", request_contact=True)]]
    return ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await asyncio.to_thread(record_user, update.effective_user)
    await update.message.reply_text(
        "Assalomu alaykum, hurmatli abituriyent \U0001f44b\n\n"
        "Ushbu bot orqali siz umumiy o'rningizni bilib olishingiz mumkin.\n"
        "Shunchaki 7 xonali abituriyent ID raqamini yuboring.\n\n"
        "Misol:\n`1234567`\n\n"
        "Iltimos, telefon raqamingizni ham ulashing \U0001f447",
        parse_mode="Markdown",
        reply_markup=_contact_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await asyncio.to_thread(record_user, update.effective_user)
    await update.message.reply_text(
        "\U0001f4d6 *Foydalanish:*\n\n"
        "7 xonali abituriyent ID raqamini yuboring.\n"
        "Misol: `6156306`\n\n"
        "*Bot sizga ko'rsatadi:*\n"
        "\u2022 \U0001f464 Ism\n"
        "\u2022 \U0001f4ca To'plangan ball\n"
        "\u2022 \U0001f4cc O'tdi / O'tmadi\n"
        "\u2022 \U0001f3c6 Umumiy o'rin\n"
        "\u2022 \U0001f4da Fanlar",
        parse_mode="Markdown",
    )


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    contact = update.message.contact
    # Faqat o'z raqamini qabul qilamiz
    if contact.user_id != update.effective_user.id:
        return
    phone = contact.phone_number
    if not phone.startswith("+"):
        phone = "+" + phone
    await asyncio.to_thread(save_phone, update.effective_user, phone)
    await update.message.reply_text(
        "\u2705 Telefon raqam saqlandi, rahmat!",
        reply_markup=ReplyKeyboardRemove(),
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_input = update.message.text.strip()

    if not (user_input.isdigit() and len(user_input) == 7):
        await asyncio.to_thread(record_user, update.effective_user)
        await update.message.reply_text(
            "\u26a0\ufe0f Iltimos, *7 xonali* abituriyent ID raqamini kiriting.\n"
            "Misol: `6156306`",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text("\U0001f50d Qidirilmoqda... iltimos kuting.")
    result = get_student_data(user_input)

    if result["status"] == "success":
        d = result["data"]
        subj = []
        if d.get("s4subject"): subj.append(d["s4subject"])
        if d.get("s5subject"): subj.append(d["s5subject"])
        query_data = {
            "id":       user_input,
            "name":     d.get("name", ""),
            "subjects": " + ".join(subj),
        }
        await asyncio.to_thread(record_user, update.effective_user, query_data)
        await update.message.reply_text(
            "\u2705 *Natija topildi:*\n\n" + format_result(d),
            parse_mode="Markdown",
        )
    elif result["status"] == "not_found":
        await asyncio.to_thread(record_user, update.effective_user)
        await update.message.reply_text(
            "\u274c Ushbu ID bo'yicha ma'lumot topilmadi.\n"
            "ID raqam to'g'ri ekanligini tekshiring."
        )
    else:
        await asyncio.to_thread(record_user, update.effective_user)
        await update.message.reply_text(
            f"\u26a0\ufe0f Xatolik yuz berdi:\n`{result['message']}`",
            parse_mode="Markdown",
        )


# ── Admin handlers ────────────────────────────────────────────────────────────

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("\u26d4 Ruxsat yo'q.")
        return
    s    = await asyncio.to_thread(get_stats)
    last = s["last_seen"] if s["last_seen"] else "Hali yo'q"
    await update.message.reply_text(
        "\U0001f4ca *Statistika*\n\n"
        f"\U0001f465 Jami foydalanuvchilar: *{s['total_users']}*\n"
        f"\U0001f50d Jami so'rovlar: *{s['total_queries']}*\n"
        f"\U0001f550 So'nggi faollik: *{last}*",
        parse_mode="Markdown",
    )


async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("\u26d4 Ruxsat yo'q.")
        return

    try:
        page = max(1, int(context.args[0])) if context.args else 1
    except (ValueError, IndexError):
        page = 1

    users_slice, total = await asyncio.to_thread(get_users_page, page)

    if total == 0:
        await update.message.reply_text("Hali hech kim foydalanmagan.")
        return

    total_pages = (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE

    if page > total_pages:
        await update.message.reply_text(
            f"\u26a0\ufe0f Sahifa mavjud emas. Jami {total_pages} ta sahifa bor.\n"
            "Misol: /users 1"
        )
        return

    start_num = (page - 1) * USERS_PER_PAGE + 1
    end_num   = start_num + len(users_slice) - 1

    header  = (
        f"\U0001f465 <b>Foydalanuvchilar ({start_num}\u2013{end_num} / {total})</b>"
    )
    SEP     = "\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    entries = [format_user_entry(u) for u in users_slice]
    body    = SEP.join(entries)

    footer = ""
    if total_pages > 1:
        if page < total_pages:
            footer = f"\n\n\U0001f4c4 Sahifa: {page}/{total_pages}  |  Keyingisi: /users {page + 1}"
        else:
            footer = f"\n\n\U0001f4c4 Sahifa: {page}/{total_pages}"

    await update.message.reply_text(
        header + SEP + body + footer,
        parse_mode="HTML",
    )


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help",  help_command))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CommandHandler("users", admin_users))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot ishga tushdi...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
