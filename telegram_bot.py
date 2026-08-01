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
