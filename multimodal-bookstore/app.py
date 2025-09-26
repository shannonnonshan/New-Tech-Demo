import os
import io
import json
import base64
import requests
from PIL import Image
from flask import Flask, render_template, request, jsonify, send_from_directory, session
import pytesseract
from dotenv import load_dotenv

# ================== CONFIG ==================
load_dotenv()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "static", "data", "books.json")

with open(DATA_PATH, "r", encoding="utf-8") as f:
    BOOKS = json.load(f)

# Flask secret key (để lưu session cho client)
app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "supersecret")

# ================== UTILS ==================
def ocr_image(pil_img: Image.Image):
    """Chạy OCR với pytesseract"""
    try:
        txt = pytesseract.image_to_string(pil_img)
        return txt.strip()
    except Exception:
        return ""

def image_from_base64(data_url):
    """Chuyển base64 -> PIL Image"""
    header, b64 = data_url.split(",", 1)
    img_bytes = base64.b64decode(b64)
    return Image.open(io.BytesIO(img_bytes))

# ================== SESSION MANAGER ==================
def get_session_history():
    """Lấy lịch sử hội thoại từ session Flask"""
    if "history" not in session:
        session["history"] = [
            {
                "role": "system",
                "content": (
                    "Bạn là trợ lý AI riêng của cửa hàng BooksLand, địa chỉ tại Thủ Đức. "
                    "Nhiệm vụ của bạn là hỗ trợ khách hàng tìm kiếm sách, "
                    "giới thiệu sản phẩm và đưa ra câu trả lời thân thiện, súc tích. "
                    "Luôn xưng là trợ lý của BooksLand."
                )
            }
        ]
    return session["history"]

def add_to_history(role, content):
    history = get_session_history()
    history.append({"role": role, "content": content})
    session["history"] = history

def reset_history():
    session.pop("history", None)

# ================== AI (OpenRouter) ==================
def ask_openrouter(messages, model="openai/gpt-4o-mini"):
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

# ================== ROUTES ==================
@app.route("/")
def index():
    return render_template("index.html", books=BOOKS)

# -------- Reset session --------
@app.route("/api/reset-session", methods=["POST"])
def api_reset_session():
    reset_history()
    return jsonify({"ok": True, "reply": "🔄 Đã reset hội thoại."})

# -------- Text Query (chat) --------
@app.route("/api/text-query", methods=["POST"])
def api_text_query():
    body = request.json or {}
    q = body.get("query", "")
    if not q:
        return jsonify({"ok": False, "reply": "❌ Bạn chưa nhập gì cả."})

    books_info = json.dumps(BOOKS, ensure_ascii=False)

    add_to_history("user", q)
    reply = ask_openrouter([
        {
            "role": "system",
            "content": (
                "Bạn là trợ lý AI của BooksLand (cửa hàng tại Thủ Đức). "
                "Dưới đây là dữ liệu JSON về sách. "
                "Hãy kiểm tra xem có sách nào phù hợp với câu hỏi khách hàng không. "
                "Nếu có, trả lời đúng tên và giá. "
                "Nếu không có thì trả lời: 'Không có trong cửa hàng BooksLand'."
            )
        },
        {"role": "system", "content": f"Dữ liệu books.json: {books_info}"},
        *get_session_history(),
        {"role": "user", "content": q}
    ])
    add_to_history("assistant", reply)

    return jsonify({"ok": True, "reply": reply})

# -------- Upload / Crop (OCR + AI) --------
@app.route("/api/query", methods=["POST"])
def api_query():
    if "file" in request.files:
        pil = Image.open(request.files["file"].stream).convert("RGB")
    else:
        data = request.json or {}
        b64 = data.get("image")
        if not b64:
            return jsonify({"ok": False, "reply": "❌ Không có ảnh nào được gửi."}), 400
        pil = image_from_base64(b64).convert("RGB")

    ocr_text = ocr_image(pil)
    books_info = json.dumps(BOOKS, ensure_ascii=False)

    add_to_history("user", f"OCR text từ ảnh: {ocr_text}")
    reply = ask_openrouter([
        {
            "role": "system",
            "content": (
                "Bạn là trợ lý AI của BooksLand (cửa hàng tại Thủ Đức). "
                "Dưới đây là dữ liệu JSON về sách. "
                "Hãy so khớp nội dung OCR với JSON. "
                "Nếu có sách phù hợp thì trả lời theo format: "
                "'Your image is a book called <tên sách> cost <giá> VND'. "
                "Nếu không có thì trả lời: 'Không tìm thấy sách nào phù hợp trong BooksLand'."
            )
        },
        {"role": "system", "content": f"Dữ liệu books.json: {books_info}"},
        *get_session_history(),
        {"role": "user", "content": f"Nội dung OCR: {ocr_text}"}
    ])
    add_to_history("assistant", reply)

    return jsonify({"ok": True, "reply": reply})

# -------- Static files --------
@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)

# ================== RUN ==================
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
