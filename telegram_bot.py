import os
import json
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
from scraper import get_student_data, get_total_count

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
        ws.update(range_name="A1", values=[HEADERS])
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


def escape_md(text: str) -> str:
    """Markdown v1 uchun maxsus belgilarni escape qilish."""
    text = str(text or "")
    for ch in ["\\", "*", "_", "`", "["]:
        text = text.replace(ch, "\\" + ch)
    return text


def _he(text) -> str:
    """HTML parse_mode uchun xavfli belgilarni escape qilish."""
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

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
                ]],
            )
    except Exception as e:
        print(f"[record_user xato] {e}")


def get_stats() -> dict:
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


def get_users_page(page: int) -> tuple[list, int]:
    records = _all_records()
    sorted_records = sorted(records, key=lambda r: r.get("first_seen", ""))
    total = len(sorted_records)
    start = (page - 1) * USERS_PER_PAGE
    end   = start + USERS_PER_PAGE
    return sorted_records[start:end], total

# ── Formatlash ────────────────────────────────────────────────────────────────

def format_result(d: dict) -> str:
    status_emoji = "\u2705" if d["is_pass"] else "\u274c"
    name = escape_md(str(d.get("name") or ""))
    lines = [
        f"\U0001f464 *{name}*",
        f"\U0001f194 ID: `{d['id']}`",
        f"\U0001f4ca Ball: *{d['score']}*",
        f"\U0001f4cc Holat: *{escape_md(str(d.get('pass_status') or ''))}* {status_emoji}",
    ]
    if d.get("rank"):
        rank        = d["rank"]
        total       = d.get("total_count")
        page_link   = d.get("page_link")

        if total:
            pct      = round(rank / total * 100, 1)
            rank_str = f"*{rank}-o'rin* (jami {total} ta abituriyent ichida) — top {pct}%"
        else:
            rank_str = f"*{rank}-o'rin*"

        if page_link:
            lines.append(
                f"\U0001f3c6 Reytingda: {rank_str}"
                f" \u2014 [Saytda ko'rish]({page_link})"
            )
        else:
            lines.append(f"\U0001f3c6 Reytingda: {rank_str}")
    subjects = []
    if d.get("s4subject"): subjects.append(escape_md(str(d["s4subject"])))
    if d.get("s5subject"): subjects.append(escape_md(str(d["s5subject"])))
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
    print(f"[scraper] status={result['status']}")

    if result["status"] == "success":
        d = result["data"]

        # 1-xabar: darhol natija (total_count siz)
        await update.message.reply_text(
            "\u2705 *Natija topildi:*\n\n" + format_result(d),
            parse_mode="Markdown",
        )

        # 2-xabar: jami hisoblanmoqda
        if d.get("rank") and d.get("page_number"):
            counting_msg = await update.message.reply_text(
                "\u23f3 Jami abituriyentlar soni hisoblanmoqda..."
            )

            total = await asyncio.to_thread(
                get_total_count,
                d["page_number"], d["page_size"],
                d.get("s4subject"), d.get("s5subject"), d.get("ed_lang_id"),
            )

            if total:
                pct = round(d["rank"] / total * 100, 1)
                await counting_msg.edit_text(
                    f"\U0001f4ca Jami *{total}* ta abituriyent ichida "
                    f"*{d['rank']}*-o'rin (top *{pct}%*)",
                    parse_mode="Markdown",
                )
            else:
                await counting_msg.delete()

    elif result["status"] == "not_found":
        await update.message.reply_text(
            "\u274c Ushbu ID bo'yicha ma'lumot topilmadi.\n"
            "ID raqam to'g'ri ekanligini tekshiring."
        )
    else:
        print(f"[scraper xato] {result.get('message')}")
        await update.message.reply_text(
            f"\u26a0\ufe0f Xatolik yuz berdi:\n{result['message']}"
        )

# ── Admin handlers ────────────────────────────────────────────────────────────

