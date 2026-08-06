import os
import json
import asyncio
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
from scraper import get_student_data, get_total_count

TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

SHEET_ID                = os.getenv("SHEET_ID", "")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
LOG_GROUP_ID            = os.getenv("LOG_GROUP_ID", "")

SCOPES  = ["https://www.googleapis.com/auth/spreadsheets"]
HEADERS = ["user_id", "first_name", "last_name", "username",
           "first_seen", "last_seen", "query_count", "is_blocked",
           "last_id_1", "last_id_2", "last_id_3"]

USERS_PER_PAGE = 15

# ── Topic management ─────────────────────────────────────────────────────────────
_topic_cache = {}        # Cache for topic IDs: {subject_combo: topic_id}
_topics_loaded = False   # Flag: mavjud topiclar bir marta yuklandi
MAX_TOPICS = 50          # Maximum number of topics to create

# ── Sheets ulanishi ────────────────────────────────────────────────────────────

_sheet = None
_stats_sheet = None

def _get_sheet() -> gspread.Worksheet:
    global _sheet
    if _sheet is not None:
        return _sheet
    creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)
    ws = spreadsheet.sheet1
    first_row = ws.row_values(1)
    if first_row != HEADERS:
        ws.update(range_name="A1", values=[HEADERS])
    _sheet = ws
    return _sheet


def _get_stats_sheet() -> gspread.Worksheet:
    """'Statistics' varag'ini qaytaradi, yo'q bo'lsa yaratadi."""
    global _stats_sheet
    if _stats_sheet is not None:
        return _stats_sheet
    creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)
    try:
        ws = spreadsheet.worksheet("Statistics")
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title="Statistics", rows=10, cols=2)
        ws.update(range_name="A1", values=[["Ko'rsatkich", "Qiymat"]])
    _stats_sheet = ws
    return _stats_sheet


