import os
import json
import random
import string
import threading
import gspread
import requests
from datetime import datetime
from fastapi import FastAPI, Request
from google import genai
from google.genai import types
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

# ----------------------------------------------------
# 1. KONFIGURASI BOT & SECRETS (DARI ENVIRONMENT)
# ----------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FONNTE_TOKEN = os.getenv("FONNTE_TOKEN")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# Nomor WA/HP Penerima Alert Khusus (Format: 08xxxxxxxxxx atau 628xxxxxxxxxx)
ALERT_PHONE_NUMBER = os.getenv("ALERT_PHONE_NUMBER")

# Inisialisasi Gemini Client
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# Inisialisasi Google Sheets Client
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)
sheet = gc.open("IT_Support_Log").sheet1

# ----------------------------------------------------
# 2. SYSTEM PROMPT & HELPER FUNCTIONS
# ----------------------------------------------------
SYSTEM_PROMPT = """
Anda adalah AI Assistant IT Support internal toko dan perusahaan yang responsif, ramah, dan profesional.

Tugas Utama:
Klasifikasikan pesan dan berikan balasan HANYA jika pesan berisi KELUHAN TEKNIS IT SUPPORT / PEKERJAAN.

Aturan Klasifikasi:
1. Jika pengguna mengirim SAPAAN / CHAT PRIBADI / OBROLAN BIASA (contoh: "Halo", "P", "Tes", "Lagi apa", "Siang"):
   - Set "kategori": "Pribadi/Abaikan"
   - Set "jawaban": ""

2. Jika pengguna mengirim KELUHAN TEKNIS / TROUBLE (contoh: "Printer macet", "Aplikasi POS error", "WiFi kasir putus", "Scanner mati"):
   - Berikan 2-3 langkah troubleshooting yang ringkas, jelas, dan mudah diikuti.
   - Jika kendala memerlukan penanganan langsung, sampaikan bahwa Anda akan bantu jadwalkan kunjungan teknisi IT.
   - Set "kategori" ke salah satu pilihan: "Hardware", "Network", "POS", "Software", atau "Lainnya".

Format Respon Wajib (HANYA JSON VALID):
{
  "kategori": "Pribadi/Abaikan/Hardware/Network/POS/Software/Lainnya",
  "jawaban": "Isi pesan balasan Anda di sini"
}
"""

def generate_ticket_number():
    date_str = datetime.now().strftime("%Y%m%d")
    random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"TCK-{date_str}-{random_str}"

def send_whatsapp_reply(target: str, message: str):
    url = "https://api.fonnte.com/send"
    headers = {"Authorization": FONNTE_TOKEN}
    payload = {
        "target": target,
        "message": message
    }
    try:
        response = requests.post(url, headers=headers, data=payload, timeout=10)
        print(f"[FONNTE API] Kirim ke {target} | Respon: {response.text}")
    except Exception as e:
        print(f"[FONNTE ERROR] Gagal mengirim pesan WA ke {target}: {e}")

def send_simple_wa_alert(sender: str, sender_name: str):
    """Mengirim Notifikasi Ringkas untuk SEMUA Pesan Masuk (Pribadi & Pekerjaan)"""
    if not ALERT_PHONE_NUMBER:
        print("[WARNING] ALERT_PHONE_NUMBER belum di-set di Environment Variables Render!")
        return
    
    # Format universal yang singkat & pasti ditangkap MacroDroid
    alert_message = f"SMS_ALERT: Ada WA Masuk dari {sender_name} ({sender})"
    print(f"[ALERT HP LAMA] Mengirim sinyal alert ke {ALERT_PHONE_NUMBER}")
    send_whatsapp_reply(ALERT_PHONE_NUMBER, alert_message)