async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    is_admin = "Ha ✅" if uid == ADMIN_ID else "Yo'q ❌"
    await update.message.reply_text(
        f"Sizning ID: `{uid}`\n"
        f"ADMIN\\_ID: `{ADMIN_ID}`\n"
        f"Admin: *{is_admin}*",
        parse_mode="Markdown",
    )


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    print(f"[/stats] user_id={uid}, ADMIN_ID={ADMIN_ID}, match={uid == ADMIN_ID}")
    if uid != ADMIN_ID:
        await update.message.reply_text("\u26d4 Ruxsat yo'q.")
        return

    try:
        s = await asyncio.to_thread(get_stats)
        last = s["last_seen"] if s["last_seen"] else "Hali yo'q"
        await update.message.reply_text(
            "\U0001f4ca *Statistika*\n\n"
            f"\U0001f465 Jami foydalanuvchilar: *{s['total_users']}*\n"
            f"\U0001f50d Jami so'rovlar: *{s['total_queries']}*\n"
            f"\U0001f550 So'nggi faollik: *{last}*",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(
            f"\u274c *Sheets xatosi:*\n`{e}`",
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

    try:
        users_slice, total = await asyncio.to_thread(get_users_page, page)
    except Exception as e:
        await update.message.reply_text(
            f"\u274c *Sheets xatosi:*\n`{e}`",
            parse_mode="Markdown",
        )
        return

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


async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("\u26d4 Ruxsat yo'q.")
        return

    if not context.args:
        await update.message.reply_text(
            "\U0001f4e2 *Broadcast foydalanish:*\n"
            "`/broadcast <xabar matni>`\n\n"
            "Markdown qo'llab-quvvatlanadi:\n"
            "`*qalin*`  `_kursiv_`  `` `kod` ``",
            parse_mode="Markdown",
        )
        return

    # context.args newline yo'qotadi, raw text dan olamiz
    raw = update.message.text or ""
    text = raw.split(None, 1)[1] if " " in raw or "\n" in raw else ""

    try:
        records = await asyncio.to_thread(_all_records)
    except Exception as e:
        await update.message.reply_text(f"\u274c Sheets xatosi:\n`{e}`", parse_mode="Markdown")
        return

    user_ids = [str(r.get("user_id")) for r in records if r.get("user_id")]

    if not user_ids:
        await update.message.reply_text("Hali foydalanuvchilar yo'q.")
        return

    total = len(user_ids)
    status_msg = await update.message.reply_text(f"\U0001f4e4 Yuborilmoqda... 0/{total}")

    success, blocked, failed = 0, [], []

    for i, uid in enumerate(user_ids, 1):
        try:
            try:
                await context.bot.send_message(
                    chat_id=int(uid),
                    text=text,
                    parse_mode="Markdown",
                )
            except Exception as md_err:
                if "can't parse" in str(md_err).lower():
                    # Markdown xatosi — plain text sifatida qayta urinib ko'ramiz
                    await context.bot.send_message(chat_id=int(uid), text=text)
                else:
                    raise
            success += 1
        except Exception as e:
            err = str(e).lower()
            if any(k in err for k in ("forbidden", "blocked", "deactivated", "kicked", "not found")):
                blocked.append(uid)
            else:
                failed.append(uid)

        # Progress har 10 tadan
        if i % 10 == 0 or i == total:
            try:
                await status_msg.edit_text(f"\U0001f4e4 Yuborilmoqda... {i}/{total}")
            except Exception:
                pass

        await asyncio.sleep(1)  # 1 xabar/sekund — xavfsiz tezlik

    # ── Yakuniy hisobot ────────────────────────────────────────────────────────
    lines = ["\U0001f4e2 *Broadcast yakunlandi*\n",
             f"\u2705 Muvaffaqiyatli: *{success}* ta"]

    if blocked:
        lines.append(f"\U0001f6ab Bot blok qilgan: *{len(blocked)}* ta")
        preview = ", ".join(f"`{u}`" for u in blocked[:20])
        if len(blocked) > 20:
            preview += f" ... va yana {len(blocked) - 20} ta"
        lines.append(f"   {preview}")

    if failed:
        lines.append(f"\u26a0\ufe0f Boshqa xato: *{len(failed)}* ta")
        preview = ", ".join(f"`{u}`" for u in failed[:10])
        if len(failed) > 10:
            preview += f" ... va yana {len(failed) - 10} ta"
        lines.append(f"   {preview}")

    try:
        await status_msg.edit_text("\n".join(lines), parse_mode="Markdown")
    except Exception:
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ── main ──────────────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    import os
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        print("[CONFLICT] Boshqa bot instance ishlayapti — process to'xtatildi.")
        os._exit(1)
    print(f"[Xato] {context.error}")


def main() -> None:
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help",  help_command))
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CommandHandler("users", admin_users))
    application.add_handler(CommandHandler("broadcast", admin_broadcast))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    print("Bot ishga tushdi...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=False,
    )


if __name__ == "__main__":
    main()