def update_stats_sheet() -> None:
    """Statistics varag'ini tarixiy ma'lumotlar bilan yangilaydi."""
    try:
        records = _all_records()
        today = datetime.now(tz=timezone(timedelta(hours=5))).strftime("%d.%m.%Y")
        today_full = datetime.now(tz=timezone(timedelta(hours=5))).strftime("%Y-%m-%d")

        # Faqat user_id mavjud bo'lgan satrlarni olamiz
        valid_records = [r for r in records if str(r.get("user_id") or "").strip()]
        total_users   = len(valid_records)
        
        def _safe_int(v):
            try: return int(float(str(v or 0)))
            except: return 0

        total_queries = sum(_safe_int(r.get("query_count")) for r in valid_records)

        today_queries = sum(
            _safe_int(r.get("query_count"))
            for r in valid_records
            if str(r.get("last_seen") or "").startswith(today_full)
        )
        today_new_users = sum(
            1 for r in valid_records
            if str(r.get("first_seen") or "").startswith(today_full)
        )

        # Bugun faoliyat ko'rsatgan foydalanuvchilar (last_seen bugungi kunga to'g'ri keladiganlar)
        # Eslatma: user_id larning bugungi so'rovlar sonini aniq hisoblash uchun 
        # agar qidiruv vaqtida query_count oshsa, bugungi so'rov yuborganlarni filtered qilamiz.
        # Hozirgi bot arxitekturasida query_count jami so'rovlar sonini saqlaydi.
        # Bugungi eng faol foydalanuvchini topish uchun last_seen bugun bo'lgan va
        # bugun eng ko'p so'rov yuborgan (yoki oxirgi vaqtlari faol bo'lgan) foydalanuvchini aniqlaymiz.
        # Aniqroq bo'lishi uchun last_seen bugun bo'lgan valid_records ni olamiz:
        today_active_users = [
            r for r in valid_records 
            if str(r.get("last_seen") or "").startswith(today_full)
        ]

        if today_active_users:
            # Agar bugun so'rov yuborganlar bo'lsa, ularning ichidan query_count eng ko'pi
            # (yoki bugungi faolligi eng yuqori bo'lgani) — lekin query_count umumiy bo'lgani uchun,
            # agar bugun faqat bugungi so'rovlar hisoblansa:
            # Keling, bugun active bo'lganlar orasida max query_count ni olamiz yoki 
            # umumiy query_count bo'yicha bugun kirganlar ichidan topamiz.
            top_today = max(today_active_users, key=lambda r: _safe_int(r.get("query_count")), default=None)
        else:
            top_today = None

        if top_today:
            first  = top_today.get("first_name") or ""
            last_n = top_today.get("last_name")  or ""
            top_name  = (first + " " + last_n).strip() or "Noma'lum"
            top_count = _safe_int(top_today.get("query_count"))
            top_str   = f"{top_name} ({top_count} ta)"
        else:
            top_today_any = max(valid_records, key=lambda r: _safe_int(r.get("query_count")), default=None)
            if top_today_any:
                first  = top_today_any.get("first_name") or ""
                last_n = top_today_any.get("last_name")  or ""
                top_name  = (first + " " + last_n).strip() or "Noma'lum"
                top_count = _safe_int(top_today_any.get("query_count"))
                top_str   = f"{top_name} ({top_count} ta)"
            else:
                top_str = "—"

        ws = _get_stats_sheet()
        
        # Yangi headers
        new_headers = [
            "Sana", 
            "Jami foydalanuvchilar", 
            "Jami so'rovlar", 
            "Bugungi so'rovlar", 
            "Bugungi yangi foydalanuvchilar", 
            "Bugungi eng faol foydalanuvchi"
        ]
        
        # Check if headers need to be updated
        current_headers = ws.row_values(1)
        if current_headers != new_headers:
            ws.update(range_name="A1", values=[new_headers])
        
        # Get all existing data
        all_data = ws.get_all_records(expected_headers=new_headers)
        
        # Check if today's data already exists
        today_row = None
        today_row_num = None
        
        for i, row in enumerate(all_data, start=2):  # start=2 because row 1 is headers
            if str(row.get("Sana", "")) == today:
                today_row = row
                today_row_num = i
                break
        
        # Prepare today's data
        today_data = [
            today,
            total_users,
            total_queries,
            today_queries,
            today_new_users,
            top_str
        ]
        
        if today_row:
            # Update existing row
            ws.update(range_name=f"A{today_row_num}:F{today_row_num}", values=[today_data])
        else:
            # Add new row at the end
            next_row = len(all_data) + 2
            ws.update(range_name=f"A{next_row}:F{next_row}", values=[today_data])
            
    except Exception as e:
        print(f"[update_stats_sheet xato] {e}")


def _all_records() -> list[dict]:
    return _get_sheet().get_all_records(expected_headers=HEADERS)


def _row_num(records: list[dict], uid: str):
    for i, rec in enumerate(records):
        if str(rec.get("user_id")) == uid:
            return i + 2
    return None


def escape_md(text: str) -> str:
    text = str(text or "")
    for ch in ["\\", "*", "_", "`", "["]:
        text = text.replace(ch, "\\" + ch)
    return text


def _he(text) -> str:
    return str(text or "").replace("&", "&​amp;").replace("<", "&​lt;").replace(">", "&​gt;")

# ── Topic management functions ───────────────────────────────────────────────────

_forum_enabled_cache: bool | None = None  # None = hali tekshirilmagan

async def _check_forum_enabled(bot) -> bool:
    """Check if the log group has forum/Topics enabled."""
    global _forum_enabled_cache
    if _forum_enabled_cache is not None:
        return _forum_enabled_cache

    if not LOG_GROUP_ID:
        _forum_enabled_cache = False
        return False

    try:
        chat = await bot.get_chat(int(LOG_GROUP_ID))
        _forum_enabled_cache = getattr(chat, 'is_forum', False)
        return _forum_enabled_cache
    except Exception as e:
        print(f"[_check_forum_enabled xato] {e}")
        return False


def _format_topic_name(s4subject: str, s5subject: str) -> str | None:
    """Format subject combination as topic name."""
    subjects = []
    if s4subject:
        subjects.append(str(s4subject).strip())
    if s5subject:
        subjects.append(str(s5subject).strip())
    
    if not subjects:
        return None
    
    return " | ".join(subjects)


