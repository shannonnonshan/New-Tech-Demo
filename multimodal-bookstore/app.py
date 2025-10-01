
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
from datetime import timedelta
from bson import ObjectId
from difflib import get_close_matches

# ================== CONFIG ==================
load_dotenv()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
MONGO_URI = os.environ.get("MONGO_URI")
CLIP_API_URL = os.environ.get("CLIP_API_URL")  # URL CLIP API

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = secrets.token_hex(16)
app.config["MONGO_URI"] = MONGO_URI
app.config["SESSION_PERMANENT"] = False
mongo = PyMongo(app)

# ================== UTILS ==================
def image_from_base64(data_url):
    if "," in data_url:
        _, b64 = data_url.split(",", 1)
    else:
        b64 = data_url
    return Image.open(io.BytesIO(base64.b64decode(b64)))
def find_book_by_title(text_query, books):
    query_lower = text_query.lower()

    # 1. Regex trực tiếp (không phân biệt hoa thường)
    book_match = mongo.db.books.find_one({
        "title": {"$regex": text_query, "$options": "i"}
    })
    if book_match:
        return book_match

    # Chuẩn bị danh sách title (lowercase để fuzzy match)
    all_titles = [b["title"] for b in books]
    all_titles_lower = [t.lower() for t in all_titles]

    # 2. Fuzzy match toàn bộ tên
    close = get_close_matches(query_lower, all_titles_lower, n=1, cutoff=0.6)
    if close:
        # tìm lại tên gốc khớp lowercase
        idx = all_titles_lower.index(close[0])
        return mongo.db.books.find_one({"title": all_titles[idx]})

    # 3. Fuzzy match từng từ
    words = query_lower.split()
    candidates = []
    for word in words:
        close_word = get_close_matches(word, all_titles_lower, n=3, cutoff=0.6)
        candidates.extend(close_word)

    if candidates:
        # chọn tên xuất hiện nhiều nhất
        best_guess = max(set(candidates), key=candidates.count)
        idx = all_titles_lower.index(best_guess)
        return mongo.db.books.find_one({"title": all_titles[idx]})

    return None
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
    pil_img = None
    text_query = ""

    # ================== 0. Nhận input ==================
    if request.is_json:
        body = request.get_json() or {}
        text_query = (body.get("query") or "").strip()
        if body.get("image"):
            pil_img = image_from_base64(body["image"]).convert("RGB")
    else:
        body = request.form
        text_query = (body.get("query") or "").strip()
        if "file" in request.files:
            pil_img = Image.open(request.files["file"].stream).convert("RGB")

    # ================== 1. Reset session ==================
    if text_query.lower() == "reset":
        clear_session_data()
        return jsonify({"ok": True, "msg": "Session đã được reset."})

    # ================== 2. Phân loại input ==================
    input_type = "unknown"
    if pil_img and text_query:
        input_type = "both"
    elif pil_img:
        input_type = "image"
    elif text_query:
        input_type = "text"

    # ================== 3. Load sách từ Mongo ==================
    books = list(mongo.db.books.find())
    for book in books:
        book["_id"] = str(book["_id"])

    session_data = get_session_data()

    # ================== 4. Helper session ==================
    def update_session_if_new_book(book):
        last_match = session_data.get("last_best_match")
        if not last_match or str(last_match.get("_id")) != str(book.get("_id")):
            session_data["last_best_match"] = make_json_safe(book)
            save_session_data(session_data)

    # ================== 5. Greeting intent ==================
    greetings = ["hi", "hello", "chào", "hey", "xin chào"]
    if text_query.lower() in greetings:
        add_to_history("user", text_query)
        reply = "Chào bạn 👋! Mình là trợ lý BooksLand, có thể giúp bạn tìm sách hoặc giới thiệu sản phẩm."
        add_to_history("assistant", reply)
        return jsonify({
            "ok": True,
            "reply": reply,
            "cover": None,
            "covers": [],
            "book": None,
            "suggested": []
        })

    # ================== 6. Xử lý Text-only ==================
    if text_query and not pil_img:
        query_lower = text_query.lower()

        # --- Xử lý xác nhận "có" (mua) ---
        confirm_words_yes = ["có", "đúng rồi", "ok", "mua", "chuẩn", "phải"]
        if query_lower in confirm_words_yes:
            last_book = session_data.get("last_best_match")
            if last_book:
                book_in_store = mongo.db.books.find_one({"_id": ObjectId(last_book["_id"])})
                if book_in_store:
                    book_in_store["_id"] = str(book_in_store["_id"])
                    reply = f"👍 Trong tiệm có bán '{book_in_store['title']}' của {book_in_store['author']}, giá {book_in_store.get('price', 'chưa có giá')}."
                    return jsonify({
                        "ok": True,
                        "reply": reply,
                        "cover": book_in_store.get("cover"),
                        "covers": [book_in_store.get("cover")] if book_in_store.get("cover") else [],
                        "book": make_json_safe(book_in_store),
                        "suggested": []
                    })
            return jsonify({"ok": False, "reply": "Bạn muốn mua sách nào nhỉ? Hãy chọn lại nhé."})

        # --- Xử lý xác nhận "không" ---
        confirm_words_no = ["không", "không mua", "không phải", "sai", "nhầm"]
        if query_lower in confirm_words_no:
            last_suggested = session_data.get("last_suggested", [])
            if last_suggested:
                reply = "Không sao 😊. Bạn thử xem thêm mấy cuốn này nhé:"
                return jsonify({
                    "ok": True,
                    "reply": reply,
                    "cover": last_suggested[0].get("cover") if last_suggested[0].get("cover") else None,
                    "covers": [b.get("cover") for b in last_suggested if b.get("cover")],
                    "book": None,
                    "suggested": last_suggested
                })
            return jsonify({"ok": True, "reply": "Vậy mình có thể gợi ý vài cuốn khác cho bạn không?"})

        # --- Xử lý hỏi giá ---
        price_keywords = ["giá", "cost", "bao nhiêu", "mấy tiền", "giá bao nhiêu", "nhiêu"]
        if any(word in query_lower for word in price_keywords):
    # Tìm sách gần giống (regex + fuzzy + từ khóa)
            last_book = session_data.get("last_best_match")
            book_match = find_book_by_title(text_query, books)

            if book_match:
                book_match["_id"] = str(book_match["_id"])
                update_session_if_new_book(book_match)
                session_data["last_suggested"] = [make_json_safe(book_match)]
                save_session_data(session_data)

                reply = f"📚 Ý bạn có phải sách '{book_match['title']}' của {book_match.get('author','không rõ tác giả')}? Giá là {book_match.get('price', 'chưa có giá')}."
                return jsonify({
                    "ok": True,
                    "reply": reply,
                    "cover": book_match.get("cover"),
                    "covers": [book_match.get("cover")] if book_match.get("cover") else [],
                    "book": make_json_safe(book_match),
                    "suggested": [book_match]
                })
            elif last_book:
                # Không nhắc tên mới nhưng đã có sách trước đó => báo giá từ session
                reply = f"📚 Sách '{last_book['title']}' của {last_book.get('author','không rõ tác giả')} có giá {last_book.get('price','chưa có giá')}."
                return jsonify({
                    "ok": True,
                    "reply": reply,
                    "cover": last_book.get("cover"),
                    "covers": [last_book.get("cover")] if last_book.get("cover") else [],
                    "book": make_json_safe(last_book),
                    "suggested": [last_book]
                })
            else:
                return jsonify({
                    "ok": False,
                    "reply": "Xin lỗi, mình chưa tìm thấy sách nào gần giống để báo giá."
                })

        if "có bán" in query_lower or "có sách" in query_lower or "trong tiệm có" in query_lower:
            book_match = find_book_by_title(text_query, books)
            if book_match:
                book_match["_id"] = str(book_match["_id"])
                update_session_if_new_book(book_match)
                reply = f"✅ Trong tiệm có bán '{book_match['title']}' của {book_match.get('author','không rõ tác giả')} với giá {book_match.get('price','chưa có giá')}."
                return jsonify({
                    "ok": True,
                    "reply": reply,
                    "cover": book_match.get("cover"),
                    "covers": [book_match.get("cover")] if book_match.get("cover") else [],
                    "book": make_json_safe(book_match),
                    "suggested": []
                })
            else:
                suggested = [make_json_safe(b) for b in books[:3]]
                reply = "❌ Hiện tại trong tiệm không có cuốn này. Bạn có thể tham khảo các sách sau:"
                return jsonify({
                    "ok": True,
                    "reply": reply,
                    "cover": suggested[0].get("cover") if suggested and suggested[0].get("cover") else None,
                    "covers": [b.get("cover") for b in suggested if b.get("cover")],
                    "book": None,
                    "suggested": suggested
                })


    
        

        # --- 1. Regex match trực tiếp ---
        book_match = find_book_by_title(text_query, books)


        # Nếu không tìm thấy, thử fuzzy match
        if not book_match:
            all_titles = [b["title"] for b in books]
            close = get_close_matches(text_query, all_titles, n=1, cutoff=0.6)  # 0.6 là ngưỡng similarity
            if close:
                book_match = mongo.db.books.find_one({"title": close[0]})

        # --- 3. Nếu tìm được match (regex hoặc fuzzy) ---
        if book_match:
            book_match["_id"] = str(book_match["_id"])
            update_session_if_new_book(book_match)
            session_data["last_suggested"] = [make_json_safe(book_match)]
            save_session_data(session_data)

            return jsonify({
                "ok": True,
                "reply": f"Mình tìm thấy sách gần giống với '{text_query}': '{book_match['title']}' của {book_match.get('author', 'không rõ tác giả')}. Bạn có muốn mua không?",
                "cover": book_match.get("cover"),
                "covers": [book_match.get("cover")] if book_match.get("cover") else [],
                "book": make_json_safe(book_match),
                "suggested": [book_match]
            })
        

        # --- Nếu không match thì gọi CLIP ---
        try:
            resp = requests.post(
                f"{CLIP_API_URL}/clip-match-multimodal-text",
                json={"query": text_query, "books": books},
                timeout=60
            ).json()
            top_matches = resp.get("matches", [])
        except Exception as e:
            print(f"⚠️ Lỗi khi gọi CLIP API: {e}")
            top_matches = []
        
        if top_matches:
            best_match = top_matches[0]
            update_session_if_new_book(best_match)
            session_data["last_suggested"] = [make_json_safe(best_match)]
            save_session_data(session_data)

            return jsonify({
                "ok": True,
                "reply": f"BooksLand có cuốn '{best_match['title']}' của {best_match['author']}. Bạn có muốn mua không?",
                "cover": best_match.get("cover"),
                "covers": [best_match.get("cover")] if best_match.get("cover") else [],
                "book": make_json_safe(best_match),
                "suggested": [best_match]
            })

        return jsonify({"ok": False, "reply": "Hiện chưa có sách phù hợp."})

    # ================== 7. Xử lý Image / Both ==================
    if pil_img:
        try:
            payload = {"books": books}
            buffered = io.BytesIO()
            pil_img.save(buffered, format="JPEG")
            img_b64 = "data:image/jpeg;base64," + base64.b64encode(buffered.getvalue()).decode()
            payload["image"] = img_b64
            if text_query:
                payload["query"] = text_query

            resp = requests.post(f"{CLIP_API_URL}/clip-match", json=payload, timeout=60).json()
            top_matches = resp.get("matches", [])
        except Exception as e:
            print(f"⚠️ Lỗi khi gọi CLIP API: {e}")
            top_matches = []

        if top_matches:
            best_match = top_matches[0]
            update_session_if_new_book(best_match)
            return jsonify({
                "ok": True,
                "reply": f"Đây có phải là sách '{best_match['title']}' của {best_match['author']} không?",
                "cover": best_match.get("cover"),
                "covers": [best_match.get("cover")] if best_match.get("cover") else [],
                "book": make_json_safe(best_match),
                "suggested": [best_match]
            })
        else:
            return jsonify({"ok": False, "reply": "Không nhận diện được sách từ ảnh."})

    # ================== 8. Fallback ==================
    return jsonify({
        "ok": False,
        "reply": "Không hiểu yêu cầu, vui lòng thử lại với text hoặc ảnh.",
        "cover": None,
        "covers": [],
        "book": None,
        "suggested": []
    })

# ================== API CLEAR SESSION ==================

@app.route("/debug-session")
def debug_session():
    return jsonify({"session": dict(session)})
@app.route("/api/session/clear", methods=["POST"])
def clear_session():
    clear_session_data()
    resp = jsonify({"ok": True, "msg": "Session đã được reset."})
    resp.set_cookie("session", "", expires=0)  # xoá cookie session
    return resp

@app.route("/")
def index():
    session.clear()
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

