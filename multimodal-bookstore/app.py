import os
import io
import json
import base64
import requests
from PIL import Image
from flask import Flask, render_template, request, jsonify, send_from_directory
import pytesseract
from dotenv import load_dotenv

# ================== CONFIG ==================
load_dotenv()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "static", "data", "books.json")

with open(DATA_PATH, "r", encoding="utf-8") as f:
    BOOKS = json.load(f)

# ================== UTILS ==================
def find_books_by_text(text):
    text = (text or "").lower()
    found = []
    for b in BOOKS:
        for k in b.get("keywords", []):
            if k in text:
                found.append(b)
                break
    return found

def ocr_image(pil_img: Image.Image):
    try:
        txt = pytesseract.image_to_string(pil_img)
        return txt.strip()
    except Exception:
        return ""

def image_from_base64(data_url):
    header, b64 = data_url.split(",", 1)
    img_bytes = base64.b64decode(b64)
    return Image.open(io.BytesIO(img_bytes))

# ================== AI (OpenRouter) ==================
def ask_openrouter(messages, model="openai/gpt-4o-mini"):
    """
    Gọi OpenRouter để trả lời chat.
    model gợi ý: 
      - "openai/gpt-4o-mini" (nhanh, rẻ)
      - "anthropic/claude-3-sonnet"
      - "mistralai/mixtral-8x7b-instruct"
    """
    if not OPENROUTER_API_KEY:
        return "⚠️ Chưa có OPENROUTER_API_KEY trong .env"

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"⚠️ AI error (OpenRouter): {e}"

# ================== FLASK APP ==================
app = Flask(__name__, static_folder="static", template_folder="templates")

@app.route("/")
def index():
    return render_template("index.html", books=BOOKS)

# -------- Text Query (chat) --------
@app.route("/api/text-query", methods=["POST"])
def api_text_query():
    body = request.json or {}
    q = body.get("query", "")
    if not q:
        return jsonify({"ok": False, "reply": "❌ You have not entered a question."})

    # Tìm trong sách
    found = find_books_by_text(q)
    if found:
        lines = [f"{b['title']} — {b['price']} VND" for b in found]
        reply = "\n".join(lines)
        mode = "books"
    else:
        reply = ask_openrouter(
            [
                {"role": "system", "content": "Bạn là trợ lý AI trong ứng dụng bookstore, hãy trả lời thân thiện và súc tích."},
                {"role": "user", "content": q}
            ]
        )
        mode = "ai"

    return jsonify({"ok": True, "mode": mode, "reply": reply})

# -------- Upload Image (OCR + AI fallback) --------
@app.route("/api/upload-image", methods=["POST"])
def api_upload_image():
    if "file" in request.files:
        pil = Image.open(request.files["file"].stream).convert("RGB")
    else:
        data = request.json or {}
        b64 = data.get("image")
        if not b64:
            return jsonify({"status": "not image found"}), 400
        pil = image_from_base64(b64).convert("RGB")

    ocr_text = ocr_image(pil)
    found = find_books_by_text(ocr_text)

    if found:
        reply = {
            "mode": "books",
            "ocr_text": ocr_text,
            "found": [{"title": b["title"], "price": b["price"]} for b in found]
        }
    else:
        ai_reply = ask_openrouter(
            [
                {"role": "system", "content": "Bạn là trợ lý AI trong bookstore. Hãy phân tích nội dung OCR từ ảnh và gợi ý thông tin hữu ích."},
                {"role": "user", "content": f"Nội dung OCR: {ocr_text}"}
            ]
        )
        reply = {
            "mode": "ai",
            "ocr_text": ocr_text,
            "found": [],
            "ai_analysis": ai_reply
        }

    return jsonify({"ok": True, "reply": reply})

# -------- Static files --------
@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)

# ================== RUN ==================
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
