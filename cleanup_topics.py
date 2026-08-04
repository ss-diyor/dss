"""
cleanup_topics.py — Guruhdagi bir xil nomdagi (dublikat) Topic larni tozalash.

Ishlatish:
    TELEGRAM_BOT_TOKEN=... LOG_GROUP_ID=... python3 cleanup_topics.py

Nima qiladi:
  - Guruhdagi barcha topiclarni oladi
  - Bir xil nomli topiclarni aniqlaydi
  - Har bir nomdan faqat BITTA (eng kattasi — ya'ni eng eski, ID jihatidan) qoldiradi
  - Qolganlarini o'chiradi (close + delete)
  - Hech narsani o'zgartirmasdan faqat ko'rish uchun --dry-run rejimi mavjud
"""

import os
import asyncio
import argparse
from collections import defaultdict
from telegram import Bot
from telegram.error import TelegramError


TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN", "")
LOG_GROUP_ID = os.getenv("LOG_GROUP_ID", "")


async def fetch_all_topics(bot: Bot, chat_id: int) -> list:
    """Guruhdan barcha topiclarni oladi."""
    try:
        topics = await bot.get_forum_topic_list(chat_id=chat_id)
        return list(topics)
    except TelegramError as e:
        print(f"[XATO] Topiclarni olishda muammo: {e}")
        return []


async def cleanup(dry_run: bool = False) -> None:
    if not TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN muhit o'zgaruvchisi topilmadi.")
        return
    if not LOG_GROUP_ID:
        print("❌ LOG_GROUP_ID muhit o'zgaruvchisi topilmadi.")
        return

    chat_id = int(LOG_GROUP_ID)
    bot = Bot(token=TOKEN)

    print(f"📋 Guruh: {chat_id}")
    print(f"{'🔍 DRY-RUN rejimi — hech narsa o`chirilmaydi' if dry_run else '🗑  HAQIQIY rejim — dublikatlar o`chiriladi'}\n")

    # 1. Barcha topiclarni yukla
    topics = await fetch_all_topics(bot, chat_id)
    if not topics:
        print("⚠️  Guruhda topiclar topilmadi yoki guruh forum emas.")
        await bot.close()
        return

    print(f"✅ Jami {len(topics)} ta topic topildi.\n")

    # 2. Nomlar bo'yicha guruhlash
    by_name: dict[str, list] = defaultdict(list)
    for topic in topics:
        by_name[topic.name].append(topic)

    # 3. Dublikatlarni aniqlash
    duplicates = {name: lst for name, lst in by_name.items() if len(lst) > 1}

    if not duplicates:
        print("🎉 Dublikat topiclar yo'q — hamma narsa tartibda!")
        await bot.close()
        return

    print(f"⚠️  {len(duplicates)} ta nomda dublikat topildi:\n")

    total_to_delete = 0

    for name, topic_list in duplicates.items():
        # Eng kichik message_thread_id = eng eski (birinchi yaratilgan) → shu qoladi
        topic_list_sorted = sorted(topic_list, key=lambda t: t.message_thread_id)
        keep   = topic_list_sorted[0]
        remove = topic_list_sorted[1:]

        print(f"  📌 '{name}'")
        print(f"     ✅ Qoladi  → ID: {keep.message_thread_id}")
        for t in remove:
            print(f"     🗑  O'chadi → ID: {t.message_thread_id}")
            total_to_delete += 1

        if not dry_run:
            for t in remove:
                try:
                    # Avval topicni yopamiz (ba'zi guruhlar yopilmagan topicni o'chirishga ruxsat bermaydi)
                    await bot.close_forum_topic(
                        chat_id=chat_id,
                        message_thread_id=t.message_thread_id
                    )
                except TelegramError as e:
                    print(f"     ⚠️  Yopishda xato (ID {t.message_thread_id}): {e}")

                try:
                    await bot.delete_forum_topic(
                        chat_id=chat_id,
                        message_thread_id=t.message_thread_id
                    )
                    print(f"     ✅ O'chirildi: ID {t.message_thread_id}")
                except TelegramError as e:
                    print(f"     ❌ O'chirishda xato (ID {t.message_thread_id}): {e}")

                # Telegram rate limit uchun kichik pauza
                await asyncio.sleep(0.5)

        print()

    await bot.close()

    if dry_run:
        print(f"🔍 DRY-RUN yakunlandi. O'chirish kerak bo'lgan: {total_to_delete} ta topic.")
        print("   Haqiqatda o'chirish uchun --dry-run flagini olib tashlang.")
    else:
        print(f"✅ Tozalash yakunlandi. O'chirildi: {total_to_delete} ta dublikat topic.")


def main():
    parser = argparse.ArgumentParser(description="Telegram guruh dublikat topiclarini tozalash")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Faqat ko'rish rejimi — hech narsa o'chirilmaydi"
    )
    args = parser.parse_args()
    asyncio.run(cleanup(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
