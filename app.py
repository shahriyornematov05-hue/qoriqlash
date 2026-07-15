
import asyncio
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl

import uvicorn
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup, WebAppInfo,
    FSInputFile, InputMediaPhoto, InputMediaDocument
)
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from database import Database

load_dotenv()

BOT_TOKEN=os.getenv("BOT_TOKEN","").strip()
CHANNEL_RAW=os.getenv("CHANNEL_ID","").strip()
SUPER_ADMIN_ID=int(os.getenv("SUPER_ADMIN_ID","0") or 0)
APPROVER_IDS={int(x) for x in os.getenv("APPROVER_IDS","").split(",") if x.strip().isdigit()}
APPROVER_IDS.add(SUPER_ADMIN_ID)
WEBAPP_URL=(os.getenv("WEBAPP_URL","").strip() or os.getenv("RENDER_EXTERNAL_URL","").strip())
HOST=os.getenv("HOST","0.0.0.0")
PORT=int(os.getenv("PORT","8000"))

if not BOT_TOKEN or not CHANNEL_RAW or not SUPER_ADMIN_ID:
    raise RuntimeError(".env faylidagi BOT_TOKEN, CHANNEL_ID va SUPER_ADMIN_ID to‘ldirilishi kerak.")

CHANNEL_ID=int(CHANNEL_RAW) if CHANNEL_RAW.lstrip("-").isdigit() else CHANNEL_RAW
logging.basicConfig(level=logging.INFO)

db=Database()
db.initialize()
bot=Bot(BOT_TOKEN)
dp=Dispatcher()
router=Router()
dp.include_router(router)

app=FastAPI(title="Qo'riqlash xizmatiga ulash")
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])
app.mount("/static",StaticFiles(directory="static"),name="static")

def verify_init_data(init_data:str)->dict:
    if not init_data:
        raise HTTPException(401,"Telegram initData yo'q")
    data=dict(parse_qsl(init_data,keep_blank_values=True))
    recv_hash=data.pop("hash",None)
    if not recv_hash:
        raise HTTPException(401,"initData hash yo'q")
    check="\n".join(f"{k}={v}" for k,v in sorted(data.items()))
    secret=hmac.new(b"WebAppData",BOT_TOKEN.encode(),hashlib.sha256).digest()
    calc=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc,recv_hash):
        raise HTTPException(401,"Telegram initData tasdiqlanmadi")
    user=json.loads(data.get("user","{}"))
    return user

def approver_keyboard(record_id:int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ ULANDI",callback_data=f"ok:{record_id}"),
        InlineKeyboardButton(text="❌ RAD ETILDI",callback_data=f"no:{record_id}")
    ]])

def register_keyboard(user_id:int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Tasdiqlash",callback_data=f"ua:{user_id}"),
        InlineKeyboardButton(text="❌ Rad etish",callback_data=f"ur:{user_id}")
    ]])

def app_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[
        KeyboardButton(text="🚀 Tizimni ochish",web_app=WebAppInfo(url=(WEBAPP_URL or "http://127.0.0.1:8000/")))
    ]],resize_keyboard=True)

def cyr(text:str)->str:
    if not text: return ""
    if any("А"<=ch<="я" or ch in "ҚқҒғҲҳЎў" for ch in text): return text
    pairs=[("g‘","ғ"),("g'","ғ"),("o‘","ў"),("o'","ў"),("sh","ш"),("ch","ч"),("yo","ё"),("yu","ю"),("ya","я")]
    out=text
    for a,b in pairs: out=out.replace(a,b).replace(a.title(),b.upper())
    table=str.maketrans(
      "ABDEFGHIJKLMNOPQRSTUVXYZabdefghijklmnopqrstuvxyz",
      "АБДЕФГҲИЖКЛМНОПҚРСТУВХЙЗабдефгҳижклмнопқрстувхйз")
    return out.translate(table)

def latin(text:str)->str:
    mp={"А":"A","Б":"B","В":"V","Г":"G","Ғ":"G‘","Д":"D","Е":"E","Ё":"Yo","Ж":"J","З":"Z","И":"I","Й":"Y","К":"K","Қ":"Q","Л":"L","М":"M","Н":"N","О":"O","П":"P","Р":"R","С":"S","Т":"T","У":"U","Ў":"O‘","Ф":"F","Х":"X","Ҳ":"H","Ч":"Ch","Ш":"Sh","Э":"E","Ю":"Yu","Я":"Ya"}
    mp.update({k.lower():v.lower() for k,v in list(mp.items())})
    return "".join(mp.get(ch,ch) for ch in (text or ""))

