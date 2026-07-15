
import json
import sqlite3
from datetime import datetime
from typing import Any

class Database:
    def __init__(self, path: str = "qoriqlash.sqlite3") -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS users(
                telegram_id INTEGER PRIMARY KEY,
                fio TEXT NOT NULL,
                username TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS records(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id INTEGER NOT NULL,
                card_number TEXT NOT NULL,
                record_type TEXT NOT NULL,
                device_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                channel_text TEXT NOT NULL,
                current_text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                channel_chat_id TEXT,
                channel_message_id INTEGER,
                approver_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_records_card ON records(card_number);
            CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);
            """)

    def upsert_user(self, telegram_id:int, fio:str, username:str|None, status:str="pending")->None:
        now=datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute("""
            INSERT INTO users(telegram_id,fio,username,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(telegram_id) DO UPDATE SET
              fio=excluded.fio,username=excluded.username,status=excluded.status,updated_at=excluded.updated_at
            """,(telegram_id,fio,username,status,now,now))

    def get_user(self, telegram_id:int)->dict[str,Any]|None:
        with self.connect() as conn:
            row=conn.execute("SELECT * FROM users WHERE telegram_id=?",(telegram_id,)).fetchone()
        return dict(row) if row else None

    def set_user_status(self, telegram_id:int, status:str)->None:
        with self.connect() as conn:
            conn.execute("UPDATE users SET status=?,updated_at=? WHERE telegram_id=?",
                         (status,datetime.now().isoformat(timespec="seconds"),telegram_id))

    def add_record(self, creator_id:int, card_number:str, record_type:str, device_type:str,
                   payload:dict[str,Any], channel_text:str)->int:
        now=datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            cur=conn.execute("""
            INSERT INTO records(creator_id,card_number,record_type,device_type,payload_json,
                                channel_text,current_text,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,'pending',?,?)
            """,(creator_id,card_number,record_type,device_type,json.dumps(payload,ensure_ascii=False),
                 channel_text,channel_text,now,now))
            return int(cur.lastrowid)

    def set_channel_message(self, record_id:int, chat_id:int, message_id:int)->None:
        with self.connect() as conn:
            conn.execute("""UPDATE records SET channel_chat_id=?,channel_message_id=?,updated_at=? WHERE id=?""",
                         (str(chat_id),message_id,datetime.now().isoformat(timespec="seconds"),record_id))

    def get_record(self, record_id:int)->dict[str,Any]|None:
        with self.connect() as conn:
            row=conn.execute("SELECT * FROM records WHERE id=?",(record_id,)).fetchone()
        return dict(row) if row else None

    def find_by_card(self, card_number:str)->dict[str,Any]|None:
        with self.connect() as conn:
            row=conn.execute("""SELECT * FROM records WHERE card_number=? ORDER BY id DESC LIMIT 1""",
                             (card_number,)).fetchone()
        return dict(row) if row else None

    def set_record_status(self, record_id:int, status:str, approver_id:int, current_text:str)->None:
        with self.connect() as conn:
            conn.execute("""UPDATE records SET status=?,approver_id=?,current_text=?,updated_at=? WHERE id=?""",
                         (status,approver_id,current_text,datetime.now().isoformat(timespec="seconds"),record_id))

    def report(self)->dict[str,int]:
        with self.connect() as conn:
            row=conn.execute("""
            SELECT
              SUM(record_type='object') objects,
              SUM(record_type='house') houses,
              SUM(device_type='KTS') kts,
              SUM(device_type='TEKO') teko,
              SUM(device_type='PRITOK') pritok,
              SUM(status='pending') pending,
              SUM(status='approved') approved,
              SUM(status='rejected') rejected
            FROM records
            """).fetchone()
        keys=("objects","houses","kts","teko","pritok","pending","approved","rejected")
        return {k:int(row[k] or 0) for k in keys}