async def _load_existing_topics(bot) -> None:
    """Load existing topics from the group into cache (faqat bir marta chaqiriladi)."""
    global _topic_cache, _topics_loaded

    if _topics_loaded:
        return

    if not LOG_GROUP_ID:
        _topics_loaded = True
        return

    try:
        # Telegram get_forum_topic_list() ko'pi bilan oxirgi ~100 ta topicni qaytaradi.
        # Shuning uchun natijani to'liq ishonchli deb hisoblab bo'lmaydi —
        # lekin bu bot uchun MAX_TOPICS=50 bo'lgani uchun yetarli.
        topics = await bot.get_forum_topic_list(chat_id=int(LOG_GROUP_ID))

        for topic in topics:
            topic_name = topic.name
            topic_id = topic.message_thread_id
            _topic_cache[topic_name] = topic_id
            print(f"[_load_existing_topics] Loaded: '{topic_name}' (ID: {topic_id})")

        _topics_loaded = True
        print(f"[_load_existing_topics] Jami {len(topics)} ta topic cache'ga yuklandi")
    except Exception as e:
        print(f"[_load_existing_topics xato] {e}")
        # Xato bo'lsa ham _topics_loaded = True qilamiz —
        # aks holda har bir so'rovda qayta urinadi va bot sekinlashadi.
        _topics_loaded = True


async def _get_or_create_topic(bot, subject_combo: str) -> int | None:
    """Get existing topic ID or create new topic for subject combination."""
    global _topic_cache

    if not LOG_GROUP_ID:
        return None

    # 1-qadam: mavjud topiclarni bir marta yukla (bot qayta ishga tushganda ham)
    await _load_existing_topics(bot)

    # 2-qadam: cache'da bor-yo'qligini tekshir
    if subject_combo in _topic_cache:
        print(f"[_get_or_create_topic] Cache'dan topildi: '{subject_combo}' (ID: {_topic_cache[subject_combo]})")
        return _topic_cache[subject_combo]

    # 3-qadam: limit tekshir
    if len(_topic_cache) >= MAX_TOPICS:
        print(f"[_get_or_create_topic] Topic limiti to'ldi ({MAX_TOPICS})")
        return None

    try:
        # 4-qadam: yangi topic yaratishdan OLDIN Telegram'dan qayta qidirish
        # (agar _load_existing_topics API limiti tufayli to'liq kelmagan bo'lsa)
        # Bu "double-check" dublikat yaratilishining oldini oladi.
        fresh_topics = await bot.get_forum_topic_list(chat_id=int(LOG_GROUP_ID))
        for topic in fresh_topics:
            if topic.name not in _topic_cache:
                _topic_cache[topic.name] = topic.message_thread_id
            if topic.name == subject_combo:
                print(f"[_get_or_create_topic] Yangi tekshiruvda topildi: '{subject_combo}' (ID: {topic.message_thread_id})")
                return topic.message_thread_id

        # 5-qadam: haqiqatan ham yo'q — yangi topic yarat
        new_topic = await bot.create_forum_topic(
            chat_id=int(LOG_GROUP_ID),
            name=subject_combo
        )
        topic_id = new_topic.message_thread_id
        _topic_cache[subject_combo] = topic_id
        print(f"[_get_or_create_topic] Yangi topic yaratildi: '{subject_combo}' (ID: {topic_id})")
        return topic_id

    except Exception as e:
        print(f"[_get_or_create_topic xato] {e}")
        return None

# ── Ma'lumot funksiyalari ──────────────────────────────────────────────────────

