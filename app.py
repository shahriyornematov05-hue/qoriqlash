import asyncio
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, quote

import uvicorn
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from database import Database


# =========================
# SOZLAMALAR
# =========================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_RAW = os.getenv("CHANNEL_ID", "").strip()
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0") or 0)

APPROVER_IDS = {
    int(x)
    for x in os.getenv("APPROVER_IDS", "").split(",")
    if x.strip().isdigit()
}
APPROVER_IDS.add(SUPER_ADMIN_ID)

WEBAPP_URL = (
    os.getenv("WEBAPP_URL", "").strip()
    or os.getenv("RENDER_EXTERNAL_URL", "").strip()
)

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

if not BOT_TOKEN or not CHANNEL_RAW or not SUPER_ADMIN_ID:
    raise RuntimeError(
        ".env faylidagi BOT_TOKEN, CHANNEL_ID va SUPER_ADMIN_ID to‘ldirilishi kerak."
    )

CHANNEL_ID = (
    int(CHANNEL_RAW)
    if CHANNEL_RAW.lstrip("-").isdigit()
    else CHANNEL_RAW
)

logging.basicConfig(level=logging.INFO)


# =========================
# BOT, BAZA VA FASTAPI
# =========================

db = Database()
db.initialize()

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

app = FastAPI(title="Qo‘riqlash xizmatiga ulash")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static",
)


# =========================
# TELEGRAM AUTH
# =========================

def verify_init_data(init_data: str) -> dict:
    """
    Telegram WebApp initData ni tekshiradi.
    """
    if not init_data:
        raise HTTPException(401, "Telegram initData yo‘q")

    data = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = data.pop("hash", None)

    if not received_hash:
        raise HTTPException(401, "initData hash yo‘q")

    data_check_string = "\n".join(
        f"{key}={value}"
        for key, value in sorted(data.items())
    )

    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256,
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(
            401,
            "Telegram initData tasdiqlanmadi",
        )

    return json.loads(data.get("user", "{}"))


def make_launch_auth(user_id: int) -> str:
    """
    /start tugmasi uchun xavfsiz vaqtinchalik bo‘lmagan auth kaliti.
    """
    raw_user_id = str(user_id)

    signature = hmac.new(
        BOT_TOKEN.encode(),
        raw_user_id.encode(),
        hashlib.sha256,
    ).hexdigest()

    return f"{raw_user_id}.{signature}"


def verify_launch_auth(auth: str) -> int:
    """
    Bot tugmasi orqali berilgan auth kalitini tekshiradi.
    """
    try:
        raw_user_id, received_signature = auth.split(".", 1)

        expected_signature = hmac.new(
            BOT_TOKEN.encode(),
            raw_user_id.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(
            expected_signature,
            received_signature,
        ):
            raise ValueError

        return int(raw_user_id)

    except (ValueError, AttributeError):
        raise HTTPException(
            401,
            "WebApp kirish kaliti noto‘g‘ri",
        )


def resolve_user(init_data: str = "", auth: str = "") -> tuple[int, str | None]:
    """
    Avval Telegram initData ni ishlatadi.
    Agar initData kelmasa, bot tugmasidagi auth ishlatiladi.
    """
    if init_data:
        user = verify_init_data(init_data)

        return (
            int(user["id"]),
            user.get("username"),
        )

    if auth:
        return (
            verify_launch_auth(auth),
            None,
        )

    raise HTTPException(
        401,
        "Telegram ma’lumotlari kelmadi",
    )


# =========================
# KLAVIATURALAR
# =========================

def approver_keyboard(record_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ ULANDI",
                callback_data=f"ok:{record_id}",
            ),
            InlineKeyboardButton(
                text="❌ RAD ETILDI",
                callback_data=f"no:{record_id}",
            ),
        ]]
    )


def register_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Tasdiqlash",
                callback_data=f"ua:{user_id}",
            ),
            InlineKeyboardButton(
                text="❌ Rad etish",
                callback_data=f"ur:{user_id}",
            ),
        ]]
    )


def app_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    base_url = WEBAPP_URL or "http://127.0.0.1:8000/"

    separator = "&" if "?" in base_url else "?"
    launch_url = (
        f"{base_url}{separator}"
        f"auth={quote(make_launch_auth(user_id))}"
    )

    return ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(
                text="🚀 Tizimni ochish",
                web_app=WebAppInfo(url=launch_url),
            )
        ]],
        resize_keyboard=True,
    )


# =========================
# MATN O‘GIRISH
# =========================

