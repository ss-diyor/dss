import os
import json
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
from scraper import get_student_data

TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Google Sheets sozlamalari
# GOOGLE_CREDENTIALS_JSON — service account JSON (butun matn, bir qatorda)
# SHEET_ID               — spreadsheet URL dagi uzun ID
SHEET_ID               = os.getenv("SHEET_ID", "")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")

SCOPES  = ["https://www.googleapis.com/auth/spreadsheets"]
HEADERS = ["user_id", "first_name", "last_name", "username",
           "first_seen", "last_seen", "query_count"]

USERS_PER_PAGE = 20

def _he(text: str) -> str:
    """HTML uchun < > & belgilarini escape qilish."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


# ── Sheets ulanishi ────────────────────────────────────────────────────────────

_sheet = None

def _get_sheet() -> gspread.Worksheet:
    global _sheet
    if _sheet is not None:
        return _sheet
    creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)
    ws = spreadsheet.sheet1
    # Sarlavha qatori yo'q bo'lsa qo'shamiz
    first_row = ws.row_values(1)
    if first_row != HEADERS:
        ws.insert_row(HEADERS, 1)
    _sheet = ws
    return _sheet


def _all_records() -> list[dict]:
    return _get_sheet().get_all_records()


def _row_num(records: list[dict], uid: str):
    """Foydalanuvchi qaysi qatorda ekanini topa olsa qaytaradi (2-indexed), aks holda None."""
    for i, rec in enumerate(records):
        if str(rec.get("user_id")) == uid:
            return i + 2   # 1 — header, 1 — 0-index farq
    return None

# ── Ma'lumot funksiyalari ──────────────────────────────────────────────────────

def record_user(user, query: bool = False) -> None:
    try:
        uid = str(user.id)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet = _get_sheet()
        records = _all_records()
        row = _row_num(records, uid)

        if row is None:
            # Yangi foydalanuvchi — qo'shamiz
            sheet.append_row([
                uid,
                user.first_name or "",
                user.last_name  or "",
                ("@" + user.username) if user.username else "",
                now, now,
                1 if query else 0,
            ])
        else:
            # Mavjud foydalanuvchi — B:G ustunlarini yangilaymiz
            existing = {str(r.get("user_id")): r for r in records}[uid]
            count = int(existing.get("query_count") or 0)
            if query:
                count += 1
            sheet.update(
                range_name=f"B{row}:G{row}",
                values=[[
                    user.first_name or "",
                    user.last_name  or "",
                    ("@" + user.username) if user.username else "",
                    existing.get("first_seen", now),
                    now,
                    count,
                ]]
            )
    except Exception as e:
        print(f"[record_user xato] {e}")


def get_stats() -> dict:
    try:
        records = _all_records()
        total_users   = len(records)
        total_queries = sum(int(r.get("query_count") or 0) for r in records)

        last_seen = ""
        if records:
            latest = max(records, key=lambda r: r.get("last_seen", ""))
            raw = latest.get("last_seen", "")
            if raw:
                try:
                    dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
                    last_seen = dt.strftime("%d.%m.%Y %H:%M")
                except ValueError:
                    last_seen = raw

        return {"total_users": total_users, "total_queries": total_queries, "last_seen": last_seen}
    except Exception as e:
        print(f"[get_stats xato] {e}")
        return {"total_users": 0, "total_queries": 0, "last_seen": ""}


def get_users_page(page: int) -> tuple[list, int]:
    try:
        records = _all_records()
        sorted_records = sorted(records, key=lambda r: r.get("first_seen", ""))
        total = len(sorted_records)
        start = (page - 1) * USERS_PER_PAGE
        end   = start + USERS_PER_PAGE
        return sorted_records[start:end], total
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

# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await asyncio.to_thread(record_user, update.effective_user)
    await update.message.reply_text(
        "Assalomu alaykum, hurmatli abituriyent \U0001f44b\n\n"
        "Ushbu bot orqali siz umumiy o'rningizni bilib olishingiz mumkin.\n"
        "Shunchaki 7 xonali abituriyent ID raqamini yuboring.\n\n"
        "Misol:\n`1234567`\n\n"
        "\"Saytda ko'rish\" ni bosish orqali siz mandat.uzbmb.uz saytida "
        "aynan qaysi sahifada ekanligingizni bilib olishingiz mumkin.",
        parse_mode="Markdown",
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

    await asyncio.to_thread(record_user, update.effective_user, True)
    await update.message.reply_text("\U0001f50d Qidirilmoqda... iltimos kuting.")

    result = get_student_data(user_input)

    if result["status"] == "success":
        await update.message.reply_text(
            "\u2705 *Natija topildi:*\n\n" + format_result(result["data"]),
            parse_mode="Markdown",
        )
    elif result["status"] == "not_found":
        await update.message.reply_text(
            "\u274c Ushbu ID bo'yicha ma'lumot topilmadi.\n"
            "ID raqam to'g'ri ekanligini tekshiring."
        )
    else:
        await update.message.reply_text(
            f"\u26a0\ufe0f Xatolik yuz berdi:\n`{result['message']}`",
            parse_mode="Markdown",
        )

# ── Admin handlers ────────────────────────────────────────────────────────────

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("\u26d4 Ruxsat yo'q.")
        return

    s = await asyncio.to_thread(get_stats)
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
        page = int(context.args[0]) if context.args else 1
        if page < 1:
            page = 1
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
    lines = [
        f"\U0001f465 <b>Foydalanuvchilar "
        f"({start_num}-{start_num + len(users_slice) - 1} / {total}):</b>\n"
    ]

    for i, u in enumerate(users_slice, start=start_num):
        first  = u.get("first_name", "") or ""
        last_n = u.get("last_name",  "") or ""
        full_name    = _he((first + " " + last_n).strip() or "Noma'lum")
        username_str = _he(u.get("username") or "username yo'q")
        count        = int(u.get("query_count") or 0)

        raw_last = u.get("last_seen", "")
        try:
            dt = datetime.strptime(raw_last, "%Y-%m-%d %H:%M:%S")
            last_date = dt.strftime("%d.%m.%Y")
        except (ValueError, TypeError):
            last_date = (raw_last[:10] if raw_last else "\u2014")

        lines.append(
            f"{i}. {full_name} ({username_str})"
            f" \u2014 {count} so'rov, oxirgi: {last_date}"
        )

    if total_pages > 1:
        if page < total_pages:
            lines.append(
                f"\n\U0001f4c4 Sahifa: {page}/{total_pages}"
                f"  |  Keyingisi: /users {page + 1}"
            )
        else:
            lines.append(f"\n\U0001f4c4 Sahifa: {page}/{total_pages}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help",  help_command))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CommandHandler("users", admin_users))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot ishga tushdi...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
