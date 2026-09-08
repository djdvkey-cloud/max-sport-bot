"""
Разовый скрипт: слушает сообщения 30 секунд и печатает id чата,
откуда они пришли. Нужен один раз — узнать MAX_CHAT_ID для группы.

ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ:
    MAX_BOT_TOKEN — токен от @MasterBot

ЗАПУСК:
    python get_chat_id.py
"""

import asyncio
import os

from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated

MAX_BOT_TOKEN = os.environ["MAX_BOT_TOKEN"]
LISTEN_SECONDS = 30

bot = Bot(MAX_BOT_TOKEN)
dp = Dispatcher()
found = []


@dp.message_created()
async def on_any_message(event: MessageCreated):
    chat_id = event.chat.chat_id
    user_id = event.from_user.user_id
    text = event.message.body.text
    found.append(chat_id)
    print(f"[НАЙДЕН ID ЧАТА] {chat_id}  (написал user_id={user_id}, текст: {text!r})")


async def main():
    print(f"Слушаю {LISTEN_SECONDS} секунд — за это время отправьте боту "
          f"любое сообщение в нужной группе (например «привет»).")
    try:
        await bot.delete_webhook()
    except Exception:
        pass
    task = asyncio.create_task(dp.start_polling(bot))
    await asyncio.sleep(LISTEN_SECONDS)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    if found:
        print(f"\nГотово. Найденные id чатов: {sorted(set(found))}")
    else:
        print("\nЗа это время сообщений не пришло. Проверьте: бот точно "
              "добавлен в группу? Запустите скрипт заново и отправьте "
              "сообщение, пока он работает.")


if __name__ == "__main__":
    asyncio.run(main())
