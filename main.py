import os
import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

# --- 1) Загрузка токена ---
load_dotenv()
TOKEN = os.getenv('API_TOKEN')
if not TOKEN:
    exit("❌ Ошибка: не найден API_TOKEN в .env")

# --- 2) Логирование ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- 3) Инициализация бота ---
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# --- 4) Простая файловая «БД» участников ---
class MemberDatabase:
    def __init__(self, path="members.db"):
        self.path = path
        self.data = {}  # { chat_id_str: { user_id_str: {username,first_name,last_name} } }
        self._ensure_file()
        self.load()

    def _ensure_file(self):
        if not os.path.exists(self.path):
            open(self.path, "w", encoding="utf-8").close()
            logger.info(f"Создан файл БД: {self.path}")

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    chat_id, user_id, username, first_name, last_name = line.strip().split("|")
                    self.data.setdefault(chat_id, {})[user_id] = {
                        "username": username,
                        "first_name": first_name,
                        "last_name": last_name
                    }
            logger.info(f"БД загружена: чатов={len(self.data)}")
        except Exception as e:
            logger.error(f"Ошибка загрузки БД: {e}")

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                for chat_id, users in self.data.items():
                    for uid, u in users.items():
                        f.write(f"{chat_id}|{uid}|{u['username']}|{u['first_name']}|{u['last_name']}\n")
            logger.info("БД сохранена")
        except Exception as e:
            logger.error(f"Ошибка сохранения БД: {e}")

    def add(self, chat_id: str, user: types.User):
        chat = str(chat_id)
        uid = str(user.id)
        self.data.setdefault(chat, {})[uid] = {
            "username": user.username or "",
            "first_name": user.first_name or "",
            "last_name": user.last_name or ""
        }
        self.save()
        logger.debug(f"Добавлен user={uid} в chat={chat}")

    def get(self, chat_id: str) -> dict:
        return self.data.get(str(chat_id), {})

    def clear(self, chat_id: str):
        chat = str(chat_id)
        if chat in self.data:
            del self.data[chat]
            self.save()
            logger.info(f"Очищен список для chat={chat}")

db = MemberDatabase()

# --- 5) Команда /start ---
@dp.message(Command("start"))
async def cmd_start(msg: types.Message):
    await msg.answer(
        "👋 Привет! Я бот для упоминаний.\n"
        "1) /scan — очистить старый список и добавить всех админов чата.\n"
        "2) После этого я буду автоматически сохранять каждого, кто пишет или вступает.\n"
        "3) /all — упомянуть всех."
    )

# --- 6) Команда /scan ---
@dp.message(Command("scan"))
async def cmd_scan(msg: types.Message):
    if msg.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    chat_id = msg.chat.id

    # Получаем админов чата
    admins = await bot.get_chat_administrators(chat_id=chat_id)
    me = await bot.get_me()
    if not any(adm.user.id == me.id for adm in admins):
        await msg.reply("❌ Я не админ этого чата — дайте мне права администратора и повторите.")
        return

    # Очищаем старый список и добавляем админов
    db.clear(chat_id)
    for adm in admins:
        db.add(chat_id, adm.user)

    await msg.reply(f"✅ Сканирование админов завершено: {len(admins)} пользователей добавлено.")

# --- 7) Команда /all: упомянуть всех ---
@dp.message(Command("all"))
async def cmd_all(msg: types.Message):
    if msg.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    chat_str = str(msg.chat.id)
    members = db.get(chat_str)
    if not members:
        await msg.reply("❌ Список пуст. Сделайте /scan или подождите активности участников.")
        return

    # Собираем упоминания
    mentions = []
    for uid, u in members.items():
        if u["username"]:
            mentions.append(f"@{u['username']}")
        else:
            name = (u["first_name"] + " " + u["last_name"]).strip()
            mentions.append(f'<a href="tg://user?id={uid}">{name}</a>')

    # Шлём по 15 упоминаний
    parts = [mentions[i:i+15] for i in range(0, len(mentions), 15)]
    for i, chunk in enumerate(parts, 1):
        await msg.reply(
            f"🔔 Всем внимание! ({i}/{len(parts)})\n" + " ".join(chunk),
            parse_mode="HTML"
        )

# --- 8) Автосбор участников при сообщении ---
@dp.message()
async def on_message(msg: types.Message):
    if msg.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        db.add(msg.chat.id, msg.from_user)

# --- 9) Автосбор при вступлении/смене статуса ---
@dp.chat_member()
async def on_chat_member(update: ChatMemberUpdated):
    status = update.new_chat_member.status
    if status in ("member", "administrator", "creator"):
        db.add(update.chat.id, update.new_chat_member.user)

# --- 10) Запуск и остановка ---
async def on_startup():
    logger.info("🔹 Бот запущен")

async def on_shutdown():
    logger.info("🔹 Сохраняем БД перед выходом...")
    db.save()
    logger.info("🔹 Бот остановлен")

async def main():
    await on_startup()
    try:
        await dp.start_polling(bot)
    finally:
        await on_shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🔹 Остановлен вручную")
    except Exception as e:
        logger.critical(f"❌ Ошибка: {e}")
