import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from scraper import get_student_data

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


def format_result(d: dict) -> str:
    status_emoji = "✅" if d["is_pass"] else "❌"

    lines = [
        f"👤 *{d['name']}*",
        f"🆔 ID: `{d['id']}`",
        f"📊 Ball: *{d['score']}*",
        f"📌 Holat: *{d['pass_status']}* {status_emoji}",
    ]

    if d.get("rank"):
        lines.append(f"🏆 Umumiy o'rin: *{d['rank']}-o'rin*")

    subjects = []
    if d.get("s4subject"):
        subjects.append(d["s4subject"])
    if d.get("s5subject"):
        subjects.append(d["s5subject"])
    if subjects:
        lines.append(f"📚 Fanlar: {' | '.join(subjects)}")

    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Assalomu alaykum!\n\n"
        "Men *MandatRating Bot*.\n"
        "7 xonali abituriyent *ID raqamini* yuboring —\n"
        "ball, holat va umumiy o'rinni topib beraman.\n\n"
        "Misol: `6156306`",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *Foydalanish:*\n\n"
        "7 xonali abituriyent ID raqamini yuboring.\n"
        "Misol: `6156306`\n\n"
        "*Bot sizga ko'rsatadi:*\n"
        "• 👤 Ism\n"
        "• 📊 To'plangan ball\n"
        "• 📌 O'tdi / O'tmadi\n"
        "• 🏆 Umumiy o'rin\n"
        "• 📚 Fanlar",
        parse_mode="Markdown"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_input = update.message.text.strip()

    if not (user_input.isdigit() and len(user_input) == 7):
        await update.message.reply_text(
            "⚠️ Iltimos, *7 xonali* abituriyent ID raqamini kiriting.\n"
            "Misol: `6156306`",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text("🔍 Qidirilmoqda... iltimos kuting.")

    result = get_student_data(user_input)

    if result["status"] == "success":
        await update.message.reply_text(
            "✅ *Natija topildi:*\n\n" + format_result(result["data"]),
            parse_mode="Markdown"
        )
    elif result["status"] == "not_found":
        await update.message.reply_text(
            "❌ Ushbu ID bo'yicha ma'lumot topilmadi.\n"
            "ID raqam to'g'ri ekanligini tekshiring."
        )
    else:
        await update.message.reply_text(
            f"⚠️ Xatolik yuz berdi:\n`{result['message']}`",
            parse_mode="Markdown"
        )


def main() -> None:
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot ishga tushdi...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