def cyr(text: str) -> str:
    if not text:
        return ""

    if any(
        "А" <= char <= "я"
        or char in "ҚқҒғҲҳЎў"
        for char in text
    ):
        return text

    pairs = [
        ("g‘", "ғ"),
        ("g'", "ғ"),
        ("o‘", "ў"),
        ("o'", "ў"),
        ("sh", "ш"),
        ("ch", "ч"),
        ("yo", "ё"),
        ("yu", "ю"),
        ("ya", "я"),
    ]

    result = text

    for latin_pair, cyr_pair in pairs:
        result = result.replace(
            latin_pair,
            cyr_pair,
        ).replace(
            latin_pair.title(),
            cyr_pair.upper(),
        )

    table = str.maketrans(
        "ABDEFGHIJKLMNOPQRSTUVXYZabdefghijklmnopqrstuvxyz",
        "АБДЕФГҲИЖКЛМНОПҚРСТУВХЙЗабдефгҳижклмнопқрстувхйз",
    )

    return result.translate(table)


def latin(text: str) -> str:
    mapping = {
        "А": "A",
        "Б": "B",
        "В": "V",
        "Г": "G",
        "Ғ": "G‘",
        "Д": "D",
        "Е": "E",
        "Ё": "Yo",
        "Ж": "J",
        "З": "Z",
        "И": "I",
        "Й": "Y",
        "К": "K",
        "Қ": "Q",
        "Л": "L",
        "М": "M",
        "Н": "N",
        "О": "O",
        "П": "P",
        "Р": "R",
        "С": "S",
        "Т": "T",
        "У": "U",
        "Ў": "O‘",
        "Ф": "F",
        "Х": "X",
        "Ҳ": "H",
        "Ч": "Ch",
        "Ш": "Sh",
        "Э": "E",
        "Ю": "Yu",
        "Я": "Ya",
    }

    mapping.update({
        key.lower(): value.lower()
        for key, value in list(mapping.items())
    })

    return "".join(
        mapping.get(char, char)
        for char in (text or "")
    )


# =========================
# KANAL MATNI
# =========================

def channel_text(data: dict) -> str:
    now = datetime.now()

    gps_text = (
        f"{data.get('latitude')}\n"
        f"{data.get('longitude')}\n"
        f"https://maps.google.com/"
        f"?q={data.get('latitude')},{data.get('longitude')}"
    )

    documents_text = ""

    if data.get("uploaded_files"):
        documents_text = (
            "\n\n📎 Ҳужжатлар:\n"
            + "\n".join(
                "✅ " + filename
                for filename in data["uploaded_files"]
            )
        )

    if data["record_type"] == "object":
        return f"""🛡️ ҚЎРИҚЛАШ ХИЗМАТИГА УЛАШ

🟡 ҲОЛАТИ: ЖАРАЁНДА

━━━━━━━━━━━━━━━━━━━━━━
🏢 ОБЪЕКТ

📋 Қурилма: {data['device_type']}
🔢 Карта рақами: {data['card_number']}

🏢 Ташкилот: {data['org_type']} "{latin(data['object_name'])}"
🏬 Объект тури: {cyr(data['object_type'])}
🏷️ Кўчадаги номи: {cyr(data['street_name'])}
👤 Объект раҳбари: {cyr(data['manager'])}
📞 Телефон: +998 {data['phone']}

📍 Манзил:
Бектемир тумани
{cyr(data['mfy'])} МФЙ
{cyr(data['street'])} кўчаси
{cyr(data['house_number'])} уй
{('Мўлжал: ' + cyr(data.get('landmark', ''))) if data.get('landmark') else ''}

📡 GOM: {data['gom']}
🖥️ Pult: {data['pult']}
📱 Аппарат SIM: +998 {data['sim']}
🔢 Аппарат рақами: {data['device_number']}
⚡ Электрик: {cyr(data['electrician'])}
👨‍💼 ТҚМ бошлиғи: {cyr(data['tqm_head'])}

🗺️ GPS:
{gps_text}

📅 Уланган сана: {now:%d.%m.%Y}
🕒 Уланган вақт: {now:%H:%M}{documents_text}"""

    sim_line = (
        ""
        if data["device_type"] == "KTS"
        else f"📱 Аппарат SIM: +998 {data['sim']}\n"
    )

    return f"""🛡️ ҚЎРИҚЛАШ ХИЗМАТИГА УЛАШ

🟡 ҲОЛАТИ: ЖАРАЁНДА

━━━━━━━━━━━━━━━━━━━━━━
🏠 ХОНАДОН

📋 Қурилма: {data['device_type']}
🔢 Карта/KTS рақами: {data['card_number']}
👤 Ф.И.Ш.: {cyr(data['fio'])}
📞 Телефон: +998 {data['phone']}

📍 Манзил:
Бектемир тумани
{cyr(data['mfy'])} МФЙ
{cyr(data['street'])} кўчаси
{cyr(data['house_number'])} уй
{cyr(data['apartment'])}-хонадон
{(cyr(data.get('entrance', '')) + '-подъезд') if data.get('entrance') else ''}
{(cyr(data.get('floor', '')) + '-қават') if data.get('floor') else ''}
{(cyr(data.get('building_floors', '')) + ' қаватли уй') if data.get('building_floors') else ''}
{('Мўлжал: ' + cyr(data.get('landmark', ''))) if data.get('landmark') else ''}

📡 GOM: {data['gom']}
🖥️ Pult: {data['pult']}
{sim_line}🔢 Аппарат рақами: {data['device_number']}
⚡ Электрик: {cyr(data['electrician'])}
👨‍💼 ТҚМ бошлиғи: {cyr(data['tqm_head'])}

🗺️ GPS:
{gps_text}

📅 Уланган сана: {now:%d.%m.%Y}
🕒 Уланган вақт: {now:%H:%M}{documents_text}"""


