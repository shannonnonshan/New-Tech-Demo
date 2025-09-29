import os
import io
import base64
import random
import uuid
import requests
from PIL import Image
from flask import Flask, render_template, request, jsonify, session
from flask_pymongo import PyMongo
from dotenv import load_dotenv
import pytesseract

# ================== CONFIG ==================
load_dotenv()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
MONGO_URI = os.environ.get("MONGO_URI")
CLIP_API_URL = os.environ.get("CLIP_API_URL")  # URL CLIP API

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "supersecret")
app.config["MONGO_URI"] = MONGO_URI
mongo = PyMongo(app)

# ================== UTILS ==================
def image_from_base64(data_url):
    if "," in data_url:
        _, b64 = data_url.split(",", 1)
    else:
        b64 = data_url
    return Image.open(io.BytesIO(base64.b64decode(b64)))

def remote_clip_match(pil_img, books, text_query=None, n_results=5):
    """Gọi CLIP API, trả về top n kết quả"""
    try:
        payload = {"books": books}
        if pil_img:
            buffered = io.BytesIO()
            pil_img.save(buffered, format="JPEG")
            img_b64 = "data:image/jpeg;base64," + base64.b64encode(buffered.getvalue()).decode()
            payload["image"] = img_b64
        if text_query:
            payload["query"] = text_query

        resp = requests.post(CLIP_API_URL + ("/clip-match-text" if text_query else "/clip-match"),
                             json=payload, timeout=60)
        resp.raise_for_status()
        result = resp.json()
        if result.get("ok") and result.get("matches"):
            return result["matches"][:n_results]
        return []
    except Exception as e:
        print(f"⚠️ Lỗi khi gọi CLIP API: {e}")
        return []

def make_json_safe(obj):
    if isinstance(obj, dict):
        return {k: make_json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_json_safe(i) for i in obj]
    elif isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    else:
        return str(obj)

def get_session_data():
    if "data" not in session:
        session["data"] = {}
    return session["data"]

def save_session_data(data):
    session["data"] = data

def get_session_history():
    if "history" not in session:
        session["history"] = [
            {
                "role": "system",
                "content": (
                    "Bạn là trợ lý AI của cửa hàng BooksLand, nhiệm vụ hỗ trợ khách hàng tìm sách, "
                    "giới thiệu sản phẩm và trả lời thân thiện, súc tích. Luôn xưng là trợ lý BooksLand."
                )
            }
        ]
    return session["history"]

def add_to_history(role, content):
    history = get_session_history()
    history.append({"role": role, "content": content})
    session["history"] = history

def call_openrouter(messages):
    if not OPENROUTER_API_KEY:
        raise Exception("⚠️ Chưa có OPENROUTER_API_KEY trong .env")
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {"model": "openai/gpt-4o-mini", "messages": messages}
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

# ================== PUSH BOOKS TO CLIP ==================
def push_books_to_clip():
    if not CLIP_API_URL:
        print("⚠️ Chưa cấu hình CLIP_API_URL, bỏ qua push sách")
        return
    try:
        books = list(mongo.db.books.find())
        for book in books:
            book["_id"] = str(book["_id"])
            # Thêm UUID để không trùng id trong CLIP
            book["clip_id"] = str(uuid.uuid4())
        if not books:
            print("⚠️ Mongo chưa có sách, bỏ qua push sách lên CLIP")
            return
        # Gọi API CLIP với query dummy để tạo embeddings
        resp = requests.post(CLIP_API_URL + "/clip-match-text",
                             json={"query": "dummy", "books": books}, timeout=120)
        if resp.status_code == 200:
            print(f"✅ Đã push {len(books)} sách lên CLIP API thành công")
        else:
            print(f"⚠️ Lỗi khi push sách: {resp.status_code}, {resp.text[:200]}")
    except Exception as e:
        print(f"⚠️ Exception khi push sách lên CLIP: {e}")

push_books_to_clip()

