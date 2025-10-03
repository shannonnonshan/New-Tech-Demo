import os
import io
import base64
import random
import re
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
from werkzeug.utils import secure_filename

# ================== CONFIG ==================
load_dotenv()
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
# Đảm bảo bạn đã cấu hình MONGO_URI và CLIP_API_URL trong .env
MONGO_URI = os.environ.get("MONGO_URI")
CLIP_API_URL = os.environ.get("CLIP_API_URL")  # URL CLIP API

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = secrets.token_hex(16)
app.config["MONGO_URI"] = MONGO_URI
app.config["SESSION_PERMANENT"] = False
mongo = PyMongo(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
# ================== UTILS ==================
def image_from_base64(data_url):
    """Giải mã chuỗi base64 thành đối tượng PIL Image."""
    if "," in data_url:
        _, b64 = data_url.split(",", 1)
    else:
        b64 = data_url
    return Image.open(io.BytesIO(base64.b64decode(b64)))
    
def find_book_by_title(text_query, books):
    """
    Tìm sách bằng tiêu đề sử dụng 3 chiến lược:
    1. Regex (không phân biệt hoa thường)
    2. Fuzzy match toàn bộ tiêu đề
    3. Fuzzy match từng từ trong truy vấn
    """
    query_lower = text_query.lower()

    # 1. Regex trực tiếp (không phân biệt hoa thường)
    # Tìm kiếm trong DB, không phải trong list books đã load.
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
    """Chuyển đổi các đối tượng không an toàn cho JSON (như ObjectId) thành chuỗi."""
    if isinstance(obj, dict):
        return {k: make_json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_json_safe(i) for i in obj]
    elif isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    else:
        return str(obj)

def get_session_data():
    """Lấy dữ liệu phiên (session data)."""
    if "data" not in session:
        session["data"] = {}
    return session["data"]

def save_session_data(data):
    """Lưu dữ liệu vào phiên."""
    session["data"] = data

def clear_session_data():
    """Xóa toàn bộ session."""
    session.clear()

def get_session_history():
    """Lấy lịch sử hội thoại, khởi tạo nếu chưa có."""
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
    """Thêm tin nhắn vào lịch sử hội thoại."""
    history = get_session_history()
    history.append({"role": role, "content": content})
    session["history"] = history

def call_openrouter(messages):
    """Gọi API OpenRouter (sử dụng GPT-4o-mini)."""
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

def generate_llm_reply_for_book(intent, book_data, session_history):
    """
    Sử dụng LLM để tạo ra phản hồi thân thiện, tự nhiên dựa trên ngữ cảnh.
    :param intent: Mục đích của người dùng ('price', 'in_stock', 'general_info').
    :param book_data: Dữ liệu cuốn sách được tìm thấy.
    :param session_history: Lịch sử hội thoại (để LLM hiểu ngữ cảnh).
    :return: Câu trả lời do LLM tạo.
    """
    book_title = book_data.get('title', 'một cuốn sách')
    book_author = book_data.get('author', 'không rõ tác giả')
    book_description = book_data.get('description', 'không có mô tả chi tiết')
    book_price = book_data.get('price', 'chưa có giá')

    # Xây dựng prompt cho LLM
    system_prompt = session_history[0]['content'] # Giữ nguyên system role cũ
    
    # Chuẩn bị context về sách
    book_context = f"""
    THÔNG TIN SÁCH ĐƯỢC TÌM THẤY:
    - Tựa đề: {book_title}
    - Tác giả: {book_author}
    - Giá: {book_price}
    - Mô tả tóm tắt: {book_description[:200]}...
    - Trạng thái: Có sẵn trong tiệm.
    """
    
    # Chuẩn bị yêu cầu cụ thể
    user_request = session_history[-1]['content']
    
    if intent == 'price':
        instruction = f"Người dùng hỏi giá của sách '{book_title}'. Dựa trên thông tin SÁCH ĐƯỢC TÌM THẤY, hãy tạo ra một câu trả lời thân thiện, **đưa ra thông tin giá**, và hỏi lại xem họ có muốn mua không."
    elif intent == 'in_stock':
        instruction = f"Người dùng hỏi tiệm có sách '{book_title}' không. Dựa trên thông tin SÁCH ĐƯỢC TÌM THẤY, hãy xác nhận sách có sẵn, **giới thiệu sơ lược về sách (tác giả, nội dung tóm tắt)** và hỏi lại xem họ có muốn mua không."
    elif intent == 'general_info':
        # Yêu cầu rõ ràng bắt đầu bằng câu xác nhận (như yêu cầu của bạn)
        instruction = f"Người dùng tìm kiếm hoặc hỏi thông tin chung về sách '{book_title}'. Dựa trên thông tin SÁCH ĐƯỢC TÌM THẤY, hãy tóm tắt ngắn gọn nội dung và hỏi khách có muốn mua không. **Bắt đầu bằng câu hỏi xác nhận: 'Có phải bạn đang tìm cuốn...'**."
    else:
        instruction = f"Người dùng vừa gửi truy vấn: '{user_request}'. Dựa trên thông tin SÁCH ĐƯỢC TÌM THẤY, hãy tạo ra một phản hồi thân thiện, giàu thông tin và phù hợp với ngữ cảnh."

    
    # Xây dựng messages cho LLM
    llm_messages = [
        {"role": "system", "content": system_prompt + "\n" + book_context},
        {"role": "user", "content": instruction}
    ]
    
    # Thêm lịch sử (tối đa 3 lần tương tác gần nhất) để LLM giữ ngữ cảnh
    for msg in session_history[-4:-1]: 
        llm_messages.append(msg)

    try:
        response = call_openrouter(llm_messages)
        return response["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"⚠️ Lỗi khi gọi OpenRouter/LLM: {e}")
        # Fallback cứng nếu LLM lỗi
        if intent == 'price':
            return f"📚 Sách '{book_title}' của {book_author} có giá **{book_price}**. Bạn có muốn mua không?"
        elif intent == 'in_stock':
            return f"✅ Có ạ. Trong tiệm có bán '{book_title}' của {book_author}, nói về {book_description[:50]}... Bạn có muốn mua không?"
        else:
            # Fallback cho general_info
            return f"Có phải bạn đang tìm cuốn sách tựa đề '{book_title}' của {book_author}? Bạn có muốn mua không?"
def generate_llm_summary_all_books(all_books, session_history):
    """
    Sinh phản hồi thân thiện cho nhiều sách cùng lúc.
    """
    # Chuẩn bị context
    book_infos = []
    total_price = 0
    for match in all_books:
        info = f"- {match.get('title')} của {match.get('author')} (giá: {match.get('price')} VND)"
        book_infos.append(info)
        total_price += match.get("price", 0)

    books_context = "\n".join(book_infos)
    summary_prompt = f"""
    Tôi có danh sách sách sau đây:

    {books_context}

    Tổng giá của tất cả sách là {total_price} VND.

    Hãy viết một đoạn trả lời tự nhiên, thân thiện như con người, 
    giới thiệu các cuốn sách này, nhấn mạnh tổng giá,
    và gợi ý khách xem có muốn chọn mua cuốn nào không.
    """

    # Gọi LLM
    system_prompt = session_history[0]["content"] if session_history else "Bạn là nhân viên tư vấn sách thân thiện."
    llm_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": summary_prompt}
    ]

    try:
        response = call_openrouter(llm_messages)
        return response["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"⚠️ Lỗi khi gọi OpenRouter/LLM: {e}")
        # fallback: tự ghép chuỗi
        return f"Tìm thấy {len(all_books)} cuốn sách:\n{books_context}\n👉 Tổng giá: {total_price} VND."

# ================== PUSH BOOKS TO CLIP ==================
def push_books_to_clip():
    """Gửi toàn bộ dữ liệu sách lên CLIP API để tạo embedding phục vụ tìm kiếm."""
    if not CLIP_API_URL:
        print("⚠️ Chưa cấu hình CLIP_API_URL, bỏ qua push sách")
        return
    try:
        # Lấy sách từ Mongo (chỉ lấy id và title để check)
        books = list(mongo.db.books.find({}, {"_id": 1, "title": 1}))
        if not books:
            print("⚠️ Mongo chưa có sách, bỏ qua push sách lên CLIP")
            return
            
        # Tải lại toàn bộ dữ liệu sách để gửi lên CLIP
        full_books = list(mongo.db.books.find())
        for book in full_books:
            book["_id"] = str(book["_id"])
            
        resp = requests.post(CLIP_API_URL + "/clip-match-text",
                             json={"query": "dummy", "books": full_books}, timeout=120)
        
        if resp.status_code == 200:
            print(f"✅ Đã push {len(full_books)} sách lên CLIP API thành công")
        else:
            print(f"⚠️ Lỗi khi push sách: {resp.status_code}, {resp.text[:200]}")
    except Exception as e:
        print(f"⚠️ Exception khi push sách lên CLIP: {e}")

# Chỉ chạy khi module được load
push_books_to_clip()

# ================== ROUTES ==================
@app.route("/api/query", methods=["POST"])
def api_query():
    pil_img = None
    text_query = ""
    top_matches = []   # ✅ luôn khai báo trước để tránh lỗi

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
        return jsonify({"ok": True, "reply": "Session đã được reset."})

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
            "ok": True, "reply": reply,
            "cover": None, "covers": [], "book": None, "suggested": []
        })

    # ================== 6. Xử lý Text-only ==================
    if text_query and not pil_img:
        query_lower = text_query.lower()

        # --- Xác nhận "có" ---
        confirm_words_yes = ["có", "đúng rồi", "ok", "mua", "chuẩn", "phải"]
        if query_lower in confirm_words_yes:
            last_book = session_data.get("last_best_match")
            if last_book:
                book_in_store = find_book_by_title(text_query, books)
                if book_in_store:
                    book_in_store["_id"] = str(book_in_store["_id"])
                    add_to_history("user", text_query)
                    llm_reply = generate_llm_reply_for_book("in_stock", book_in_store, get_session_history()) 
                    add_to_history("assistant", llm_reply)
                    return jsonify({
                        "ok": True, "reply": llm_reply,
                        "cover": book_in_store.get("cover"),
                        "covers": [book_in_store.get("cover")] if book_in_store.get("cover") else [],
                        "book": make_json_safe(book_in_store), "suggested": []
                    })
            return jsonify({"ok": False, "reply": "Bạn muốn mua sách nào nhỉ? Hãy chọn lại nhé."})

        # --- Xác nhận "không" ---
        confirm_words_no = ["không", "không mua", "không nha", "không nhe", "không phải", "sai", "nhầm"]
        if query_lower in confirm_words_no:
            # Lấy random 3 cuốn trong DB
            random_books = list(mongo.db.books.aggregate([{"$sample": {"size": 3}}]))
            for b in random_books:
                b["_id"] = str(b["_id"])  # convert ObjectId -> str để JSON safe

            reply = "Không sao 😊. Bạn thử xem thêm mấy cuốn này nhé."

            # Update session
            session_data["last_suggested"] = [make_json_safe(b) for b in random_books]
            save_session_data(session_data)

            # Update history
            add_to_history("user", text_query)
            add_to_history("assistant", reply)

            return jsonify({
                "ok": True,
                "reply": reply,
                "cover": random_books[0].get("cover") if random_books else None,
                "covers": [b.get("cover") for b in random_books if b.get("cover")],
                "book": None,
                "suggested": random_books
            })

        # --- Hỏi giá ---
        price_keywords = ["giá", "cost", "bao nhiêu", "mấy tiền", "giá bao nhiêu", "nhiêu"]
        if any(word in query_lower for word in price_keywords):
            add_to_history("user", text_query)
            last_book = session_data.get("last_best_match")
            book_match = find_book_by_title(text_query, books)
            if book_match:
                book_match["_id"] = str(book_match["_id"])
                update_session_if_new_book(book_match)
                session_data["last_suggested"] = [make_json_safe(book_match)]
                save_session_data(session_data)
                reply = generate_llm_reply_for_book("price", book_match, get_session_history())
                add_to_history("assistant", reply)
                return jsonify({
                    "ok": True, "reply": reply,
                    "cover": book_match.get("cover"),
                    "covers": [book_match.get("cover")] if book_match.get("cover") else [],
                    "book": make_json_safe(book_match), "suggested": [book_match]
                })
            elif last_book:
                reply = generate_llm_reply_for_book("price", last_book, get_session_history())
                add_to_history("assistant", reply)
                return jsonify({
                    "ok": True, "reply": reply,
                    "cover": last_book.get("cover"),
                    "covers": [last_book.get("cover")] if last_book.get("cover") else [],
                    "book": make_json_safe(last_book), "suggested": [last_book]
                })
            else:
                reply = "Xin lỗi, mình chưa tìm thấy sách nào gần giống để báo giá."
                add_to_history("assistant", reply)
                return jsonify({"ok": False, "reply": reply})

        # --- Hỏi có bán không ---
        if "có bán" in query_lower or "có sách" in query_lower or "trong tiệm có" in query_lower:
            add_to_history("user", text_query)
            book_match = find_book_by_title(text_query, books)
            if book_match:
                book_match["_id"] = str(book_match["_id"])
                update_session_if_new_book(book_match)
                reply = generate_llm_reply_for_book("in_stock", book_match, get_session_history())
                add_to_history("assistant", reply)
                return jsonify({
                    "ok": True, "reply": reply,
                    "cover": book_match.get("cover"),
                    "covers": [book_match.get("cover")] if book_match.get("cover") else [],
                    "book": make_json_safe(book_match), "suggested": []
                })

        # --- Tìm theo màu bìa ---
        color_map = {
            "màu đỏ": "red", "bìa đỏ": "red",
            "màu xanh": "blue", "bìa xanh": "blue",
            "màu vàng": "yellow", "bìa vàng": "yellow",
            "màu trắng": "white", "bìa trắng": "white",
            "màu đen": "black", "bìa đen": "black",
            "màu cam": "orange", "màu tím": "purple",
            "màu hồng": "pink", "màu nâu": "brown",
            "màu xám": "gray"
        }
        detected_color = None
        for kw, eng in color_map.items():
            if kw in query_lower:
                detected_color = eng
                break

        if detected_color:
            add_to_history("user", text_query)
            try:
                print(f"-> Gọi CLIP API theo màu sắc: {detected_color}")
                resp = requests.post(
                    f"{CLIP_API_URL}/clip-match-color",
                    json={"color": detected_color},
                    timeout=60
                ).json()
                top_matches = resp.get("suggested", [])
            except Exception as e:
                print(f"⚠️ Lỗi khi gọi CLIP API Color Search: {e}")
                top_matches = []

            if top_matches:
                best_match = top_matches[0]
                update_session_if_new_book(best_match)

                # Lưu danh sách gợi ý vào session
                session_data["last_suggested"] = [make_json_safe(b) for b in top_matches]
                save_session_data(session_data)

                # Danh sách sách kèm giá
                book_list_str = "\n".join([
                    f"- **{b['title']}** ({b['author']}) → {b.get('price', 0):,}₫"
                    for b in top_matches
                ])

                # Tính tổng giá
                total_price = sum(b.get("price", 0) for b in top_matches)

                reply = (
                    f"Dựa trên yêu cầu tìm sách bìa **{detected_color}**, BooksLand gợi ý:\n"
                    f"{book_list_str}\n"
                    f"👉 **Tổng cộng: {total_price:,}₫**\n\n"
                    "Bạn muốn mình giới thiệu chi tiết cuốn nào không?"
                )

                add_to_history("assistant", reply)

                return jsonify({
                    "ok": True,
                    "reply": reply,
                    "cover": best_match.get("cover"),
                    "covers": [b.get("cover") for b in top_matches if b.get("cover")],
                    "book": make_json_safe(best_match),
                    "suggested": top_matches,
                    "detected_color": detected_color,
                    "total_price": total_price
                })
            else:
                reply = f"Xin lỗi, chưa tìm thấy sách bìa **{detected_color}**."
                add_to_history("assistant", reply)
                return jsonify({"ok": False, "reply": reply})

        # --- Tìm kiếm chung ---
        book_match = find_book_by_title(text_query, books)
        if book_match:
            add_to_history("user", text_query)
            book_match["_id"] = str(book_match["_id"])
            update_session_if_new_book(book_match)
            session_data["last_suggested"] = [make_json_safe(book_match)]
            save_session_data(session_data)
            reply = generate_llm_reply_for_book("general_info", book_match, get_session_history())
            add_to_history("assistant", reply)
            return jsonify({
                "ok": True, "reply": reply,
                "cover": book_match.get("cover"),
                "covers": [book_match.get("cover")] if book_match.get("cover") else [],
                "book": make_json_safe(book_match), "suggested": [book_match]
            })

        try:
            resp = requests.post(
                f"{CLIP_API_URL}/clip-match-multimodal-text",
                json={"query": text_query, "books": books},
                timeout=60
            ).json()
            top_matches = resp.get("matches", [])
        except Exception as e:
            print(f"⚠️ Lỗi khi gọi CLIP API Multimodal Search: {e}")
            top_matches = []

        if top_matches:
            add_to_history("user", text_query)
            best_match = top_matches[0]
            update_session_if_new_book(best_match)
            session_data["last_suggested"] = [make_json_safe(best_match)]
            save_session_data(session_data)
            reply = generate_llm_reply_for_book("general_info", best_match, get_session_history())
            add_to_history("assistant", reply)
            return jsonify({
                "ok": True, "reply": reply,
                "cover": best_match.get("cover"),
                "covers": [best_match.get("cover")] if best_match.get("cover") else [],
                "book": make_json_safe(best_match), "suggested": [best_match]
            })

        reply = "Hiện chưa có sách phù hợp."
        add_to_history("user", text_query)
        add_to_history("assistant", reply)
        return jsonify({"ok": False, "reply": reply})

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
            resp = requests.post(f"{CLIP_API_URL}/clip-match-book", json=payload, timeout=60).json()
            all_matches = resp.get("matches", [])   # list các crop
            print(all_matches)
        except Exception as e:
            print(f"⚠️ Lỗi khi gọi CLIP API (Image): {e}")
            all_matches = []

        # gom tất cả matches từ mọi crop
        all_filtered = all_matches

        if all_filtered:
            combined_reply = generate_llm_summary_all_books(all_filtered, get_session_history())
            add_to_history("assistant", combined_reply)

            return jsonify({
                "ok": True,
                "reply": combined_reply,
                "covers": [m.get("cover") for m in all_filtered if m.get("cover")],
                "books": [make_json_safe(m) for m in all_filtered],
                "suggested": all_filtered,
                "total_price": sum(m.get("price", 0) for m in all_filtered)
            })
        else:
            reply = "Không nhận diện được sách từ ảnh."
            add_to_history("user", f"[IMAGE] {text_query if text_query else '(Tìm sách qua ảnh)'}")
            add_to_history("assistant", reply)
            return jsonify({"ok": False, "reply": reply})


    # ================== 8. Fallback ==================
    reply = "Không hiểu yêu cầu, vui lòng thử lại với text hoặc ảnh."
    add_to_history("user", text_query)
    add_to_history("assistant", reply)
    return jsonify({
        "ok": False, "reply": reply,
        "cover": None, "covers": [], "book": None, "suggested": []
    })