def record_user(user, query: bool = False) -> None:
    try:
        uid = str(user.id)
        now = datetime.now(tz=timezone(timedelta(hours=5))).strftime("%Y-%m-%d %H:%M:%S")
        sheet = _get_sheet()
        records = _all_records()
        row = _row_num(records, uid)

        if row is None:
            # Yangi foydalanuvchini jadvalning oxiriga qo'shish
            # HEADERS = ["user_id", "first_name", "last_name", "username", "first_seen", "last_seen", "query_count"]
            # Jami 7 ta ustun (A dan G gacha)
            new_row = [
                uid,
                user.first_name or "",
                user.last_name  or "",
                ("@" + user.username) if user.username else "",
                now, now,
                1 if query else 0,
                "FALSE",
            ]
            # append_row o'rniga aniq oxirgi satrni aniqlab yozamiz
            # valid_records dan foydalanib haqiqiy oxirgi satrni topamiz
            valid_recs = [r for r in records if str(r.get("user_id") or "").strip()]
            next_row = len(valid_recs) + 2
            sheet.update(range_name=f"A{next_row}:H{next_row}", values=[new_row])
            try:
                sheet.format(f"H{next_row}", {
                    "backgroundColor": {"red": 0.85, "green": 0.93, "blue": 0.83}
                })
            except Exception:
                pass
        else:
            # Mavjud foydalanuvchini yangilash
            user_map = {}
            for r in records:
                if str(r.get("user_id")):
                    user_map[str(r["user_id"])] = r
            existing = user_map[uid]
            
            try:
                count = int(float(str(existing.get("query_count") or 0)))
            except:
                count = 0
            if query:
                count += 1
                
            # A dan G gacha barcha ustunlarni yangilaymiz (user_id dan query_count gacha)
            is_blocked = existing.get("is_blocked") or "FALSE"
            updated_values = [
                uid,
                user.first_name or "",
                user.last_name  or "",
                ("@" + user.username) if user.username else "",
                existing.get("first_seen", now),
                now,
                count,
                is_blocked,
            ]
            sheet.update(
                range_name=f"A{row}:H{row}",
                values=[updated_values],
            )
    except Exception as e:
        print(f"[record_user xato] {e}")
        return
    try:
        update_stats_sheet()
    except Exception as e:
        print(f"[update_stats_sheet xato] {e}")


def update_user_searched_ids(uid: str, new_search_id: str) -> None:
    try:
        sheet = _get_sheet()
        records = _all_records()
        row = _row_num(records, uid)
        if row is None:
            return
        
        user_map = {str(r.get("user_id")): r for r in records if str(r.get("user_id"))}
        existing = user_map.get(uid, {})
        
        old_1 = str(existing.get("last_id_1") or "").strip()
        old_2 = str(existing.get("last_id_2") or "").strip()
        
        updated_id_1 = str(new_search_id)
        updated_id_2 = old_1 if old_1 else ""
        updated_id_3 = old_2 if old_2 else ""
        
        sheet.update(
            range_name=f"I{row}:K{row}",
            values=[[updated_id_1, updated_id_2, updated_id_3]]
        )
    except Exception as e:
        print(f"[update_user_searched_ids xato] {e}")


def get_stats() -> dict:
    records = _all_records()
    valid_records = [r for r in records if str(r.get("user_id") or "").strip()]
    total_users   = len(valid_records)
    
    def _safe_int(v):
        try: return int(float(str(v or 0)))
        except: return 0

    total_queries = sum(_safe_int(r.get("query_count")) for r in valid_records)

    last_seen = ""
    if valid_records:
        latest = max(valid_records, key=lambda r: str(r.get("last_seen") or ""))
        raw = latest.get("last_seen") or ""
        if raw:
            try:
                dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
                last_seen = dt.strftime("%d.%m.%Y %H:%M")
            except ValueError:
                last_seen = raw

    return {"total_users": total_users, "total_queries": total_queries, "last_seen": last_seen}


def get_users_page(page: int) -> tuple[list, int]:
    records = _all_records()
    valid_records = [r for r in records if str(r.get("user_id") or "").strip()]
    sorted_records = sorted(valid_records, key=lambda r: str(r.get("first_seen") or ""), reverse=True)
    total = len(sorted_records)
    start = (page - 1) * USERS_PER_PAGE
    end   = start + USERS_PER_PAGE
    return sorted_records[start:end], total

# ── Formatlash ────────────────────────────────────────────────────────────────

ED_LANG = {"1": "O'zbek", "2": "Rus", "3": "Qoraqalpoq", "4": "Tojik", "5": "Qozoq"}

