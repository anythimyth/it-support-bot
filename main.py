import os
import json
import random
import string
import threading
import gspread
import requests
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
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

def log_to_sheets_async(ticket_id, sender, sender_name, message, image_url, kategori, ai_reply):
    """Mencatat 11 kolom log tiket ke Google Sheets secara asinkron"""
    def run():
        try:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            row_data = [
                ticket_id,                              # A: No. Tiket
                now_str,                                # B: Waktu
                str(sender),                            # C: No. HP Pengirim
                str(sender_name),                       # D: Nama Client / Toko
                str(message) if message else "-",       # E: Pesan Masuk
                str(image_url) if image_url else "-",   # F: Lampiran Foto (URL)
                kategori,                               # G: Kategori Kendala
                ai_reply,                               # H: Solusi AI / Troubleshooting
                "Open / Automated",                     # I: Status Tiket
                "-",                                    # J: Teknisi PIC
                "-"                                     # K: Catatan Penyelesaian
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

    # Catat Log Tiket ke Google Sheets (11 Kolom)
    log_to_sheets_async(ticket_id, sender, sender_name, message, image_url, kategori, ai_reply)

# ----------------------------------------------------
# 3. ENDPOINTS FASTAPI & WEB DASHBOARD
# ----------------------------------------------------
@app.api_route("/", methods=["GET", "HEAD"])
def read_root():
    return {"status": "IT Support Bot Webhook Service is Running"}

@app.api_route("/health", methods=["GET", "HEAD"])
def health_check():
    return {"status": "healthy"}

@app.get("/dashboard", response_class=HTMLResponse)
def view_dashboard():
    try:
        records = sheet.get_all_values()
        if not records or len(records) <= 1:
            return "<h2>Google Sheets Kosong atau Belum Ada Tiket</h2>"
        
        rows = records[1:]
        
        total_tickets = len(rows)
        open_tickets = sum(1 for r in rows if len(r) > 8 and "Open" in r[8])
        closed_tickets = sum(1 for r in rows if len(r) > 8 and "Closed" in r[8])
        
        table_rows_html = ""
        for idx, r in enumerate(reversed(rows), start=1):
            row_idx = len(rows) - idx + 2
            ticket_id = r[0] if len(r) > 0 else "-"
            waktu = r[1] if len(r) > 1 else "-"
            sender_name = r[3] if len(r) > 3 else "-"
            pesan = r[4] if len(r) > 4 else "-"
            image_url = r[5] if len(r) > 5 else "-"
            kategori = r[6] if len(r) > 6 else "-"
            status = r[8] if len(r) > 8 else "Open / Automated"
            pic = r[9] if len(r) > 9 else "-"
            catatan = r[10] if len(r) > 10 else "-"
            
            img_html = f'<a href="{image_url}" target="_blank" class="text-blue-500 underline font-semibold">Lihat Foto</a>' if image_url.startswith("http") else "-"
            status_color = "bg-yellow-100 text-yellow-800" if "Open" in status else ("bg-blue-100 text-blue-800" if "Progress" in status else "bg-green-100 text-green-800")
            
            table_rows_html += f"""
            <tr class="border-b hover:bg-gray-50">
                <td class="p-3 font-semibold text-gray-700">{ticket_id}</td>
                <td class="p-3 text-xs text-gray-500">{waktu}</td>
                <td class="p-3">{sender_name}</td>
                <td class="p-3 max-w-xs truncate">{pesan}</td>
                <td class="p-3">{img_html}</td>
                <td class="p-3"><span class="px-2 py-1 text-xs font-semibold rounded bg-gray-200">{kategori}</span></td>
                <td class="p-3"><span class="px-2 py-1 text-xs font-semibold rounded {status_color}">{status}</span></td>
                <td class="p-3">{pic}</td>
                <td class="p-3 text-xs">{catatan}</td>
                <td class="p-3">
                    <form action="/update-ticket" method="post" class="flex flex-col gap-1">
                        <input type="hidden" name="row_idx" value="{row_idx}">
                        <select name="status" class="text-xs p-1 border rounded bg-white">
                            <option value="Open / Automated" {"selected" if "Open" in status else ""}>Open</option>
                            <option value="In Progress" {"selected" if "Progress" in status else ""}>In Progress</option>
                            <option value="Closed" {"selected" if "Closed" in status else ""}>Closed</option>
                        </select>
                        <input type="text" name="pic" placeholder="Teknisi PIC" value="{pic if pic != '-' else ''}" class="text-xs p-1 border rounded">
                        <input type="text" name="catatan" placeholder="Catatan Penyelesaian" value="{catatan if catatan != '-' else ''}" class="text-xs p-1 border rounded">
                        <button type="submit" class="bg-blue-600 text-white text-xs px-2 py-1 rounded hover:bg-blue-700 transition">Update</button>
                    </form>
                </td>
            </tr>
            """

        html_content = f"""
        <!DOCTYPE html>
        <html lang="id">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Dashboard IT Support Tiket</title>
            <script src="https://cdn.tailwindcss.com"></script>
        </head>
        <body class="bg-gray-100 p-6">
            <div class="max-w-7xl mx-auto">
                <h1 class="text-2xl font-bold mb-4 text-gray-800">🛠️ Dashboard Status Tiket IT Support</h1>
                
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
                    <div class="bg-white p-4 rounded shadow">
                        <p class="text-gray-500 text-sm">Total Tiket</p>
                        <p class="text-2xl font-bold text-gray-800">{total_tickets}</p>
                    </div>
                    <div class="bg-white p-4 rounded shadow border-l-4 border-yellow-400">
                        <p class="text-gray-500 text-sm">Tiket Open</p>
                        <p class="text-2xl font-bold text-yellow-600">{open_tickets}</p>
                    </div>
                    <div class="bg-white p-4 rounded shadow border-l-4 border-green-500">
                        <p class="text-gray-500 text-sm">Tiket Closed</p>
                        <p class="text-2xl font-bold text-green-600">{closed_tickets}</p>
                    </div>
                </div>

                <div class="bg-white rounded shadow overflow-x-auto">
                    <table class="w-full text-left text-sm">
                        <thead class="bg-gray-800 text-white text-xs uppercase">
                            <tr>
                                <th class="p-3">No. Tiket</th>
                                <th class="p-3">Waktu</th>
                                <th class="p-3">Client</th>
                                <th class="p-3">Pesan</th>
                                <th class="p-3">Foto</th>
                                <th class="p-3">Kategori</th>
                                <th class="p-3">Status</th>
                                <th class="p-3">Teknisi</th>
                                <th class="p-3">Catatan</th>
                                <th class="p-3">Aksi Update</th>
                            </tr>
                        </thead>
                        <tbody>
                            {table_rows_html}
                        </tbody>
                    </table>
                </div>
            </div>
        </body>
        </html>
        """
        return html_content
    except Exception as e:
        return f"<h2>Error loading dashboard: {e}</h2>"

@app.post("/update-ticket")
def update_ticket(row_idx: int = Form(...), status: str = Form(...), pic: str = Form(...), catatan: str = Form(...)):
    try:
        sheet.update_cell(row_idx, 9, status)
        sheet.update_cell(row_idx, 10, pic.strip() if pic.strip() else "-")
        sheet.update_cell(row_idx, 11, catatan.strip() if catatan.strip() else "-")
        return HTMLResponse(content="<script>window.location.href='/dashboard';</script>")
    except Exception as e:
        return f"<h2>Gagal memperbarui tiket: {e}</h2>"

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

    threading.Thread(
        target=process_message_async, 
        args=(str(sender), str(sender_name), str(message), image_url)
    ).start()

    return {"status": "processing"}