# ================== API CLEAR SESSION ==================

@app.route("/debug-session")
def debug_session():
    """Endpoint để kiểm tra session (chỉ dùng khi debug)."""
    return jsonify({"session": dict(session)})
    
@app.route("/api/session/clear", methods=["POST"])
def clear_session_api():
    """API để xóa session."""
    clear_session_data()
    resp = jsonify({"ok": True, "msg": "Session đã được reset."})
    resp.set_cookie("session", "", expires=0)  # xoá cookie session
    return resp

@app.route("/")
def index():
    """Trang chủ, xóa session khi load."""
    session.clear()
    books = list(mongo.db.books.find())
    return render_template("index.html", books=books)

@app.route("/api/books", methods=["GET"])
def get_books():
    """Lấy danh sách toàn bộ sách."""
    books = list(mongo.db.books.find())
    for book in books:
        book["_id"] = str(book["_id"])
    return jsonify({"ok": True, "books": [make_json_safe(book) for book in books]})
# -------- Add book --------
@app.route("/api/add-book", methods=["POST"])
def api_add_book():
    title = request.form.get("new-title")
    author = request.form.get("new-author")
    price = request.form.get("new-price")
    cover = request.files.get("new-cover-file")
    cover_url = request.form.get("new-cover-url")
    print(request.form)
    if not title or not author:
        return jsonify({"ok": False, "message": "❌ Thiếu tiêu đề hoặc tác giả."}), 400

    if cover:
        filename = secure_filename(cover.filename)
        save_path = os.path.join(UPLOAD_FOLDER, filename)
        cover.save(save_path)
        cover_url = "/static/uploads/" + filename

    mongo.db.books.insert_one({
        "title": title,
        "author": author,
        "price": int(price) if price and price.isdigit() else 0,
        "cover": cover_url
    })

    return jsonify({"ok": True, "message": "✅ Book added to MongoDB"})

@app.route("/api/recommended", methods=["GET"])
def get_recommended():
    """Lấy danh sách sách gợi ý ngẫu nhiên."""
    books = list(mongo.db.books.find())
    recommended = random.sample(books, min(6, len(books)))
    for book in recommended:
        book["_id"] = str(book["_id"])
    return jsonify({"ok": True, "books": [make_json_safe(book) for book in recommended]})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)