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
# 1. KONFIGURASI BOT & SECRETS (BACA DARI ENVIRONMENT)
# ----------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FONNTE_TOKEN = os.getenv("FONNTE_TOKEN")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# Group ID diambil dari Environment Variables agar aman dari public GitHub
GROUP_IT_SUPPORT = os.getenv("GROUP_IT_SUPPORT")  # Format: 120363xxxxxxxx@g.us
GROUP_PRIBADI = os.getenv("GROUP_PRIBADI")        # Format: 120363xxxxxxxx@g.us

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

Aturan Respon:
1. Jika pengguna mengirim SAPAAN / CHAT BIASA (contoh: "Halo", "Selamat Pagi", "P", "Tes", "Siang"):
   - Balas sapaan dengan ramah dan tanyakan kendala IT Support apa yang sedang dialami.
   - Set field "kategori": "Informasi/Sapaan".

2. Jika pengguna mengirim KELUHAN TEKNIS / TROUBLE (contoh: "Printer macet", "Aplikasi POS error", "WiFi kasir putus"):
   - Berikan 2-3 langkah troubleshooting yang ringkas, jelas, dan mudah diikuti.
   - Jika kendala memerlukan penanganan langsung, sampaikan bahwa Anda akan bantu jadwalkan kunjungan teknisi IT.
   - Set field "kategori" ke salah satu pilihan: "Hardware", "Network", "POS", "Software", atau "Lainnya".

Format Respon Wajib (HANYA JSON VALID):
{
  "kategori": "Informasi/Sapaan/Hardware/Network/POS/Software/Lainnya",
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
        requests.post(url, headers=headers, data=payload, timeout=10)
    except Exception as e:
        print(f"Error sending WA via Fonnte: {e}")

def send_group_notification(target_group: str, title: str, ticket_id: str, sender: str, sender_name: str, message: str, kategori: str):
    if not target_group:
        return
    group_text = (
        f"{title}\n\n"
        f"🎫 *No. Tiket:* #{ticket_id}\n"
        f"👤 *Pengirim:* {sender_name} ({sender})\n"
        f"📂 *Kategori:* {kategori}\n"
        f"💬 *Pesan:* \"{message}\""
    )
    send_whatsapp_reply(target_group, group_text)

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
        except Exception as e:
            print(f"Error logging to Google Sheets: {e}")
            
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
    
    # Abaikan pesan yang berasal dari grup notifikasi sendiri
    if str(sender) in [GROUP_IT_SUPPORT, GROUP_PRIBADI]:
        return {"status": "ignored_group_msg"}
    
    # 1. Generate Nomor Tiket Baru
    ticket_id = generate_ticket_number()
    
    # 2. Olah Pesan dengan Gemini AI (Auto-Fallback Mechanism)
    models_to_try = ['gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-2.5-flash', 'gemini-1.5-flash']
    response = None
    kategori = "Lainnya"
    ai_reply = ""
    
    for model_name in models_to_try:
        try:
            response = ai_client.models.generate_content(
                model=model_name,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.2,
                ),
                contents=message
            )
            if response and response.text:
                break
        except Exception:
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
            kategori = ai_data.get("kategori", "Lainnya")
            ai_reply = ai_data.get("jawaban", "Halo! Terima kasih telah menghubungi IT Support. Ada yang bisa kami bantu?")
        except Exception:
            ai_reply = response.text.strip()
            kategori = "Informasi/Sapaan"
    else:
        kategori = "Informasi/Sapaan"
        ai_reply = "Halo! Terima kasih telah menghubungi IT Support. Silakan sampaikan kendala teknis Anda."

    formatted_reply = f"🎫 *[TIKET #{ticket_id}]*\n\n{ai_reply}"

    # 3. Kirim Balasan Otomatis ke Pengirim Chat
    if FONNTE_TOKEN:
        send_whatsapp_reply(str(sender), formatted_reply)

        # 4. Kirim Notifikasi ke Grup Terkait (Dipisah Berdasarkan Kategori)
        if kategori in ["Informasi/Sapaan"]:
            if GROUP_PRIBADI:
                threading.Thread(
                    target=send_group_notification, 
                    args=(GROUP_PRIBADI, "💬 *[CHAT PRIBADI / SAPAAN]*", ticket_id, str(sender), str(sender_name), str(message), kategori)
                ).start()
        else:
            if GROUP_IT_SUPPORT:
                threading.Thread(
                    target=send_group_notification, 
                    args=(GROUP_IT_SUPPORT, "🚨 *[TIKET MASUK IT SUPPORT]*", ticket_id, str(sender), str(sender_name), str(message), kategori)
                ).start()

    # 5. Catat Log ke Google Sheets di Background Thread
    log_to_sheets_async(ticket_id, sender, sender_name, message, kategori, ai_reply)

    return {"ticket_id": ticket_id, "target": sender, "message": formatted_reply}
