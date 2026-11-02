import os
import json
import random
import string
import gspread
import requests
from datetime import datetime
from fastapi import FastAPI, Request
from google import genai
from google.genai import types
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

# ----------------------------------------------------
# 1. KONFIGURASI GEMINI AI & GOOGLE SHEETS
# ----------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
FONNTE_TOKEN = os.getenv("FONNTE_TOKEN")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# Inisialisasi Google GenAI Client
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# Inisialisasi Google Sheets Client via Service Account
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
   - Set field "kategori" ke salah satu pilihan: "Hardware", "Network", "POS", "Software", atau "Lainnya".

Format Respon Wajib (HANYA JSON VALID):
{
  "kategori": "Informasi/Sapaan/Hardware/Network/POS/Software/Lainnya",
  "jawaban": "Isi pesan balasan Anda di sini"
}
"""

def generate_ticket_number():
    """Membuat Nomor Tiket Unik berbasis Tanggal + Random Alfanumerik"""
    date_str = datetime.now().strftime("%Y%m%d")
    random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"TCK-{date_str}-{random_str}"

def send_whatsapp_reply(target: str, message: str):
    """Mengirim Balasan Pesan via Fonnte WA Gateway API"""
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

# ----------------------------------------------------
# 3. ENDPOINTS FASTAPI
# ----------------------------------------------------
@app.get("/")
def read_root():
    return {"status": "IT Support Bot Webhook Service is Running"}

@app.post("/webhook")
async def handle_whatsapp(request: Request):
    try:
        data = await request.json()
    except Exception:
        return {"status": "invalid json"}
    
    # Tangkap data sender dan message dari berbagai format payload Fonnte
    sender = data.get("sender") or data.get("from") or data.get("phone")
    message = data.get("message") or data.get("text")
    sender_name = data.get("name") or data.get("pushname") or "Client WA"
    
    if not message or not sender:
        return {"status": "ignored"}
    
    # 1. Generate Nomor Tiket Baru
    ticket_id = generate_ticket_number()
    
    # 2. Process AI Reply (Auto-Fallback Mechanism)
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
                print(f"Sukses memproses dengan model: {model_name}")
                break
        except Exception as e:
            print(f"Model {model_name} error/unresponsive: {e}, mencoba model berikutnya...")
            continue

    # Parsing Respon dari AI
    if response and response.text:
        try:
            raw_text = response.text.strip()
            # Pembersihan Markdown Formatting ```json ... ```
            if "```" in raw_text:
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
            raw_text = raw_text.strip()
            
            ai_data = json.loads(raw_text)
            kategori = ai_data.get("kategori", "Lainnya")
            ai_reply = ai_data.get("jawaban", "Halo! Terima kasih telah menghubungi IT Support. Ada yang bisa kami bantu?")
        except Exception:
            # Fallback jika parsing JSON gagal
            ai_reply = response.text.strip()
            kategori = "Informasi/Sapaan"
    else:
        # Fallback Cadangan Netral jika seluruh API Google sibuk/offline
        kategori = "Informasi/Sapaan"
        ai_reply = "Halo! Terima kasih telah menghubungi IT Support. Silakan sampaikan kendala teknis Anda (seperti POS, Printer, WiFi, atau Scanner)."

    # Format Pesan Akhir yang Ditampilkan di WhatsApp
    formatted_reply = f"🎫 *[TIKET #{ticket_id}]*\n\n{ai_reply}"

    # 3. Log ke Google Sheets
    try:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Struktur Kolom: [Ticket ID, Waktu, No. WA, Nama Pengirim, Pesan Masuk, Kategori, Balasan AI, Status, Teknisi, Catatan]
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

    # 4. Kirim Balasan Otomatis ke WhatsApp Pengirim
    if FONNTE_TOKEN:
        send_whatsapp_reply(str(sender), formatted_reply)

    return {"ticket_id": ticket_id, "target": sender, "message": formatted_reply}
