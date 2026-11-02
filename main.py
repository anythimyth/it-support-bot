import os
import json
import gspread
from datetime import datetime
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
sheet = sheet_client.open("IT_Support_Log").sheet1  # Nama file Google Sheets Anda

# 3. System Prompt IT Support & Penentu Kategori
SYSTEM_PROMPT = """
Anda adalah AI Assistant IT Support internal toko/perusahaan. 
Tugas Anda:
1. Analisis keluhan pengguna yang dikirim via WhatsApp.
2. Klasifikasikan kendala ke salah satu kategori berikut: Hardware, Network, POS, Software, atau Lainnya.
3. Berikan 2-3 langkah penanganan awal (troubleshooting) yang singkat dan mudah dipahami.
4. Jawab HANYA dengan format JSON seperti ini:
{
  "kategori": "Hardware/Network/POS/Software/Lainnya",
  "jawaban": "Langkah troubleshooting..."
}
"""

@app.post("/webhook")
async def handle_whatsapp(request: Request):
    data = await request.json()
    
    # Ambil data dari webhook WA Gateway (Contoh format Fonnte)
    sender = data.get("sender") or data.get("from")
    message = data.get("message") or data.get("text")
    sender_name = data.get("name") or "Client WA"  # Nama pengirim jika ada
    
    if not message:
        return {"status": "ignored"}
    
    # Generate Balasan AI menggunakan Gemini
    try:
        response = ai_client.models.generate_content(
            model="gemini-3.6-flash",
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.2,
                response_mime_type="application/json"
            ),
            contents=message
        )
        ai_data = json.loads(response.text)
        kategori = ai_data.get("kategori", "Lainnya")
        ai_reply = ai_data.get("jawaban", "Terima kasih, kendala Anda sedang dicek tim IT.")
    except Exception as e:
        print(f"Error AI Processing: {e}")
        kategori = "Lainnya"
        ai_reply = "Mohon maaf, terjadi kendala sistem saat memproses pesan Anda. Tim IT akan segera menghubungi Anda."

    # Waktu pesan masuk
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 4. Simpan ke Google Sheets sesuai Struktur 10 Kolom
    # Format baris: [Timestamp, No. WA, Nama/Lokasi, Keluhan, Kategori, Jawaban AI, Status, Teknisi, Catatan, Waktu Selesai]
    row_data = [
        now_str,                 # Kolom A: Timestamp
        sender,                  # Kolom B: No. WhatsApp
        sender_name,             # Kolom C: Nama / Lokasi
        message,                 # Kolom D: Keluhan Client
        kategori,                # Kolom E: Kategori Kendala (Hasil AI)
        ai_reply,                # Kolom F: Jawaban AI
        "Automated / Pending",   # Kolom G: Status Tiket
        "-",                     # Kolom H: Teknisi Penanggung Jawab
        "-",                     # Kolom I: Catatan Penanganan
        "-"                      # Kolom J: Waktu Selesai
    ]

    try:
        sheet.append_row(row_data)
    except Exception as e:
        print(f"Error logging to Sheets: {e}")

    # Kembalikan response JSON ke WA Gateway untuk dikirim balik ke WA client
    return {
        "target": sender,
        "message": ai_reply
    }

@app.get("/")
def health_check():
    return {"status": "IT Support Bot Webhook is Active"}
