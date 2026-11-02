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

# Group ID Notifikasi IT Support (Format: 120363xxxxxxxx@g.us)
GROUP_IT_SUPPORT = os.getenv("GROUP_IT_SUPPORT")

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
        "
