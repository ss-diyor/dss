import os
import json
import asyncio
import threading
import time
from pathlib import Path
from flask import Flask, jsonify, send_from_directory
from waitress import serve
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import gspread
from google.oauth2.service_account import Credentials
from scraper import get_student_data, get_total_count
from mandat_monitor import manual_check_report, monitor_site

TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

SHEET_ID                = os.getenv("SHEET_ID", "")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
LOG_GROUP_ID            = os.getenv("LOG_GROUP_ID", "")

SCOPES  = ["https://www.googleapis.com/auth/spreadsheets"]
HEADERS = ["user_id", "first_name", "last_name", "username",
           "first_seen", "last_seen", "query_count", "is_blocked",
           "last_id_1", "last_id_2", "last_id_3", "saved_ids"]

USERS_PER_PAGE = 15

# ── Mini statistics website ───────────────────────────────────────────────────

WEB_DIR = Path(__file__).resolve().parent
web_app = Flask(__name__, static_folder=None)
_stats_api_cache = {"data": None, "ts": 0.0}
STATS_API_CACHE_TTL = 60


def _stats_history_payload() -> dict:
    """Statistics worksheet ma'lumotlarini public dashboard uchun tayyorlaydi."""
    now = time.time()
    cached = _stats_api_cache.get("data")
    if cached is not None and now - float(_stats_api_cache.get("ts", 0)) < STATS_API_CACHE_TTL:
        return cached

    ws = _get_stats_sheet()
    records = ws.get_all_records()

    def safe_int(value):
        try:
            return int(float(str(value or 0)))
        except (TypeError, ValueError):
            return 0

    history = []
    for row in records:
        date = str(row.get("Sana") or "").strip()
        if not date:
            continue
        history.append({
            "date": date,
            "totalUsers": safe_int(row.get("Jami foydalanuvchilar")),
            "totalQueries": safe_int(row.get("Jami so'rovlar")),
            "todayQueries": safe_int(row.get("Bugungi so'rovlar")),
            "newUsers": safe_int(row.get("Bugungi yangi foydalanuvchilar")),
        })

    latest = history[-1] if history else {
        "date": "—",
        "totalUsers": 0,
        "totalQueries": 0,
        "todayQueries": 0,
        "newUsers": 0,
    }

    payload = {
        "ok": True,
        "latest": latest,
        "history": history[-60:],
        "updatedAt": datetime.now(tz=timezone(timedelta(hours=5))).isoformat(),
    }
    _stats_api_cache["data"] = payload
    _stats_api_cache["ts"] = now
    return payload


@web_app.get("/")
def dashboard():
    return send_from_directory(WEB_DIR, "index.html")


@web_app.get("/api/stats")
def api_stats():
    try:
        response = jsonify(_stats_history_payload())
        response.headers["Cache-Control"] = "public, max-age=30"
        return response
    except Exception as e:
        return jsonify({"ok": False, "error": "Statistikani yuklab bo'lmadi"}), 500


@web_app.get("/health")
def health():
    return jsonify({"ok": True, "service": "telegram-bot-dashboard"})


def run_web_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    serve(web_app, host="0.0.0.0", port=port, threads=4)


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
        
        # Har doim F1 sarlavhasini majburiy yangilash
        ws.update(range_name="A1:F1", values=[new_headers])
        
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
            sheet.update(range_name=f"A{next_row}:L{next_row}", values=[new_row + ["", "", "", ""]])
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


def _admission_label(data: dict) -> str:
    labels = {
        "grant": "Davlat granti",
        "contract": "To‘lov-kontrakt",
        "accepted": "Qabul qilindi",
        "not_recommended": "Talabalikka tavsiya etilmadi",
    }
    return labels.get(str(data.get("result_status") or ""), str(data.get("pass_status") or "Noma'lum"))


