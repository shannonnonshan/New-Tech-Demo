# app.py
import os
import io
import json
import base64
import time
from PIL import Image
from flask import Flask, render_template, request, jsonify, send_from_directory
import pytesseract
import requests

# ========== Config ==========
# If tesseract is not in PATH on Windows, uncomment and set path:
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

HUGGINGFACE_TOKEN = os.environ.get("HUGGINGFACE_TOKEN")  # optional
HF_HEADERS = {"Authorization": f"Bearer {HUGGINGFACE_TOKEN}"} if HUGGINGFACE_TOKEN else None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "static", "data", "books.json")


# ========== Load DB ==========
with open(DATA_PATH, "r", encoding="utf-8") as f:
    BOOKS = json.load(f)

# ========== Helpers ==========
def find_books_by_text(text):
    text = (text or "").lower()
    found = []
    for b in BOOKS:
        for k in b.get("keywords", []):
            if k in text:
                found.append(b)
                break
    return found

def query_price_by_fragment(fragment):
    found = find_books_by_text(fragment)
    if not found:
        return None
    return found

def ocr_image(pil_img: Image.Image):
    try:
        txt = pytesseract.image_to_string(pil_img)
        return txt.strip()
    except Exception as e:
        return ""

def image_from_base64(data_url):
    header, b64 = data_url.split(",", 1)
    img_bytes = base64.b64decode(b64)
    return Image.open(io.BytesIO(img_bytes))

# Optional: call HF inference (caption or vqa) if you want (token required)
def hf_image_caption(pil_img, model="Salesforce/blip-image-captioning-base"):
    if not HUGGINGFACE_TOKEN:
        return {"error": "No HUGGINGFACE_TOKEN"}
    url = f"https://api-inference.huggingface.co/models/{model}"
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    payload = {"inputs": {"image": {"data": f"data:image/png;base64,{b64}"}}}
    resp = requests.post(url, headers=HF_HEADERS, json=payload, timeout=60)
    if resp.status_code != 200:
        return {"error": resp.text}
    return resp.json()

# ========== Flask app ==========
app = Flask(__name__, static_folder="static", template_folder="templates")


@app.route("/")
def index():
    return render_template("index.html", books=BOOKS)


@app.route("/api/text-query", methods=["POST"])
def api_text_query():
    body = request.json or {}
    q = body.get("query", "")
    if not q:
        return jsonify({"ok": False, "reply": "Bạn chưa nhập câu hỏi."})
    found = query_price_by_fragment(q)
    if found:
        lines = [f"{b['title']} — {b['price']} VND" for b in found]
        reply = "\n".join(lines)
    else:
        reply = "Xin lỗi, không tìm thấy sách phù hợp trong database."
    return jsonify({"ok": True, "reply": reply})


@app.route("/api/upload-image", methods=["POST"])
def api_upload_image():
    # Accept multipart form with file or JSON with base64 'image'
    if "file" in request.files:
        file = request.files["file"]
        pil = Image.open(file.stream).convert("RGB")
    else:
        data = request.json or {}
        b64 = data.get("image")
        if not b64:
            return jsonify({"ok": False, "error": "No image provided"}), 400
        pil = image_from_base64(b64)
        pil = pil.convert("RGB")

    # OCR
    ocr_text = ocr_image(pil)
    found = find_books_by_text(ocr_text)
    if found:
        reply = {"source":"ocr", "ocr_text": ocr_text, "found": [{"title":b["title"], "price": b["price"]} for b in found]}
        return jsonify({"ok": True, "reply": reply})
    # fallback: if HF token is set, call caption
    if HUGGINGFACE_TOKEN:
        cap = hf_image_caption(pil)
        # if successful, try to extract text or match DB
        if isinstance(cap, list) and len(cap)>0 and isinstance(cap[0], dict):
            caption_text = cap[0].get("generated_text") or cap[0].get("caption") or str(cap[0])
        elif isinstance(cap, dict) and cap.get("error"):
            caption_text = ""
        else:
            caption_text = str(cap)
        found2 = find_books_by_text(caption_text + " " + ocr_text)
        if found2:
            reply = {"source":"hf_caption", "caption": caption_text, "found": [{"title":b["title"], "price":b["price"]} for b in found2]}
            return jsonify({"ok": True, "reply": reply})
        else:
            return jsonify({"ok": True, "reply": {"source":"hf_caption", "caption": caption_text, "found": []}})
    return jsonify({"ok": True, "reply": {"source":"ocr", "ocr_text": ocr_text, "found": []}})


@app.route("/api/multimodal-reason", methods=["POST"])
def api_multimodal_reason():
    body = request.json or {}
    b64 = body.get("image")
    q = body.get("query", "") or ""
    if not b64:
        return jsonify({"ok": False, "error": "No image"}), 400
    pil = image_from_base64(b64).convert("RGB")
    # OCR whole image
    ocr_text = ocr_image(pil)
    found = find_books_by_text(ocr_text)
    if not found and HUGGINGFACE_TOKEN:
        cap = hf_image_caption(pil)
        caption_text = ""
        if isinstance(cap, list) and len(cap)>0 and isinstance(cap[0], dict):
            caption_text = cap[0].get("generated_text","")
        else:
            caption_text = str(cap)
        found = find_books_by_text(caption_text + " " + ocr_text)
    if not found:
        return jsonify({"ok": True, "reply": {"plan":"Read->Match->No match", "found":[], "ocr":ocr_text}})
    # simple CoT: if question asks for total then sum
    qlow = q.lower()
    if any(w in qlow for w in ["tổng", "cộng", "total", "bao nhiêu"]):
        total = sum(b['price'] for b in found)
        plan = ["1) Read image text/caption", "2) Match titles in DB", "3) Sum prices"]
        return jsonify({"ok": True, "reply": {"plan": plan, "found":[{"title":b["title"],"price":b["price"]} for b in found], "total": total}})
    else:
        plan = ["1) Read image text/caption", "2) Match titles in DB", "3) Return prices"]
        return jsonify({"ok": True, "reply": {"plan": plan, "found":[{"title":b["title"],"price":b["price"]} for b in found]}})

# Serve static (optional)
@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)

if __name__ == "__main__":
    # run local server
    app.run(host="127.0.0.1", port=5000, debug=True)