# =========================
# FASTAPI SAHIFALAR
# =========================

@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.post("/api/register")
async def register(
    fio: str = Form(...),
    init_data: str = Form(""),
    auth: str = Form(""),
):
    telegram_id, username = resolve_user(
        init_data,
        auth,
    )

    db.upsert_user(
        telegram_id,
        fio,
        username,
        "pending",
    )

    await bot.send_message(
        SUPER_ADMIN_ID,
        (
            "🆕 YANGI FOYDALANUVCHI\n\n"
            f"👤 F.I.Sh.: {fio}\n"
            f"🆔 Telegram ID: {telegram_id}"
        ),
        reply_markup=register_keyboard(telegram_id),
    )

    return {
        "ok": True,
        "status": "pending",
    }


@app.get("/api/me")
async def me(
    init_data: str = "",
    auth: str = "",
):
    telegram_id, _ = resolve_user(
        init_data,
        auth,
    )

    row = db.get_user(telegram_id)

    return {
        "user": row,
    }


@app.post("/api/submit")
async def submit(
    payload: str = Form(...),
    init_data: str = Form(""),
    auth: str = Form(""),
    passport_front: UploadFile | None = File(None),
    passport_back: UploadFile | None = File(None),
    extra_document: UploadFile | None = File(None),
):
    telegram_id, _ = resolve_user(
        init_data,
        auth,
    )

    user_row = db.get_user(telegram_id)

    if not user_row or user_row["status"] != "approved":
        raise HTTPException(
            403,
            "Foydalanuvchi tasdiqlanmagan",
        )

    data = json.loads(payload)
    uploaded_files = []
    saved_paths = []

    for uploaded_file in (
        passport_front,
        passport_back,
        extra_document,
    ):
        if uploaded_file and uploaded_file.filename:
            file_path = (
                Path("uploads")
                / f"{datetime.now().timestamp()}_{uploaded_file.filename}"
            )

            file_path.parent.mkdir(exist_ok=True)

            file_path.write_bytes(
                await uploaded_file.read()
            )

            uploaded_files.append(
                uploaded_file.filename
            )

            saved_paths.append(file_path)

    data["uploaded_files"] = uploaded_files

    text = channel_text(data)

    record_id = db.add_record(
        telegram_id,
        data["card_number"],
        data["record_type"],
        data["device_type"],
        data,
        text,
    )

    sent_message = await bot.send_message(
        CHANNEL_ID,
        text,
        reply_markup=approver_keyboard(record_id),
    )

    db.set_channel_message(
        record_id,
        sent_message.chat.id,
        sent_message.message_id,
    )

    for file_path in saved_paths:
        if file_path.suffix.lower() in {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
        }:
            await bot.send_photo(
                CHANNEL_ID,
                FSInputFile(file_path),
            )
        else:
            await bot.send_document(
                CHANNEL_ID,
                FSInputFile(file_path),
            )

    return {
        "ok": True,
        "record_id": record_id,
    }


@app.get("/api/search/{card}")
async def search(
    card: str,
    init_data: str = "",
    auth: str = "",
):
    telegram_id, _ = resolve_user(
        init_data,
        auth,
    )

    user_row = db.get_user(telegram_id)

    if not user_row or user_row["status"] != "approved":
        raise HTTPException(
            403,
            "Ruxsat yo‘q",
        )

    row = db.find_by_card(card)

    return {
        "record": row,
    }