def _lang_name(ed_lang_id) -> str | None:
    if not ed_lang_id:
        return None
    return ED_LANG.get(str(ed_lang_id), str(ed_lang_id))


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
        rank      = d["rank"]
        total     = d.get("total_count")
        page_link = d.get("page_link")

        if total:
            pct      = round(rank / total * 100, 1)
            rank_str = f"*{rank}-o'rin* (jami {total} ta abituriyent ichida) — top {pct}%"
        else:
            rank_str = f"*{rank}-o'rin*"

        lines.append(f"\U0001f3c6 Reytingda: {rank_str}")
    subjects = []
    if d.get("s4subject"): subjects.append(escape_md(str(d["s4subject"])))
    if d.get("s5subject"): subjects.append(escape_md(str(d["s5subject"])))
    if subjects:
        lines.append(f"\U0001f4da Fanlar: {' | '.join(subjects)}")
    lang = _lang_name(d.get("ed_lang_id"))
    if lang:
        lines.append(f"\U0001f5e3 Ta'lim tili: *{escape_md(lang)}*")
    lines.append("\n— @mandat\\_applicant\\_ratingbot orqali tekshirildi")
    return "\n".join(lines)

# ── Guruh bildirishnomasi ─────────────────────────────────────────────────────

async def _notify_group(bot, user, queried_id: str, data: dict) -> None:
    """Muvaffaqiyatli so'rovdan keyin log guruhiga xabar yuboradi."""
    if not LOG_GROUP_ID:
        return
    try:
        first     = user.first_name or ""
        last      = user.last_name  or ""
        full_name = (first + " " + last).strip() or "Noma'lum"
        username  = f"@{user.username}" if user.username else "yo'q"
        now       = datetime.now(tz=timezone(timedelta(hours=5))).strftime("%d.%m.%Y %H:%M")

        student_name = _he(str(data.get("name") or "—"))
        score        = _he(str(data.get("score") or "—"))
        pass_status  = _he(str(data.get("pass_status") or "—"))
        status_emoji = "\u2705" if data.get("is_pass") else "\u274c"

        subjects = []
        if data.get("s4subject"): subjects.append(_he(str(data["s4subject"])))
        if data.get("s5subject"): subjects.append(_he(str(data["s5subject"])))
        subjects_str = " | ".join(subjects) if subjects else "—"

        lang_str = _he(_lang_name(data.get("ed_lang_id")) or "—")

        rank_str = ""
        if data.get("rank"):
            rank_str = f"\n\U0001f3c6 O'rin: <b>{data['rank']}-o'rin</b>"

        natija = (
            f"\U0001f464 Abituriyent: {student_name}\n"
            f"\U0001f4ca Ball: <b>{score}</b>\n"
            f"\U0001f4cc Holat: <b>{pass_status}</b> {status_emoji}\n"
            f"\U0001f4da Fanlar: {subjects_str}\n"
            f"\U0001f5e3 Ta'lim tili: <b>{lang_str}</b>"
            f"{rank_str}"
        )
        text = (
            "\U0001f514 <b>Yangi so'rov</b>\n\n"
            f"\U0001f464 Ism: {_he(full_name)}\n"
            f"\U0001f194 Telegram ID: <code>{user.id}</code>\n"
            f"\U0001f4f1 Username: {_he(username)}\n"
            f"\U0001f50d Qidirgan ID: <code>{_he(queried_id)}</code>\n"
            f"\U0001f550 Vaqt: {now}\n"
            f"\n\u2014 <b>Natija</b> \u2014\n"
            f"<tg-spoiler>{natija}</tg-spoiler>"
        )
        
        # Try to use Topics if enabled
        message_thread_id = None
        forum_enabled = await _check_forum_enabled(bot)
        
        if forum_enabled:
            topic_name = _format_topic_name(data.get("s4subject"), data.get("s5subject"))
            if topic_name:
                topic_id = await _get_or_create_topic(bot, topic_name)
                if topic_id:
                    message_thread_id = topic_id
        
        # Send message to topic or general chat
        await bot.send_message(
            chat_id=int(LOG_GROUP_ID), 
            text=text, 
            parse_mode="HTML",
            message_thread_id=message_thread_id
        )
    except Exception as e:
        print(f"[_notify_group xato] {e}")

# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    asyncio.create_task(asyncio.to_thread(record_user, update.effective_user))
    await update.message.reply_text(
        "Assalomu alaykum, hurmatli abituriyent \U0001f44b\n\n"
        "Ushbu bot orqali siz quyidagilarni bilib olishingiz mumkin:\n\n"
        "\U0001f4ca *To'plangan ball*\n"
        "\U0001f4cc *O'tdi yoki o'tmadi* holati\n"
        "\U0001f3c6 *Umumiy o'rin* — tanlangan fanlar bo'yicha barcha abituriyentlar ichida\n"
        "\U0001f465 *Jami abituriyentlar soni* va *foiz* (top %)\n"
        "\U0001f517 *Saytda ko'rish* — mandat.uzbmb.uz da aynan qaysi sahifada ekanligingiz\n\n"
        "Shunchaki 7 xonali abituriyent ID raqamini yuboring.\n\n"
        "Misol:\n`1234567`",
        parse_mode="Markdown",
    )



async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_input = update.message.text.strip()

    if not (user_input.isdigit() and len(user_input) == 7):
        asyncio.create_task(asyncio.to_thread(record_user, update.effective_user))
        await update.message.reply_text(
            "\u26a0\ufe0f Iltimos, *7 xonali* abituriyent ID raqamini kiriting.\n"
            "Misol: `6156306`",
            parse_mode="Markdown",
        )
        return

    uid = str(update.effective_user.id)
    asyncio.create_task(asyncio.to_thread(update_user_searched_ids, uid, user_input))

    asyncio.create_task(asyncio.to_thread(record_user, update.effective_user, True))
    await update.message.reply_text("\U0001f50d Qidirilmoqda... iltimos kuting.")

    result = get_student_data(user_input)
    print(f"[scraper] status={result['status']}")

    if result["status"] == "success":
        d = result["data"]

        reply_markup = None
        if d.get("page_link"):
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("🌐 Saytda ko'rish", url=d["page_link"])
            ]])

        await update.message.reply_text(
            "\u2705 *Natija topildi:*\n\n" + format_result(d),
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )

        asyncio.create_task(_notify_group(context.bot, update.effective_user, user_input, d))

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
                rank    = d["rank"]
                pct     = round(rank / total * 100, 1)
                yuqori  = rank - 1
                past    = total - rank
                await counting_msg.edit_text(
                    f"\U0001f4ca Jami *{total}* ta abituriyent ichida "
                    f"*{rank}*-o'rin (top *{pct}%*)\n\n"
                    f"\U0001f53c Sizdan yuqori: *{yuqori}* ta abituriyent\n"
                    f"\U0001f53d Sizdan past: *{past}* ta abituriyent\n\n"
                    "— @mandat\\_applicant\\_ratingbot orqali tekshirildi",
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
    is_admin = "Ha \u2705" if uid == ADMIN_ID else "Yo'q \u274c"
    await update.message.reply_text(
        f"Sizning ID: `{uid}`\n"
        f"ADMIN\\_ID: `{ADMIN_ID}`\n"
        f"Admin: *{is_admin}*",
        parse_mode="Markdown",
    )


async def admin_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("\u26d4 Ruxsat yo'q.")
        return

    if not context.args:
        await update.message.reply_text(
            "\U0001f464 *Foydalanish:*\n`/user <telegram_id>`\n\nMisol: `/user 123456789`",
            parse_mode="Markdown",
        )
        return

    target_uid = context.args[0].strip()
    if not target_uid.isdigit():
        await update.message.reply_text(
            "\u26a0\ufe0f Telegram ID faqat raqamlardan iborat bo'lishi kerak.",
        )
        return

    try:
        records = await asyncio.to_thread(_all_records)
    except Exception as e:
        await update.message.reply_text(f"\u274c *Sheets xatosi:*\n`{e}`", parse_mode="Markdown")
        return

    rec = next((r for r in records if str(r.get("user_id")) == target_uid), None)

    if rec is None:
        await update.message.reply_text(
            f"\u274c <code>{_he(target_uid)}</code> ID li foydalanuvchi topilmadi.",
            parse_mode="HTML",
        )
        return

    first  = rec.get("first_name", "") or ""
    last_n = rec.get("last_name",  "") or ""
    full_name    = _he((first + " " + last_n).strip() or "Noma'lum")
    username_str = _he(rec.get("username") or "yo'q")
    try:
        query_count = int(float(str(rec.get("query_count") or 0)))
    except:
        query_count = 0

    def fmt_dt(raw):
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            return _he(raw) if raw else "—"

    first_seen = fmt_dt(rec.get("first_seen", ""))
    last_seen  = fmt_dt(rec.get("last_seen",  ""))

    await update.message.reply_text(
        f"\U0001f464 <b>Foydalanuvchi ma'lumotlari</b>\n\n"
        f"\U0001f194 Telegram ID: <code>{_he(target_uid)}</code>\n"
        f"\U0001f9d1 Ism: {full_name}\n"
        f"\U0001f4f1 Username: {username_str}\n"
        f"\U0001f50d Jami so'rovlar: <b>{query_count}</b> ta\n"
        f"\U0001f4c5 Birinchi kirish: {first_seen}\n"
        f"\U0001f550 So'nggi faollik: {last_seen}",
        parse_mode="HTML",
    )


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    print(f"[/stats] user_id={uid}, ADMIN_ID={ADMIN_ID}, match={uid == ADMIN_ID}")
    if uid != ADMIN_ID:
        await update.message.reply_text("\u26d4 Ruxsat yo'q.")
        return

    try:
        s = await asyncio.to_thread(get_stats)
        last = _he(s["last_seen"] if s["last_seen"] else "Hali yo'q")
        text = (
            "\U0001f4ca <b>Statistika</b>\n\n"
            f"\U0001f465 Jami foydalanuvchilar: <b>{s['total_users']}</b>\n"
            f"\U0001f50d Jami so'rovlar: <b>{s['total_queries']}</b>\n"
            f"\U0001f550 So'nggi faollik: <b>{last}</b>"
        )
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(
            f"\u274c <b>Sheets xatosi:</b>\n<code>{_he(str(e))}</code>",
            parse_mode="HTML",
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
        try:
            count = int(float(str(u.get("query_count") or 0)))
        except:
            count = 0

        raw_last = u.get("last_seen") or ""
        try:
            dt = datetime.strptime(str(raw_last), "%Y-%m-%d %H:%M:%S")
            last_date = dt.strftime("%d.%m.%Y")
        except (ValueError, TypeError):
            last_date = (str(raw_last)[:10] if raw_last else "\u2014")

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
                await context.bot.send_message(chat_id=int(uid), text=text, parse_mode="Markdown")
            except Exception as md_err:
                if "can't parse" in str(md_err).lower():
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

        if i % 10 == 0 or i == total:
            try:
                await status_msg.edit_text(f"\U0001f4e4 Yuborilmoqda... {i}/{total}")
            except Exception:
                pass

        await asyncio.sleep(1)

    # Broadcast tugadi — bloklangan foydalanuvchilarni batch qilib yangilash
    if blocked:
        try:
            sheet = _get_sheet()
            recs  = _all_records()
            red_fmt = {
                "backgroundColor": {
                    "red": 0.96, "green": 0.80, "blue": 0.80
                }
            }
            for uid in blocked:
                row = _row_num(recs, uid)
                if row:
                    sheet.update(range_name=f"H{row}", values=[["TRUE"]])
                    sheet.format(f"H{row}", red_fmt)
        except Exception as e:
            print(f"[batch is_blocked xato] {e}")

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
    application.add_handler(CommandHandler("start",     start))
    application.add_handler(CommandHandler("whoami",    whoami))
    application.add_handler(CommandHandler("user",      admin_user))
    application.add_handler(CommandHandler("stats",     admin_stats))
    application.add_handler(CommandHandler("users",     admin_users))
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
