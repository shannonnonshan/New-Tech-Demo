
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
import secrets


# ================== CONFIG ==================
load_dotenv()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
MONGO_URI = os.environ.get("MONGO_URI")
CLIP_API_URL = os.environ.get("CLIP_API_URL")  # URL CLIP API

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = secrets.token_hex(16)
app.config["MONGO_URI"] = MONGO_URI
mongo = PyMongo(app)

# ================== UTILS ==================
def image_from_base64(data_url):
    if "," in data_url:
        _, b64 = data_url.split(",", 1)
    else:
        b64 = data_url
    return Image.open(io.BytesIO(base64.b64decode(b64)))

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

def clear_session_data():
    session.clear()  # Xóa toàn bộ session

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
            book["clip_id"] = str(uuid.uuid4())
        if not books:
            print("⚠️ Mongo chưa có sách, bỏ qua push sách lên CLIP")
            return
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
    body = request.json or {}

    # --- Reset session nếu có flag ---
    if body.get("reset"):
        clear_session_data()
        return jsonify({"ok": True, "msg": "Session đã được reset."})

    pil_img = None
    text_query = (body.get("query") or "").strip()

    # --- Nhận input ảnh ---
    if "file" in request.files:
        pil_img = Image.open(request.files["file"].stream).convert("RGB")
    elif body.get("image"):
        pil_img = image_from_base64(body["image"]).convert("RGB")

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

    session_data = get_session_data()
    last_match = session_data.get("last_best_match")

    vague_queries = ["nó", "cuốn này", "sách đó", "giới thiệu",
                     "giới thiệu về nó", "giới thiệu về cuốn này",
                     "sách vừa rồi", "cuốn vừa nãy"]
    specific_keywords = ["có biết", "review", "tóm tắt", "nội dung", "sách"]
    is_specific_book_query = text_query and any(kw in text_query.lower() for kw in specific_keywords)

    # --- Helper ---
    def find_books_by_text(query):
        matched = []
        for book in books:
            if any(word.lower() in book.get("title", "").lower() for word in query.lower().split()):
                matched.append(book)
        return matched

    def is_book_in_db(match):
        return match and any(str(book["_id"]) == str(match.get("_id")) for book in books)

    def update_session_if_new_book(book):
        last_match = session_data.get("last_best_match")
        if not last_match or str(last_match.get("_id")) != str(book.get("_id")):
            session_data["last_best_match"] = make_json_safe(book)
            save_session_data(session_data)

    best_match = None
    cover_url = None

    # ================== 1. Query review / tóm tắt / nội dung sách (text) ==================
    if is_specific_book_query and input_type != "image":
        matched_books = find_books_by_text(text_query)
        if matched_books:
            book = matched_books[0]
            cover_url = book.get("cover")
            add_to_history("system", f"Viết review ngắn cho cuốn sách '{book.get('title')}'. "
                                      "Tự mô tả nội dung sách và thông báo rằng sách này hiện có trong cửa hàng.")
            add_to_history("user", text_query)
            update_session_if_new_book(book)
        else:
            book = None
            cover_url = None
            add_to_history("system", f"Viết review ngắn cho cuốn sách '{text_query}'. "
                                      "Tự mô tả nội dung sách và thông báo rằng sách này hiện chưa có trong cửa hàng.")
            add_to_history("user", text_query)

        try:
            gpt_res = call_openrouter(get_session_history())
            gpt_reply = gpt_res["choices"][0]["message"]["content"]
        except Exception as e:
            gpt_reply = f"⚠️ Lỗi GPT: {e}"

        return jsonify({
            "ok": True,
            "reply": gpt_reply,
            "cover": cover_url,
            "book": make_json_safe(book) if book else None,
            "suggested": []
        })

    # ================== 2. Query image / CLIP / both ==================
    top_matches = []
    try:
        payload = {"books": books}
        if input_type in ["image", "both"]:
            buffered = io.BytesIO()
            pil_img.save(buffered, format="JPEG")
            img_b64 = "data:image/jpeg;base64," + base64.b64encode(buffered.getvalue()).decode()
            payload["image"] = img_b64
        if input_type in ["text", "both"] and text_query:
            payload["query"] = text_query

        resp = requests.post(f"{CLIP_API_URL}/clip-match", json=payload, timeout=60).json()
        top_matches = resp.get("matches", [])
    except Exception as e:
        print(f"⚠️ Lỗi khi gọi CLIP API: {e}")

    if top_matches:
        best_match = top_matches[0]
        if is_book_in_db(best_match):
            cover_url = best_match.get("cover")
        update_session_if_new_book(best_match)
        add_to_history("user", text_query or "<Ảnh bìa sách>")
        add_to_history("system", f"Thông tin sách: {best_match.get('title')}")

        try:
            gpt_res = call_openrouter(get_session_history())
            gpt_reply = gpt_res["choices"][0]["message"]["content"]
        except Exception as e:
            gpt_reply = f"⚠️ Lỗi GPT: {e}"
    else:
        # fallback GPT + gợi ý sách ngẫu nhiên
        add_to_history("user", text_query or "<Ảnh bìa sách>")
        suggested = random.sample(books, min(3, len(books)))
        try:
            gpt_res = call_openrouter(get_session_history())
            gpt_reply = gpt_res["choices"][0]["message"]["content"]
            gpt_reply += "\n\n📚 Bạn có thể tham khảo thêm: " + ", ".join([b["title"] for b in suggested])
        except Exception as e:
            gpt_reply = f"⚠️ Lỗi GPT fallback: {e}"

    return jsonify({
        "ok": True,
        "reply": gpt_reply,
        "cover": cover_url,
        "book": make_json_safe(best_match) if best_match else None,
        "suggested": []
    })

# ================== API CLEAR SESSION ==================
@app.route("/api/session/clear", methods=["POST"])
def clear_session():
    clear_session_data()
    resp = jsonify({"ok": True, "msg": "Session đã được reset."})
    resp.set_cookie("session", "", expires=0)  # xoá cookie session
    return resp

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