@app.get("/api/report")
async def report(
    init_data: str = "",
    auth: str = "",
):
    telegram_id, _ = resolve_user(
        init_data,
        auth,
    )

    user_row = db.get_user(telegram_id)

    if not user_row or user_row["status"] != "approved":
        raise HTTPException(
            403,
            "Ruxsat yo‘q",
        )

    return db.report()


# =========================
# TELEGRAM BOT HANDLERLARI
# =========================

@router.message(CommandStart())
async def start(message: Message):
    user_id = message.from_user.id
    user_row = db.get_user(user_id)

    if user_row and user_row["status"] == "approved":
        await message.answer(
            f"Assalomu alaykum, {user_row['fio']}!",
            reply_markup=app_keyboard(user_id),
        )

    elif user_row and user_row["status"] == "pending":
        await message.answer(
            "⏳ So‘rovingiz admin tasdig‘ini kutmoqda."
        )

    else:
        await message.answer(
            "Ro‘yxatdan o‘tish uchun quyidagi tugmani bosing.",
            reply_markup=app_keyboard(user_id),
        )


@router.callback_query(F.data.startswith("ua:"))
async def user_approve(callback: CallbackQuery):
    if callback.from_user.id != SUPER_ADMIN_ID:
        return await callback.answer(
            "Faqat Super Admin",
            show_alert=True,
        )

    user_id = int(
        callback.data.split(":")[1]
    )

    db.set_user_status(
        user_id,
        "approved",
    )

    await bot.send_message(
        user_id,
        "✅ Ro‘yxatdan o‘tishingiz tasdiqlandi.",
        reply_markup=app_keyboard(user_id),
    )

    await callback.message.edit_text(
        callback.message.text
        + "\n\n🟢 TASDIQLANDI"
    )

    await callback.answer()


@router.callback_query(F.data.startswith("ur:"))
async def user_reject(callback: CallbackQuery):
    if callback.from_user.id != SUPER_ADMIN_ID:
        return await callback.answer(
            "Faqat Super Admin",
            show_alert=True,
        )

    user_id = int(
        callback.data.split(":")[1]
    )

    db.set_user_status(
        user_id,
        "rejected",
    )

    await bot.send_message(
        user_id,
        "❌ So‘rovingiz rad etildi.",
    )

    await callback.message.edit_text(
        callback.message.text
        + "\n\n🔴 RAD ETILDI"
    )

    await callback.answer()


def status_text(
    old_text: str,
    new_status: str,
    approver_name: str,
) -> str:
    for current_status in (
        "🟡 ҲОЛАТИ: ЖАРАЁНДА",
        "🟢 ҲОЛАТИ: УЛАНДИ ✅",
        "🔴 ҲОЛАТИ: РАД ЭТИЛДИ",
    ):
        if current_status in old_text:
            old_text = old_text.replace(
                current_status,
                new_status,
                1,
            )
            break

    return (
        old_text
        + f"\n\n✅ Тасдиқлади: {approver_name}"
        + f"\n🕒 {datetime.now():%d.%m.%Y %H:%M}"
    )


@router.callback_query(F.data.startswith(("ok:", "no:")))
async def record_status(callback: CallbackQuery):
    if callback.from_user.id not in APPROVER_IDS:
        return await callback.answer(
            "Sizda ruxsat yo‘q",
            show_alert=True,
        )

    record_id = int(
        callback.data.split(":")[1]
    )

    row = db.get_record(record_id)

    if not row or row["status"] != "pending":
        return await callback.answer(
            "Avval ko‘rib chiqilgan",
            show_alert=True,
        )

    approved = callback.data.startswith("ok:")

    status = (
        "approved"
        if approved
        else "rejected"
    )

    label = (
        "🟢 ҲОЛАТИ: УЛАНДИ ✅"
        if approved
        else "🔴 ҲОЛАТИ: РАД ЭТИЛДИ"
    )

    approver_name = (
        db.get_user(callback.from_user.id)
        or {}
    ).get(
        "fio",
        callback.from_user.full_name,
    )

    new_text = status_text(
        row["channel_text"],
        label,
        approver_name,
    )

    await bot.edit_message_text(
        new_text,
        int(row["channel_chat_id"]),
        row["channel_message_id"],
    )

    db.set_record_status(
        record_id,
        status,
        callback.from_user.id,
        new_text,
    )

    await bot.send_message(
        row["creator_id"],
        (
            "🟢 ULANDI"
            if approved
            else "🔴 RAD ETILDI"
        )
        + f": {row['card_number']}",
    )

    await callback.answer()


# =========================
# ISHGA TUSHIRISH
# =========================

async def main():
    async def run_bot():
        await dp.start_polling(bot)

    config = uvicorn.Config(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
    )

    server = uvicorn.Server(config)

    await asyncio.gather(
        run_bot(),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