def channel_text(data:dict)->str:
    now=datetime.now()
    gps=(f"{data.get('latitude')}\n{data.get('longitude')}\n"
         f"https://maps.google.com/?q={data.get('latitude')},{data.get('longitude')}")
    docs=""
    if data.get("uploaded_files"):
        docs="\n\n📎 Ҳужжатлар:\n"+"\n".join("✅ "+x for x in data["uploaded_files"])
    if data["record_type"]=="object":
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
{('Мўлжал: '+cyr(data.get('landmark',''))) if data.get('landmark') else ''}

📡 GOM: {data['gom']}
🖥️ Pult: {data['pult']}
📱 Аппарат SIM: +998 {data['sim']}
🔢 Аппарат рақами: {data['device_number']}
⚡ Электрик: {cyr(data['electrician'])}
👨‍💼 ТҚМ бошлиғи: {cyr(data['tqm_head'])}

🗺️ GPS:
{gps}

📅 Уланган сана: {now:%d.%m.%Y}
🕒 Уланган вақт: {now:%H:%M}{docs}"""
    simline="" if data["device_type"]=="KTS" else f"📱 Аппарат SIM: +998 {data['sim']}\n"
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
{(cyr(data.get('entrance',''))+'-подъезд') if data.get('entrance') else ''}
{(cyr(data.get('floor',''))+'-қават') if data.get('floor') else ''}
{(cyr(data.get('building_floors',''))+' қаватли уй') if data.get('building_floors') else ''}
{('Мўлжал: '+cyr(data.get('landmark',''))) if data.get('landmark') else ''}

📡 GOM: {data['gom']}
🖥️ Pult: {data['pult']}
{simline}🔢 Аппарат рақами: {data['device_number']}
⚡ Электрик: {cyr(data['electrician'])}
👨‍💼 ТҚМ бошлиғи: {cyr(data['tqm_head'])}

🗺️ GPS:
{gps}

📅 Уланган сана: {now:%d.%m.%Y}
🕒 Уланган вақт: {now:%H:%M}{docs}"""

@app.get("/")
async def index():
    return FileResponse("static/index.html")

@app.post("/api/register")
async def register(init_data: str = Form(...), fio: str = Form(...)):
    user=verify_init_data(init_data)
    tid=int(user["id"])
    db.upsert_user(tid,fio,user.get("username"),"pending")
    await bot.send_message(SUPER_ADMIN_ID,
        f"🆕 YANGI FOYDALANUVCHI\n\n👤 F.I.Sh.: {fio}\n🆔 Telegram ID: {tid}",
        reply_markup=register_keyboard(tid))
    return {"ok":True,"status":"pending"}

@app.get("/api/me")
async def me(init_data:str):
    user=verify_init_data(init_data)
    row=db.get_user(int(user["id"]))
    return {"user":row}

@app.post("/api/submit")
async def submit(
    init_data:str=Form(...), payload:str=Form(...),
    passport_front:UploadFile|None=File(None),
    passport_back:UploadFile|None=File(None),
    extra_document:UploadFile|None=File(None),
):
    user=verify_init_data(init_data)
    tid=int(user["id"])
    u=db.get_user(tid)
    if not u or u["status"]!="approved":
        raise HTTPException(403,"Foydalanuvchi tasdiqlanmagan")
    data=json.loads(payload)
    uploaded=[]
    files=[]
    for f in (passport_front,passport_back,extra_document):
        if f and f.filename:
            p=Path("uploads")/f"{datetime.now().timestamp()}_{f.filename}"
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(await f.read())
            uploaded.append(f.filename)
            files.append(p)
    data["uploaded_files"]=uploaded
    text=channel_text(data)
    rid=db.add_record(tid,data["card_number"],data["record_type"],data["device_type"],data,text)
    sent=await bot.send_message(CHANNEL_ID,text,reply_markup=approver_keyboard(rid))
    db.set_channel_message(rid,sent.chat.id,sent.message_id)
    for p in files:
        if p.suffix.lower() in {".jpg",".jpeg",".png",".webp"}:
            await bot.send_photo(CHANNEL_ID,FSInputFile(p))
        else:
            await bot.send_document(CHANNEL_ID,FSInputFile(p))
    return {"ok":True,"record_id":rid}