def format_result(d: dict) -> str:
    status_emoji = "✅" if d.get("is_pass") else "❌"
    name = escape_md(str(d.get("name") or ""))
    lines = [
        f"👤 *{name}*",
        f"🆔 ID: `{d['id']}`",
        f"📊 Ball: *{escape_md(str(d.get('score') or '—'))}*",
        f"📌 Holat: *{escape_md(_admission_label(d))}* {status_emoji}",
    ]
    accepted = d.get("accepted_choice") or {}
    if accepted:
        lines.extend([
            f"🏛 OTM: *{escape_md(str(accepted.get('university') or '—'))}*",
            f"📚 Yo‘nalish: *{escape_md(str(accepted.get('direction') or '—'))}*",
            f"🕒 Ta’lim shakli: *{escape_md(str(accepted.get('education_form') or '—'))}*",
            f"🎓 Qabul turi: *{escape_md(str(accepted.get('status_text') or _admission_label(d)))}*",
        ])
    choices = d.get("choices") or []
    if choices:
        lines.append("\n📋 *Tanlangan yo‘nalishlar:* ")
        for choice in choices[:5]:
            status = choice.get("status")
            mark = "✅" if status in {"grant", "contract", "accepted"} else "▫️"
            label = "Grant" if status == "grant" else "Kontrakt" if status == "contract" else "Qabul qilinmadi" if status == "not_recommended" else "Tanlov"
            lines.append(f"{mark} {escape_md(str(choice.get('priority') or ''))}: {escape_md(str(choice.get('university') or ''))} — {label}")
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
        pass_status  = _he(_admission_label(data))
        status_emoji = "✅" if data.get("is_pass") else "❌"
        accepted = data.get("accepted_choice") or {}

        subjects = []
        if data.get("s4subject"): subjects.append(_he(str(data["s4subject"])))
        if data.get("s5subject"): subjects.append(_he(str(data["s5subject"])))
        subjects_str = " | ".join(subjects) if subjects else "—"

        lang_str = _he(_lang_name(data.get("ed_lang_id")) or "—")

        rank_str = ""
        if data.get("rank"):
            rank_str = f"\n\U0001f3c6 O'rin: <b>{data['rank']}-o'rin</b>"

        admission_lines = ""
        if accepted:
            admission_lines = (
                f"\n\U0001f3db OTM: <b>{_he(str(accepted.get('university') or '—'))}</b>"
                f"\n\U0001f4da Yo'nalish: <b>{_he(str(accepted.get('direction') or '—'))}</b>"
                f"\n\U0001f393 Qabul turi: <b>{_he(str(accepted.get('status_text') or _admission_label(data)))}</b>"
            )
        natija = (
            f"\U0001f464 Abituriyent: {student_name}\n"
            f"\U0001f4ca Ball: <b>{score}</b>\n"
            f"\U0001f4cc Holat: <b>{pass_status}</b> {status_emoji}"
            f"{admission_lines}\n"
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

from telegram import ReplyKeyboardMarkup, KeyboardButton

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    asyncio.create_task(asyncio.to_thread(record_user, update.effective_user))
    
    reply_markup = ReplyKeyboardMarkup(
        [
            [KeyboardButton("🕒 Oxirgi qidiruvlar"), KeyboardButton("⭐ Saqlangan ID lar")]
        ],
        resize_keyboard=True
    )

    await update.message.reply_text(
        "Assalomu alaykum, hurmatli abituriyent 👋\n\n"
        "Ushbu bot orqali siz quyidagilarni bilib olishingiz mumkin:\n\n"
        "📊 *To'plangan ball*\n"
        "📌 *O'tdi yoki o'tmadi* holati\n"
        "🏆 *Umumiy o'rin* — tanlangan fanlar bo'yicha barcha abituriyentlar ichida\n"
        "👥 *Jami abituriyentlar soni* va *foiz* (top %)\n"
        "🔗 *Saytda ko'rish* — mandat.uzbmb.uz da aynan qaysi sahifada ekanligingiz\n\n"
        "Shunchaki 7 xonali abituriyent ID raqamini yuboring yoki pastdagi tugmalardan foydalaning.",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )



SEARCH_TIMEOUT = max(5, int(os.getenv("SEARCH_TIMEOUT", "15")))
SEARCH_CACHE_TTL = max(30, int(os.getenv("SEARCH_CACHE_TTL", "600")))
_student_cache: dict[str, tuple[float, dict]] = {}


async def get_student_data_fast(entrant_id: str) -> dict:
    """Cache, timeout va alohida thread bilan qidiruvni bot event-loop'ini bloklamasdan bajaradi."""
    key = str(entrant_id).strip()
    now = time.monotonic()
    cached = _student_cache.get(key)
    if cached and now - cached[0] < SEARCH_CACHE_TTL:
        return cached[1]
    if cached:
        _student_cache.pop(key, None)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(get_student_data, key),
            timeout=SEARCH_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {"status": "error", "message": "Sayt javobi juda kechikdi. Iltimos, birozdan so'ng qayta urinib ko'ring."}
    except Exception as error:
        return {"status": "error", "message": str(error)}

    if result.get("status") in {"success", "not_found"}:
        _student_cache[key] = (time.monotonic(), result)
        if len(_student_cache) > 2000:
            oldest_key = min(_student_cache, key=lambda item: _student_cache[item][0])
            _student_cache.pop(oldest_key, None)
    return result


def get_user_recent_searches(uid: str) -> list[str]:
    try:
        records = _all_records()
        user_map = {str(r.get("user_id")): r for r in records if str(r.get("user_id"))}
        existing = user_map.get(uid, {})
        ids = []
        for key in ["last_id_1", "last_id_2", "last_id_3"]:
            val = str(existing.get(key) or "").strip()
            if val and val.isdigit() and len(val) == 7:
                ids.append(val)
        return ids
    except Exception as e:
        print(f"[get_user_recent_searches xato] {e}")
        return []

def get_user_saved_items(uid: str) -> list[dict]:
    try:
        records = _all_records()
        user_map = {str(r.get("user_id")): r for r in records if str(r.get("user_id"))}
        existing = user_map.get(uid, {})
        saved_str = str(existing.get("saved_ids") or "").strip()
        if not saved_str:
            return []
        items = []
        for part in saved_str.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                i_id, i_name = part.split(":", 1)
                i_id = i_id.strip()
                i_name = i_name.strip()
                if i_id.isdigit() and len(i_id) == 7:
                    items.append({"id": i_id, "name": i_name})
            else:
                if part.isdigit() and len(part) == 7:
                    items.append({"id": part, "name": part})
        return items
    except Exception as e:
        print(f"[get_user_saved_items xato] {e}")
        return []

def add_user_saved_item(uid: str, new_id: str, name: str) -> list[dict]:
    try:
        sheet = _get_sheet()
        records = _all_records()
        row = _row_num(records, uid)
        if row is None:
            return []
        user_map = {str(r.get("user_id")): r for r in records if str(r.get("user_id"))}
        existing = user_map.get(uid, {})
        saved_str = str(existing.get("saved_ids") or "").strip()
        
        items = []
        for part in saved_str.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                i_id, i_name = part.split(":", 1)
                i_id = i_id.strip()
                i_name = i_name.strip()
                if i_id.isdigit() and len(i_id) == 7:
                    items.append({"id": i_id, "name": i_name})
            else:
                if part.isdigit() and len(part) == 7:
                    items.append({"id": part, "name": part})
        
        items = [i for i in items if i["id"] != new_id]
        items.insert(0, {"id": new_id, "name": name})
        items = items[:5]
        
        new_saved_str = ",".join(f"{i['id']}:{i['name']}" for i in items)
        sheet.update(range_name=f"L{row}", values=[[new_saved_str]])
        return items
    except Exception as e:
        print(f"[add_user_saved_item xato] {e}")
        return []

def remove_user_saved_item(uid: str, target_id: str) -> list[dict]:
    try:
        sheet = _get_sheet()
        records = _all_records()
        row = _row_num(records, uid)
        if row is None:
            return []
        user_map = {str(r.get("user_id")): r for r in records if str(r.get("user_id"))}
        existing = user_map.get(uid, {})
        saved_str = str(existing.get("saved_ids") or "").strip()
        
        items = []
        for part in saved_str.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                i_id, i_name = part.split(":", 1)
                i_id = i_id.strip()
                i_name = i_name.strip()
                if i_id.isdigit() and len(i_id) == 7:
                    items.append({"id": i_id, "name": i_name})
            else:
                if part.isdigit() and len(part) == 7:
                    items.append({"id": part, "name": part})
        
        items = [i for i in items if i["id"] != target_id]
        new_saved_str = ",".join(f"{i['id']}:{i['name']}" for i in items)
        sheet.update(range_name=f"L{row}", values=[[new_saved_str]])
        return items
    except Exception as e:
        print(f"[remove_user_saved_item xato] {e}")
        return []

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_input = update.message.text.strip()
    uid = str(update.effective_user.id)

    pending_id = context.user_data.get("pending_save_id")
    if pending_id:
        context.user_data["pending_save_id"] = None
        custom_name = user_input
        saved_items = await asyncio.to_thread(add_user_saved_item, uid, pending_id, custom_name)
        await update.message.reply_text(
            f"⭐ ID <code>{pending_id}</code> («<b>{_he(custom_name)}</b>») saqlanganlarga qo'shildi!\n"
            f"Jami saqlanganlar: {len(saved_items)} ta.",
            parse_mode="HTML"
        )
        return

    if user_input == "🕒 Oxirgi qidiruvlar":
        recent = await asyncio.to_thread(get_user_recent_searches, uid)
        if not recent:
            await update.message.reply_text("Sizda hali qidiruvlar tarixi mavjud emas. 7 xonali ID yuborib qidiruvni boshlang.")
            return
        buttons = [[InlineKeyboardButton(f"🔍 {i}", callback_data=f"search_{i}")] for i in recent]
        await update.message.reply_text(
            "🕒 *Sizning oxirgi qidirgan ID laringiz:*\nQuyidagi ID lardan birini tanlab qayta tekshirishingiz mumkin:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if user_input == "⭐ Saqlangan ID lar":
        saved_items = await asyncio.to_thread(get_user_saved_items, uid)
        if not saved_items:
            await update.message.reply_text("⭐ Sizda saqlangan ID lar yo'q.\nID natijasi chiqqandan so'ng '⭐ Bu ID ni saqlash' tugmasi orqali saqlab qo'yishingiz mumkin.")
            return
        buttons = [
            [
                InlineKeyboardButton(f"⭐ {item['name']} ({item['id']})", callback_data=f"search_{item['id']}"),
                InlineKeyboardButton("❌ O'chirish", callback_data=f"unsave_{item['id']}")
            ] 
            for item in saved_items
        ]
        await update.message.reply_text(
            "⭐ *Sizning saqlangan ID laringiz:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if not (user_input.isdigit() and len(user_input) == 7):
        asyncio.create_task(asyncio.to_thread(record_user, update.effective_user))
        await update.message.reply_text(
            "⚠️ Iltimos, *7 xonali* abituriyent ID raqamini kiriting.\n"
            "Misol: `6156306`",
            parse_mode="Markdown",
        )
        return

    uid = str(update.effective_user.id)
    asyncio.create_task(asyncio.to_thread(update_user_searched_ids, uid, user_input))

    asyncio.create_task(asyncio.to_thread(record_user, update.effective_user, True))
    status_msg = await update.message.reply_text("\U0001f50d Qidirilmoqda... iltimos kuting.")

    result = await get_student_data_fast(user_input)
    print(f"[scraper] status={result['status']}")

    if result["status"] == "success":
        d = result["data"]

        buttons = []
        if d.get("page_link"):
            buttons.append([InlineKeyboardButton("🌐 Saytda ko'rish", url=d["page_link"])])
        buttons.append([InlineKeyboardButton("⭐ Bu ID ni saqlash", callback_data=f"save_{user_input}")])
        reply_markup = InlineKeyboardMarkup(buttons)

        try:
            await status_msg.edit_text(
                "\u2705 *Natija topildi:*\n\n" + format_result(d),
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )
        except Exception:
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
        await status_msg.edit_text(
            "\u274c Ushbu ID bo'yicha ma'lumot topilmadi.\n"
            "ID raqam to'g'ri ekanligini tekshiring."
        )
    else:
        print(f"[scraper xato] {result.get('message')}")
        await status_msg.edit_text(
            f"\u26a0\ufe0f Xatolik yuz berdi:\n{result['message']}"
        )

# ── Admin handlers ────────────────────────────────────────────────────────────

async def admin_monitor_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sayt monitoringini admin buyrug'i bilan darhol tekshiradi."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Ruxsat yo'q.")
        return

    status_msg = await update.message.reply_text("🔎 Mandat sayti tekshirilmoqda...")
    try:
        report = await asyncio.to_thread(manual_check_report)
        await status_msg.edit_text(report, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as error:
        await status_msg.edit_text(
            f"⚠️ Manual check xatosi:\n<code>{_he(str(error))}</code>",
            parse_mode="HTML",
        )


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


async def admin_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Ruxsat yo'q.")
        return

    status_msg = await update.message.reply_text("📥 Excel fayl tayyorlanmoqda... iltimos kuting.")

    try:
        import pandas as pd
        import openpyxl

        def generate_excel():
            records = _all_records()
            df = pd.DataFrame(records)
            
            # Statistics sheet records if available
            try:
                stats_ws = _get_stats_sheet()
                stats_records = stats_ws.get_all_records()
                df_stats = pd.DataFrame(stats_records)
            except Exception:
                df_stats = pd.DataFrame()

            file_path = "/tmp/dss_users_export.xlsx"
            with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Foydalanuvchilar", index=False)
                if not df_stats.empty:
                    df_stats.to_excel(writer, sheet_name="Statistika", index=False)
            return file_path

        file_path = await asyncio.to_thread(generate_excel)
        
        with open(file_path, "rb") as doc:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=doc,
                filename=f"bot_bazasi_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
                caption="📊 *Bot bazasi va statistikasi Excel formatida tayyor!*",
                parse_mode="Markdown"
            )
        await status_msg.delete()
    except Exception as e:
        await status_msg.edit_text(f"❌ Xatolik yuz berdi:\n`{e}`", parse_mode="Markdown")


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


BROADCAST_WORKERS = max(1, int(os.getenv("BROADCAST_WORKERS", "20")))
BROADCAST_RATE = max(1.0, float(os.getenv("BROADCAST_RATE", "20")))
BROADCAST_RETRIES = max(0, int(os.getenv("BROADCAST_RETRIES", "3")))
_broadcast_lock = asyncio.Lock()


def _broadcast_is_blocked(error: Exception) -> bool:
    """Telegram foydalanuvchisi botni bloklagan yoki akkaunt yopilganini aniqlaydi."""
    error_name = type(error).__name__.lower()
    error_text = str(error).lower()
    return error_name in {"forbidden", "chatnotfound"} or any(
        marker in error_text
        for marker in ("forbidden", "bot was blocked", "user is deactivated", "kicked", "chat not found")
    )


class _BroadcastRateLimiter:
    """Barcha workerlar uchun umumiy, yumshoq Telegram API rate-limit."""

    def __init__(self, rate: float) -> None:
        self.interval = 1.0 / rate
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait_for = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.interval
        if wait_for:
            await asyncio.sleep(wait_for)


async def _broadcast_send(bot, uid: str, text: str, limiter: _BroadcastRateLimiter) -> str:
    """Bitta xabarni yuboradi; parse, transient error va RetryAfter holatlarini boshqaradi."""
    for attempt in range(BROADCAST_RETRIES + 1):
        try:
            await limiter.acquire()
            await bot.send_message(chat_id=int(uid), text=text, parse_mode="Markdown")
            return "success"
        except Exception as error:
            if "can't parse" in str(error).lower() or "parse entities" in str(error).lower():
                try:
                    await limiter.acquire()
                    await bot.send_message(chat_id=int(uid), text=text)
                    return "success"
                except Exception as fallback_error:
                    error = fallback_error

            if _broadcast_is_blocked(error):
                return "blocked"
            if attempt >= BROADCAST_RETRIES:
                return "failed"

            retry_after = getattr(error, "retry_after", None)
            if retry_after is None:
                retry_after = min(8.0, 0.5 * (2 ** attempt))
            await asyncio.sleep(max(0.5, float(retry_after)))
    return "failed"


async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Ruxsat yo'q.")
        return

    if _broadcast_lock.locked():
        await update.message.reply_text("⏳ Hozir boshqa broadcast davom etmoqda. Iltimos, tugashini kuting.")
        return

    raw = update.message.text or ""
    text = raw.split(None, 1)[1].strip() if len(raw.split(None, 1)) > 1 else ""
    if not text:
        await update.message.reply_text(
            "📢 *Broadcast foydalanish:*\n`/broadcast <xabar matni>`\n\n"
            "Markdown qo'llab-quvvatlanadi: `*qalin*`, `_kursiv_`, `` `kod` ``",
            parse_mode="Markdown",
        )
        return
    if len(text) > 4096:
        await update.message.reply_text("❌ Xabar 4096 belgidan oshmasligi kerak.")
        return

    try:
        records = await asyncio.to_thread(_all_records)
    except Exception as error:
        await update.message.reply_text(f"❌ Sheets xatosi:\n`{escape_md(error)}`", parse_mode="Markdown")
        return

    user_ids = []
    seen = set()
    for record in records:
        uid = str(record.get("user_id") or "").strip()
        if uid and uid not in seen:
            try:
                int(uid)
                user_ids.append(uid)
                seen.add(uid)
            except ValueError:
                continue

    if not user_ids:
        await update.message.reply_text("Hali foydalanuvchilar yo'q.")
        return

    total = len(user_ids)
    await _broadcast_lock.acquire()
    try:
        status_msg = await update.message.reply_text(f"📤 Yuborilmoqda... 0/{total}")
    except Exception:
        _broadcast_lock.release()
        raise
    queue = asyncio.Queue()
    for uid in user_ids:
        await queue.put(uid)

    limiter = _BroadcastRateLimiter(BROADCAST_RATE)
    progress_lock = asyncio.Lock()
    progress = {"done": 0, "success": 0, "blocked": [], "failed": []}
    last_status = {"at": 0.0}

    async def worker() -> None:
        while True:
            try:
                uid = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                result = await _broadcast_send(context.bot, uid, text, limiter)
                async with progress_lock:
                    progress["done"] += 1
                    if result == "success":
                        progress["success"] += 1
                    elif result == "blocked":
                        progress["blocked"].append(uid)
                    else:
                        progress["failed"].append(uid)
                    now = time.monotonic()
                    if progress["done"] == total or now - last_status["at"] >= 5:
                        last_status["at"] = now
                        try:
                            await status_msg.edit_text(
                                f"📤 Yuborilmoqda... {progress['done']}/{total}"
                            )
                        except Exception:
                            pass
            finally:
                queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(min(BROADCAST_WORKERS, total))]
    await asyncio.gather(*workers)
    success = progress["success"]
    blocked = progress["blocked"]
    failed = progress["failed"]

    # Google Sheets'ga bloklanganlar ro'yxatini bitta batch request bilan yozamiz.
    if blocked:
        try:
            sheet = _get_sheet()
            recs = _all_records()
            updates = [
                {"range": f"H{row}", "values": [["TRUE"]]}
                for uid in blocked
                if (row := _row_num(recs, uid))
            ]
            if updates:
                await asyncio.to_thread(sheet.batch_update, updates)
        except Exception as error:
            print(f"[broadcast is_blocked batch xato] {error}")

    lines = ["📢 *Broadcast yakunlandi*\n", f"✅ Muvaffaqiyatli: *{success}* ta"]
    if blocked:
        lines.append(f"🚫 Bot blok qilingan: *{len(blocked)}* ta")
        preview = ", ".join(f"`{u}`" for u in blocked[:20])
        if len(blocked) > 20:
            preview += f" ... va yana {len(blocked) - 20} ta"
        lines.append(f"   {preview}")
    if failed:
        lines.append(f"⚠️ Qayta urinishlardan keyin xato: *{len(failed)}* ta")
        preview = ", ".join(f"`{u}`" for u in failed[:10])
        if len(failed) > 10:
            preview += f" ... va yana {len(failed) - 10} ta"
        lines.append(f"   {preview}")
    try:
        await status_msg.edit_text("\n".join(lines), parse_mode="Markdown")
    except Exception:
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    finally:
        _broadcast_lock.release()


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    uid = str(query.from_user.id)

    if data.startswith("search_"):
        target_id = data.split("_")[1]
        loading_msg = await query.message.reply_text(f"🔍 <b>{target_id}</b> qidirilmoqda...", parse_mode="HTML")
        
        asyncio.create_task(asyncio.to_thread(update_user_searched_ids, uid, target_id))
        asyncio.create_task(asyncio.to_thread(record_user, query.from_user, True))

        res = await get_student_data_fast(target_id)
        if res["status"] == "success":
            d = res["data"]
            buttons = []
            if d.get("page_link"):
                buttons.append([InlineKeyboardButton("🌐 Saytda ko'rish", url=d["page_link"])])
            buttons.append([InlineKeyboardButton("⭐ Bu ID ni saqlash", callback_data=f"save_{target_id}")])
            await loading_msg.edit_text(
                "✅ *Natija topildi:*\n\n" + format_result(d),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            asyncio.create_task(_notify_group(context.bot, query.from_user, target_id, d))
        else:
            await loading_msg.edit_text("❌ Ushbu ID bo'yicha ma'lumot topilmadi.")

    elif data.startswith("save_"):
        target_id = data.split("_")[1]
        context.user_data["pending_save_id"] = target_id
        await query.message.reply_text(
            f"👤 ID <code>{target_id}</code> kimga tegishli?\n"
            f"Iltimos, ushbu ID uchun ism/nom kiriting (masalan: <i>O'zim</i>, <i>Anvar</i>, <i>Akam</i>):",
            parse_mode="HTML"
        )

    elif data.startswith("unsave_"):
        target_id = data.split("_")[1]
        saved_items = await asyncio.to_thread(remove_user_saved_item, uid, target_id)
        await query.message.reply_text(f"❌ ID <code>{target_id}</code> saqlanganlardan o'chirildi.", parse_mode="HTML")

# ── main ──────────────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    import os
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        print("[CONFLICT] Boshqa bot instance ishlayapti — process to'xtatildi.")
        os._exit(1)
    print(f"[Xato] {context.error}")


_mandat_monitor_task: asyncio.Task | None = None


async def post_init(application: Application) -> None:
    """Bot ishga tushganda sayt monitoringini fon rejimida boshlaydi."""
    global _mandat_monitor_task
    _mandat_monitor_task = asyncio.create_task(
        monitor_site(application.bot),
        name="mandat-site-monitor",
    )


async def post_shutdown(application: Application) -> None:
    """Bot to'xtaganda monitoring taskini tartibli yopadi."""
    global _mandat_monitor_task
    if _mandat_monitor_task is None or _mandat_monitor_task.done():
        return
    _mandat_monitor_task.cancel()
    try:
        await _mandat_monitor_task
    except asyncio.CancelledError:
        pass
    finally:
        _mandat_monitor_task = None


def main() -> None:
    web_thread = threading.Thread(target=run_web_server, name="stats-web", daemon=True)
    web_thread.start()
    print(f"Web dashboard ishga tushdi: PORT={os.getenv('PORT', '10000')}")

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start",     start))
    application.add_handler(CommandHandler("whoami",    whoami))
    application.add_handler(CommandHandler("user",      admin_user))
    application.add_handler(CommandHandler("stats",     admin_stats))
    application.add_handler(CommandHandler("monitor_check", admin_monitor_check))
    application.add_handler(CommandHandler("users",     admin_users))
    application.add_handler(CommandHandler("export",    admin_export))
    application.add_handler(CommandHandler("broadcast", admin_broadcast))
    application.add_handler(CallbackQueryHandler(callback_query_handler))
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
