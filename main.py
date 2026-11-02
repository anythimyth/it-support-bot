import os
import json
import gspread
import requests
from datetime import datetime
from fastapi import FastAPI, Request
from google import genai
from google.genai import types
from oauth2client.service_account import ServiceAccountCredentials

app = FastAPI()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
FONNTE_TOKEN = os.environ.get("FONNTE_TOKEN")

ai_client = genai.Client(api_key=GEMINI_API_KEY)

SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_json = json.loads(os.environ.get("GOOGLE_CREDENTIALS_JSON"))
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, SCOPE)
sheet_client = gspread.authorize(creds)
sheet = sheet_client.open("IT_Support_Log").sheet1

SYSTEM_PROMPT = """
Anda adalah AI Assistant IT Support internal toko/perusahaan. 
Klasifikasikan kendala ke salah satu kategori (Hardware/Network/POS/Software/Lainnya) dan berikan 2-3 langkah troubleshooting ringkas.
Jawab HANYA dalam format JSON:
{
  "kategori": "Hardware/Network/POS/Software/Lainnya",
  "jawaban": "Langkah troubleshooting..."
}
"""

def send_whatsapp_reply(target: str, text: str):
    url = "https://api.fonnte.com/send"
    headers = {"Authorization": FONNTE_TOKEN}
    payload = {
        "target": target,
        "message": text,
    }
    try:
        requests.post(url, headers=headers, data=payload)
    except Exception as e:
        print(f"Failed to send WA message: {e}")

# --- TAMBAHAN PENTING UNTUK FONNTE & HEALTH CHECK ---
@app.get("/")
@app.get("/webhook")
def health_check():
    """Merespon request GET dari Fonnte & Uptime Monitor agar tidak 404"""
    return {"status": "IT Support Bot Webhook is Active", "code": 200}

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
    
    # 1. Olah Pesan dengan Gemini AI (Auto-Fallback Multi-Model)
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
                print(f"Berhasil menggunakan model: {model_name}")
                break
        except Exception as e:
            print(f"Model {model_name} tidak dapat digunakan ({e}), mencoba model berikutnya...")
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
            ai_reply = ai_data.get("jawaban", "Terima kasih, kendala Anda sedang dicek tim IT.")
        except Exception:
            ai_reply = response.text.strip()
    else:
        # Fallback cadangan jika seluruh model mengalami kendala/traffic tinggi
        kategori = "Hardware"
        ai_reply = "1. Pastikan kabel daya printer dan LAN terpasang rapat.\n2. Buka penutup printer dan bersihkan sisa kertas macet.\n3. Matikan unit 10 detik lalu nyalakan kembali."

    # 2. Catat Log ke Google Sheets
    try:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row_data = [now_str, str(sender), str(sender_name), str(message), kategori, ai_reply, "Automated / Pending", "-", "-", "-"]
        sheet.append_row(row_data)
    except Exception as e:
        print(f"ERROR SHEETS: {e}")

    # 3. Kirim Balasan ke WhatsApp Pengirim
    if FONNTE_TOKEN:
        send_whatsapp_reply(str(sender), ai_reply)

    return {"target": sender, "message": ai_reply}