@app.get("/api/search/{card}")
async def search(card:str, init_data:str):
    user=verify_init_data(init_data)
    u=db.get_user(int(user["id"]))
    if not u or u["status"]!="approved":
        raise HTTPException(403,"Ruxsat yo'q")
    row=db.find_by_card(card)
    return {"record":row}

@app.get("/api/report")
async def report(init_data:str):
    user=verify_init_data(init_data)
    u=db.get_user(int(user["id"]))
    if not u or u["status"]!="approved":
        raise HTTPException(403,"Ruxsat yo'q")
    return db.report()

@router.message(CommandStart())
async def start(message:Message):
    uid=message.from_user.id
    u=db.get_user(uid)
    if u and u["status"]=="approved":
        await message.answer(f"Assalomu alaykum, {u['fio']}!",reply_markup=app_keyboard())
    elif u and u["status"]=="pending":
        await message.answer("⏳ So'rovingiz admin tasdig'ini kutmoqda.")
    else:
        await message.answer("Ro'yxatdan o'tish uchun quyidagi tugmani bosing.",reply_markup=app_keyboard())

@router.callback_query(F.data.startswith("ua:"))
async def user_approve(cb:CallbackQuery):
    if cb.from_user.id!=SUPER_ADMIN_ID:
        return await cb.answer("Faqat Super Admin",show_alert=True)
    uid=int(cb.data.split(":")[1]); db.set_user_status(uid,"approved")
    await bot.send_message(uid,"✅ Ro'yxatdan o'tishingiz tasdiqlandi.",reply_markup=app_keyboard())
    await cb.message.edit_text(cb.message.text+"\n\n🟢 TASDIQLANDI"); await cb.answer()

@router.callback_query(F.data.startswith("ur:"))
async def user_reject(cb:CallbackQuery):
    if cb.from_user.id!=SUPER_ADMIN_ID:
        return await cb.answer("Faqat Super Admin",show_alert=True)
    uid=int(cb.data.split(":")[1]); db.set_user_status(uid,"rejected")
    await bot.send_message(uid,"❌ So'rovingiz rad etildi.")
    await cb.message.edit_text(cb.message.text+"\n\n🔴 RAD ETILDI"); await cb.answer()

def status_text(old:str,status:str,name:str)->str:
    for s in ("🟡 ҲОЛАТИ: ЖАРАЁНДА","🟢 ҲОЛАТИ: УЛАНДИ ✅","🔴 ҲОЛАТИ: РАД ЭТИЛДИ"):
        if s in old:
            old=old.replace(s,status,1); break
    return old+f"\n\n✅ Тасдиқлади: {name}\n🕒 {datetime.now():%d.%m.%Y %H:%M}"

@router.callback_query(F.data.startswith(("ok:","no:")))
async def record_status(cb:CallbackQuery):
    if cb.from_user.id not in APPROVER_IDS:
        return await cb.answer("Sizda ruxsat yo'q",show_alert=True)
    rid=int(cb.data.split(":")[1]); row=db.get_record(rid)
    if not row or row["status"]!="pending":
        return await cb.answer("Avval ko'rib chiqilgan",show_alert=True)
    approved=cb.data.startswith("ok:")
    st="approved" if approved else "rejected"
    label="🟢 ҲОЛАТИ: УЛАНДИ ✅" if approved else "🔴 ҲОЛАТИ: РАД ЭТИЛДИ"
    name=(db.get_user(cb.from_user.id) or {}).get("fio",cb.from_user.full_name)
    new=status_text(row["channel_text"],label,name)
    await bot.edit_message_text(new,int(row["channel_chat_id"]),row["channel_message_id"])
    db.set_record_status(rid,st,cb.from_user.id,new)
    await bot.send_message(row["creator_id"],f"{'🟢 ULANDI' if approved else '🔴 RAD ETILDI'}: {row['card_number']}")
    await cb.answer()

async def main():
    async def run_bot():
        await dp.start_polling(bot)
    config=uvicorn.Config(app,host=HOST,port=PORT,log_level="info")
    server=uvicorn.Server(config)
    await asyncio.gather(run_bot(),server.serve())

if __name__=="__main__":
    asyncio.run(main())
