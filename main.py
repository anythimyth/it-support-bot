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
Klasifikasikan pesan (teks dan/atau foto yang dikirimkan) dan berikan balasan HANYA jika berisi KELUHAN TEKNIS IT SUPPORT / PEKERJAAN.

Aturan Klasifikasi & Analisis:
1. Jika pengguna mengirim SAPAAN / CHAT PRIBADI / OBROLAN BIASA (contoh: "Halo", "P", "Tes", "Lagi apa", "Siang"):
   - Set "kategori": "Pribadi/Abaikan"
   - Set "jawaban": ""

2. Jika pengguna mengirim FOTO/GAMBAR (layar error POS, printer macet, kabel/router):
   - Analisis teks, kode error, atau kondisi fisik yang terlihat pada gambar.
   - Berikan 2-3 langkah troubleshooting yang ringkas, jelas, dan mudah diikuti berdasarkan temuan foto dan pesan teksnya.
   - Set "kategori" ke salah satu pilihan: "Hardware", "Network", "POS", "Software", atau "Lainnya".

3. Jika pengguna mengirim KELUHAN TEKNIS TEKS:
   - Berikan 2-3 langkah troubleshooting yang ringkas dan jelas.
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

def fetch_image_bytes(url: str):
    """Mengunduh gambar dari URL Fonnte untuk dikirimkan ke Gemini Vision"""
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            content_type = res.headers.get("content-type", "image/jpeg")
            if "image" not in content_type:
                content_type = "image/jpeg"
            return res.content, content_type
    except Exception as e:
        print(f"[IMAGE FETCH ERROR] Gagal mengunduh gambar dari {url}: {e}")
    return None, None

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

def process_message_async(sender: str, sender_name: str, message: str, image_url: str = None):
    """Proses AI Vision/Text dan Pengiriman Balasan Berjalan di Background Thread"""
    contents = []
    
    # 1. Olah Gambar jika ada
    if image_url:
        img_bytes, mime_type = fetch_image_bytes(image_url)
        if img_bytes:
            contents.append(
                types.Part.from_bytes(
                    data=img_bytes,
                    mime_type=mime_type
                )
            )
            print(f"[VISION] Foto berhasil diunduh dari {image_url}")
            
    text_prompt = message.strip() if message else "Mohon analisa foto kendala teknis berikut."
    contents.append(text_prompt)

    # 2. Olah Pesan dengan Gemini AI Multimodal
    models_to_try = ['gemini-3.5-flash-lite', 'gemini-3.6-flash', 'gemini-3.8-flash']
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
                contents=contents
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
            return

    # 3. FILTER CHAT PRIBADI: JIKA CHAT PRIBADI/SAPAAN -> ABAIKAN TANPA BALASAN
    if kategori == "Pribadi/Abaikan" or not ai_reply.strip():
        print(f"[FILTER] Pesan dari {sender} dikategori Chat Pribadi. Diabaikan oleh AI.")
        return

    # 4. KELUHAN TEKNIS DIKONFIRMASI -> GENERATE TIKET (FORMAT BARU) & BALAS CLIENT
    ticket_id = generate_ticket_number()
    formatted_reply = f"🎫 *[Anythimyth_AI TIKET #{ticket_id}]*\n\n{ai_reply}"

    if FONNTE_TOKEN:
        print(f"[REPLY] Membalas pesan tiket ke client {sender}...")
        send_whatsapp_reply(str(sender), formatted_reply)

    # Catat Log Tiket ke Google Sheets
    log_to_sheets_async(ticket_id, sender, sender_name, message, kategori, ai_reply)

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
    message = data.get("message") or data.get("text") or ""
    sender_name = data.get("name") or data.get("pushname") or "Client WA"
    image_url = data.get("url") or data.get("file") or data.get("filename")
    
    if not message and not image_url:
        return {"status": "ignored"}
    
    print(f"\n--- PESAN MASUK ---")
    print(f"Dari: {sender_name} ({sender})")
    print(f"Pesan: {message} | Image URL: {image_url}")

    # Jalankan Seluruh Proses AI & Balasan di Background Thread
    threading.Thread(
        target=process_message_async, 
        args=(str(sender), str(sender_name), str(message), image_url)
    ).start()

    return {"status": "processing"}
