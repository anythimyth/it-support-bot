import os
import json
import gspread
from fastapi import FastAPI, Request
from google import genai
from google.genai import types
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

# 1. Inisialisasi Gemini API Client (Gratis)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# 2. Inisialisasi Google Sheets API (Gratis)
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_json = json.loads(os.environ.get("GOOGLE_CREDENTIALS_JSON"))
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, SCOPE)
sheet_client = gspread.authorize(creds)
sheet = sheet_client.open("IT_Support_Log").sheet1  # Nama file Google Sheets

# 3. System Prompt IT Support
SYSTEM_PROMPT = """
Anda adalah AI Assistant IT Support internal toko/perusahaan. 
Tugas Anda:
1. Analisis keluhan pengguna yang dikirim via WhatsApp.
2. Jika kendala umum (printer jam, wifi rmt, pos hang, pc mati), berikan 2-3 langkah penanganan awal (troubleshooting) yang singkat dan mudah dipahami.
3. Jawab dengan nada sopan, profesional, dan to the point.
"""

@app.post("/webhook")
async def handle_whatsapp(request: Request):
    data = await request.json()
    
    # Ambil data dari webhook WA Gateway (Contoh format Fonnte)
    sender = data.get("sender") or data.get("from")
    message = data.get("message") or data.get("text")
    
    if not message:
        return {"status": "ignored"}
    
    # Generate Balasan AI menggunakan Gemini
    response = ai_client.models.generate_content(
        model="gemini-2.5-flash",
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.3
        ),
        contents=message
    )
    ai_reply = response.text

    # Log Otomatis ke Google Sheets (Tanggal, Pengirim, Keluhan, Balasan AI, Status)
    try:
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([now, sender, message, ai_reply, "Automated / Pending"])
    except Exception as e:
        print(f"Error logging to Sheets: {e}")

    # Kembalikan response JSON ke WA Gateway untuk dikirim balik ke client
    return {
        "target": sender,
        "message": ai_reply
    }

@app.get("/")
def health_check():
    return {"status": "IT Support Bot is Running"}