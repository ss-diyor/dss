"""
cleanup_topics.py — Guruhdagi bir xil nomdagi (dublikat) Topic larni tozalash.

Ishlatish:
    python3 cleanup_topics.py           # haqiqatda o'chiradi
    python3 cleanup_topics.py --dry-run # faqat ko'rsatadi, o'chirmaydi
"""

import os
import asyncio
import argparse
import httpx
from collections import defaultdict


TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN", "")
LOG_GROUP_ID = os.getenv("LOG_GROUP_ID", "")
API_BASE     = f"https://api.telegram.org/bot{TOKEN}"


async def tg(client: httpx.AsyncClient, method: str, **params) -> dict | None:
    """Telegram Bot API ga so'rov yuboradi."""
    try:
        r = await client.post(f"{API_BASE}/{method}", json=params, timeout=15)
        data = r.json()
        if not data.get("ok"):
            print(f"  ⚠️  API xato ({method}): {data.get('description')}")
            return None
        return data["result"]
    except Exception as e:
        print(f"  ❌ So'rov xatosi ({method}): {e}")
        return None


async def fetch_all_topics(client: httpx.AsyncClient, chat_id: int) -> list:
    """Guruhdan barcha topiclarni oladi (pagination bilan)."""
    all_topics = []
    after_id   = 0

    while True:
        params = {"chat_id": chat_id, "limit": 100}
        if after_id:
            params["after"] = after_id

        result = await tg(client, "getForumTopics", **params)
        if result is None:
            break

        topics = result.get("topics", [])
        if not topics:
            break

        all_topics.extend(topics)

        # Keyingi sahifa bormi?
        if not result.get("has_next"):
            break
        after_id = topics[-1]["message_thread_id"]

    return all_topics


async def cleanup(dry_run: bool = False) -> None:
    if not TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN muhit o'zgaruvchisi topilmadi.")
        return
    if not LOG_GROUP_ID:
        print("❌ LOG_GROUP_ID muhit o'zgaruvchisi topilmadi.")
        return

    chat_id = int(LOG_GROUP_ID)

    print(f"📋 Guruh: {chat_id}")
    print(f"{'🔍 DRY-RUN rejimi — hech narsa o`chirilmaydi' if dry_run else '🗑  HAQIQIY rejim — dublikatlar o`chiriladi'}\n")

    async with httpx.AsyncClient() as client:

        # 1. Barcha topiclarni yukla
        topics = await fetch_all_topics(client, chat_id)
        if not topics:
            print("⚠️  Guruhda topiclar topilmadi yoki guruh forum emas.")
            return

        print(f"✅ Jami {len(topics)} ta topic topildi.\n")

        # 2. Nomlar bo'yicha guruhlash
        by_name: dict[str, list] = defaultdict(list)
        for topic in topics:
            name = topic.get("name", "")
            by_name[name].append(topic)

        # 3. Dublikatlarni aniqlash
        duplicates = {name: lst for name, lst in by_name.items() if len(lst) > 1}

        if not duplicates:
            print("🎉 Dublikat topiclar yo'q — hamma narsa tartibda!")
            return

        print(f"⚠️  {len(duplicates)} ta nomda dublikat topildi:\n")
        total_to_delete = 0

        for name, topic_list in duplicates.items():
            # Eng kichik message_thread_id = eng eski → shu qoladi
            topic_list_sorted = sorted(topic_list, key=lambda t: t["message_thread_id"])
            keep   = topic_list_sorted[0]
            remove = topic_list_sorted[1:]

            print(f"  📌 '{name}'")
            print(f"     ✅ Qoladi  → ID: {keep['message_thread_id']}")

            for t in remove:
                tid = t["message_thread_id"]
                print(f"     🗑  O'chadi → ID: {tid}")
                total_to_delete += 1

                if not dry_run:
                    # Avval yopamiz
                    await tg(client, "closeForumTopic",
                             chat_id=chat_id, message_thread_id=tid)
                    # Keyin o'chiramiz
                    result = await tg(client, "deleteForumTopic",
                                      chat_id=chat_id, message_thread_id=tid)
                    if result is not None:
                        print(f"     ✅ O'chirildi: ID {tid}")
                    else:
                        print(f"     ❌ O'chirishda xato: ID {tid}")

                    await asyncio.sleep(0.5)

            print()

    if dry_run:
        print(f"🔍 DRY-RUN yakunlandi. O'chirish kerak bo'lgan: {total_to_delete} ta topic.")
        print("   Haqiqatda o'chirish uchun --dry-run flagini olib tashlang.")
    else:
        print(f"✅ Tozalash yakunlandi. O'chirildi: {total_to_delete} ta dublikat topic.")


def main():
    parser = argparse.ArgumentParser(description="Telegram guruh dublikat topiclarini tozalash")
    parser.add_argument("--dry-run", action="store_true",
                        help="Faqat ko'rish rejimi — hech narsa o'chirilmaydi")
    args = parser.parse_args()
    asyncio.run(cleanup(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
