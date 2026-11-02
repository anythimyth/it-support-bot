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
    data = await request.json()
    
    sender = data.get("sender") or data.get("from")
    message = data.get("message") or data.get("text")
    sender_name = data.get("name") or "Client WA"
    
    if not message:
        return {"status": "ignored"}
    
    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
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
        kategori = "Lainnya"
        ai_reply = "Mohon maaf, terjadi kendala sistem saat memproses pesan. Tim IT akan segera menghubungi Anda."

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row_data = [now_str, sender, sender_name, message, kategori, ai_reply, "Automated / Pending", "-", "-", "-"]
    try:
        sheet.append_row(row_data)
    except Exception as e:
        print(f"Error logging to Sheets: {e}")

    if FONNTE_TOKEN:
        send_whatsapp_reply(sender, ai_reply)

    return {"target": sender, "message": ai_reply}