def log_to_sheets_async(ticket_id, sender, sender_name, message, kategori, ai_reply):
    def run():
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            row_data = [
                ticket_id, 
                now_str, 
                str(sender), 
                str(sender_name), 
                str(message), 
                kategori, 
                ai_reply, 
                "Open / Automated", 
                "-", 
                "-"
            ]
            sheet.append_row(row_data)
            print(f"[GOOGLE SHEETS] Sukses mencatat tiket #{ticket_id}")
        except Exception as e:
            print(f"[SHEETS ERROR] Gagal mencatat log ke Google Sheets: {e}")
            
    threading.Thread(target=run).start()

# ----------------------------------------------------
# 3. ENDPOINTS FASTAPI
# ----------------------------------------------------
@app.api_route("/", methods=["GET", "HEAD"])
def read_root():
    return {"status": "IT Support Bot Webhook Service is Running"}

@app.api_route("/health", methods=["GET", "HEAD"])
def health_check():
    return {"status": "healthy"}

@app.post("/webhook")
async def handle_whatsapp(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {"status": "invalid json"}
    
    sender = data.get("sender") or data.get("from") or data.get("phone")
    message = data.get("message") or data.get("text")
    sender_name = data.get("name") or data.get("pushname") or "Client WA"
    
    if not message or not sender:
        return {"status": "ignored"}
    
    # Abaikan jika pesan berasal dari nomor alert sendiri agar tidak looping
    if ALERT_PHONE_NUMBER and str(sender) == str(ALERT_PHONE_NUMBER):
        return {"status": "ignored_self_alert"}
    
    print(f"\n--- PESAN MASUK ---")
    print(f"Dari: {sender_name} ({sender})")
    print(f"Pesan: {message}")

    # 1. KIRIM NOTIFIKASI SMS_ALERT UNTUK *SEMUA* PESAN MASUK (PRIORITAS UTAMA)
    if ALERT_PHONE_NUMBER:
        threading.Thread(
            target=send_simple_wa_alert, 
            args=(str(sender), str(sender_name))
        ).start()

    # 2. Olah Pesan dengan Gemini AI untuk Klasifikasi
    models_to_try = ['gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-2.5-flash', 'gemini-1.5-flash']
    response = None
    kategori = "Pribadi/Abaikan"
    ai_reply = ""
    
    for model_name in models_to_try:
        try:
            response = ai_client.models.generate_content(
                model=model_name,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.1,
                ),
                contents=message
            )
            if response and response.text:
                print(f"[GEMINI SUCCESS] Diproses oleh model: {model_name}")
                break
        except Exception as e:
            print(f"[GEMINI FALLBACK] Model {model_name} error/unavailable: {e}")
            continue

    if response and response.text:
        try:
            raw_text = response.text.strip()
            if "```" in raw_text:
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
            raw_text = raw_text.strip()
            
            ai_data = json.loads(raw_text)
            kategori = ai_data.get("kategori", "Pribadi/Abaikan")
            ai_reply = ai_data.get("jawaban", "")
        except Exception as e:
            print(f"[PARSE ERROR] Gagal parse JSON Gemini: {e}")
            return {"status": "alert_sent_unparsed"}

    # 3. JIKA CHAT PRIBADI / SAPAAN -> STOP DI SINI (AI TIDAK MEMBALAS & TIDAK MENCATAT LOG)
    if kategori == "Pribadi/Abaikan" or not ai_reply.strip():
        print(f"[FILTER] Pesan pribadi dari {sender}. Alert terkirim, balasan AI diabaikan.")
        return {"status": "alert_sent_personal_ignored"}

    # 4. JIKA KELUHAN TEKNIS -> BALAS DENGAN TIKET & CATAT LOG
    ticket_id = generate_ticket_number()
    formatted_reply = f"🎫 *[TIKET #{ticket_id}]*\n\n{ai_reply}"

    if FONNTE_TOKEN:
        print(f"[REPLY] Membalas tiket ke client {sender}...")
        send_whatsapp_reply(str(sender), formatted_reply)

    log_to_sheets_async(ticket_id, sender, sender_name, message, kategori, ai_reply)

    return {"ticket_id": ticket_id, "target": sender, "message": formatted_reply}