# ================== ROUTES ==================
@app.route("/api/query", methods=["POST"])
def api_query():
    pil_img = None
    body = request.json or {}

    # --- Nhận input ảnh ---
    if "file" in request.files:
        pil_img = Image.open(request.files["file"].stream).convert("RGB")
    elif body.get("image"):
        pil_img = image_from_base64(body["image"]).convert("RGB")

    # --- Nhận input text ---
    text_query = (body.get("query") or "").strip()

    # --- Phân loại input ---
    input_type = None
    if pil_img and text_query:
        input_type = "both"
    elif pil_img:
        input_type = "image"
    elif text_query:
        input_type = "text"

    # --- Load sách từ Mongo ---
    books = list(mongo.db.books.find())
    for book in books:
        book["_id"] = str(book["_id"])

    best_match, top_matches = None, []
    session_data = get_session_data()
    last_match = session_data.get("last_best_match")

    vague_queries = [
        "nó", "cuốn này", "sách đó", "giới thiệu",
        "giới thiệu về nó", "giới thiệu về cuốn này",
        "sách vừa rồi", "cuốn vừa nãy"
    ]

    # --- Xử lý CLIP ---
    try:
        if input_type == "text":
            payload = {"books": books, "query": text_query}
            resp = requests.post(f"{CLIP_API_URL}/clip-match-text", json=payload, timeout=60).json()
            top_matches = resp.get("matches", [])

        elif input_type == "image":
            buffered = io.BytesIO()
            pil_img.save(buffered, format="JPEG")
            img_b64 = "data:image/jpeg;base64," + base64.b64encode(buffered.getvalue()).decode()
            payload = {"books": books, "image": img_b64}
            resp = requests.post(f"{CLIP_API_URL}/clip-match", json=payload, timeout=60).json()
            top_matches = resp.get("matches", [])

        elif input_type == "both":
            buffered = io.BytesIO()
            pil_img.save(buffered, format="JPEG")
            img_b64 = "data:image/jpeg;base64," + base64.b64encode(buffered.getvalue()).decode()
            payload = {"books": books, "image": img_b64, "query": text_query}
            resp = requests.post(f"{CLIP_API_URL}/clip-match", json=payload, timeout=60).json()
            top_matches = resp.get("matches", [])
    except Exception as e:
        print(f"⚠️ Lỗi khi gọi CLIP API: {e}")

    # Lấy match đầu tiên nếu có
    if top_matches:
        best_match = top_matches[0]
        print(f"✅ CLIP Match: {best_match.get('title')} (score={best_match.get('score', 0):.2f})")

    # --- Nếu query mơ hồ, dùng last_match ---
    if input_type == "text" and any(vq in text_query.lower() for vq in vague_queries) and last_match:
        best_match = last_match
        print(f"✅ Reusing Last Match: {best_match.get('title')}")

    # --- Nếu không tìm thấy match, fallback GPT ---
    gpt_reply, cover_url = "", None
    if best_match:
        session_data["last_best_match"] = make_json_safe(best_match)
        save_session_data(session_data)
        cover_url = best_match.get("cover")
        book_title = best_match.get("title")
        book_author = best_match.get("author", "Unknown")
        book_price = best_match.get("price", 0)
        book_desc = best_match.get("description") or "(Chưa có mô tả)"
        fact = f"📖 Sách tìm thấy:\n- Tiêu đề: {book_title}\n- Tác giả: {book_author}\n- Giá: {book_price} VNĐ\n- Mô tả: {book_desc}"
        add_to_history("system", fact)
        if text_query:
            add_to_history("user", text_query)
        elif pil_img:
            add_to_history("user", "<Ảnh bìa sách>")

        try:
            gpt_res = call_openrouter(get_session_history())
            gpt_reply = gpt_res["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"⚠️ Lỗi GPT: {e}")
            gpt_reply = f"⚠️ Lỗi khi gọi GPT: {e}"
    else:
        # fallback GPT + gợi ý sách ngẫu nhiên
        add_to_history("user", text_query)
        try:
            gpt_res = call_openrouter(get_session_history())
            gpt_reply = gpt_res["choices"][0]["message"]["content"]
            suggested = random.sample(books, min(3, len(books)))
            gpt_reply += "\n\n📚 Bạn có thể tham khảo thêm: " + ", ".join([b["title"] for b in suggested])
            return jsonify({
                "ok": True,
                "reply": gpt_reply,
                "cover": None,
                "book": None,
                "suggested": [make_json_safe(b) for b in suggested]
            })
        except Exception as e:
            print(f"⚠️ Lỗi GPT fallback: {e}")
            gpt_reply = f"⚠️ Lỗi GPT: {e}"

    return jsonify({
        "ok": True,
        "reply": gpt_reply,
        "cover": cover_url,
        "book": make_json_safe(best_match) if best_match else None,
        "suggested": []
    })
@app.route("/")
def index():
    books = list(mongo.db.books.find())
    return render_template("index.html", books=books)

@app.route("/api/books", methods=["GET"])
def get_books():
    books = list(mongo.db.books.find())
    for book in books:
        book["_id"] = str(book["_id"])
    return jsonify({"ok": True, "books": [make_json_safe(book) for book in books]})

@app.route("/api/recommended", methods=["GET"])
def get_recommended():
    books = list(mongo.db.books.find())
    recommended = random.sample(books, min(6, len(books)))
    for book in recommended:
        book["_id"] = str(book["_id"])
    return jsonify({"ok": True, "books": [make_json_safe(book) for book in recommended]})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
