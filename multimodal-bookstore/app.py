# app.py
import os
import io
import time
import base64
import requests
from PIL import Image
import pytesseract
import gradio as gr

# ---------------------------
# Config / DB giả lập
# ---------------------------
HUGGINGFACE_TOKEN = os.environ.get("HUGGINGFACE_TOKEN")  # set env var if you want HF inference
HF_HEADERS = {"Authorization": f"Bearer {HUGGINGFACE_TOKEN}"} if HUGGINGFACE_TOKEN else None

# Minimal bookstore DB (sample). You can extend or connect DB real.
BOOKS_DB = [
    {"id": 1, "title": "1984", "author": "George Orwell", "price": 120000, "cover": None, "keywords": ["1984"]},
    {"id": 2, "title": "Animal Farm", "author": "George Orwell", "price": 90000, "cover": None, "keywords": ["animal farm", "animal", "farm"]},
    {"id": 3, "title": "Harry Potter and the Prisoner of Azkaban", "author": "J.K. Rowling", "price": 150000, "cover": None, "keywords": ["harry potter", "prisoner of azkaban", "azkaban"]},
    {"id": 4, "title": "The Great Gatsby", "author": "F. Scott Fitzgerald", "price": 100000, "cover": None, "keywords": ["great gatsby", "gatsby"]},
]

# ---------------------------
# Helpers: DB lookup + OCR
# ---------------------------
def find_books_by_text(text):
    text = (text or "").lower()
    found = []
    for b in BOOKS_DB:
        for k in b["keywords"]:
            if k in text:
                found.append(b)
                break
    return found

def query_book_price_by_title_fragment(fragment):
    found = find_books_by_text(fragment)
    if not found:
        return "Không tìm thấy sách trong DB."
    lines = [f"{b['title']} — {b['price']} VND" for b in found]
    return "\n".join(lines)

def ocr_from_image(pil_img):
    try:
        txt = pytesseract.image_to_string(pil_img)
        return txt.strip()
    except Exception as e:
        return ""

# ---------------------------
# Hugging Face inference helpers (image captioning / vqa)
# ---------------------------
def hf_call_model(model_id, payload_json):
    """Generic call to Hugging Face Inference API. Returns dict or text."""
    if not HUGGINGFACE_TOKEN:
        return {"error": "No HUGGINGFACE_TOKEN set. Set env var HUGGINGFACE_TOKEN for HF inference."}
    url = f"https://api-inference.huggingface.co/models/{model_id}"
    try:
        resp = requests.post(url, headers=HF_HEADERS, json=payload_json, timeout=60)
    except Exception as e:
        return {"error": str(e)}
    if resp.status_code != 200:
        return {"error": f"Status {resp.status_code}: {resp.text}"}
    try:
        return resp.json()
    except:
        return {"result": resp.text}

def image_to_base64(pil_img):
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    b = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b}"

def hf_caption(image: Image.Image, model_id="Salesforce/blip-image-captioning-base"):
    """Use HF inference API to get caption. Model must support image-to-text."""
    img_b64 = image_to_base64(image)
    payload = {"inputs": {"image": {"data": img_b64, "type": "data:image/png;base64"}}}
    resp = hf_call_model(model_id, payload)
    # Different models return different json. Try to extract likely text:
    # Many blip models return [{"generated_text": "..."}]
    if isinstance(resp, dict) and resp.get("error"):
        return f"[HF error] {resp['error']}"
    if isinstance(resp, list) and len(resp)>0 and isinstance(resp[0], dict) and "generated_text" in resp[0]:
        return resp[0]["generated_text"]
    # fallback: if a single string or dict with 'text'
    if isinstance(resp, dict) and "generated_text" in resp:
        return resp["generated_text"]
    if isinstance(resp, str):
        return resp
    # else stringify
    return str(resp)

def hf_vqa(image: Image.Image, question: str, model_id="dandelin/vilt-b32-finetuned-vqa"):
    """Call HF VQA model (if available). Some VQA models expect 'inputs': {'image':..., 'question': ...}"""
    img_b64 = image_to_base64(image)
    payload = {"inputs": {"image": {"data": img_b64, "type": "data:image/png;base64"}, "question": question}}
    resp = hf_call_model(model_id, payload)
    if isinstance(resp, dict) and resp.get("error"):
        return f"[HF error] {resp['error']}"
    # Many VQA models return [{'answer': '...'}] or [{'generated_text':'...'}]
    if isinstance(resp, list) and len(resp)>0:
        first = resp[0]
        if isinstance(first, dict):
            for k in ("answer","generated_text","caption","text"):
                if k in first:
                    return first[k]
            # fallback: join values
            return " | ".join(f"{k}:{v}" for k,v in first.items())
    if isinstance(resp, dict) and "answer" in resp:
        return resp["answer"]
    return str(resp)

# ---------------------------
# Modal handlers
# ---------------------------
def handle_local_ocr(image, text_query):
    # If image present: OCR -> find books
    if image is None:
        # only text query
        return query_book_price_by_title_fragment(text_query)
    # crop passed image is already region selected by gradio
    txt = ocr_from_image(image)
    found = find_books_by_text(txt)
    if not found:
        return f"OCR đọc được: '{txt}'. Không thấy sách khớp trong DB."
    return simple_cot_answer(found, text_query, ocr_text=txt)

def handle_hf_caption(image, text_query):
    if HUGGINGFACE_TOKEN is None:
        return "[No HF token] Set HUGGINGFACE_TOKEN env var to use HF caption model."
    cap = hf_caption(image)
    found = find_books_by_text(cap + " " + (ocr_from_image(image) or ""))
    if not found:
        return f"Caption: '{cap}'. Không thấy sách khớp."
    return simple_cot_answer(found, text_query, caption=cap)

def handle_hf_vqa(image, text_query):
    if HUGGINGFACE_TOKEN is None:
        return "[No HF token] Set HUGGINGFACE_TOKEN env var to use HF VQA model."
    # If user provided text_query, pass it as question; else use generic "What's in image?"
    question = text_query or "What is this book?"
    ans = hf_vqa(image, question)
    # Postprocess: try to detect book titles in answer
    found = find_books_by_text(ans)
    if found:
        return simple_cot_answer(found, text_query, vqa_answer=ans)
    return f"VQA answer: '{ans}'."

def simple_cot_answer(found_books, question, ocr_text=None, caption=None, vqa_answer=None):
    """Return short-plan + final answer (CoT-style short)."""
    titles = [b["title"] for b in found_books]
    # If question asks about total
    q = (question or "").lower()
    if any(w in q for w in ["tổng", "cộng", "total", "price sum", "bao nhiêu"]):
        total = sum(b["price"] for b in found_books)
        plan = "Plan (short):\n- Read image text/caption\n- Match titles in DB\n- Sum prices\n"
        return f"{plan}\nFound: {', '.join(titles)}.\nTotal price = {total} VND."
    # default: list prices
    lines = [f"{b['title']}: {b['price']} VND" for b in found_books]
    plan = "Plan (short):\n- Identify book from image/text\n- Return price\n"
    return f"{plan}\n" + "\n".join(lines)

# ---------------------------
# Compare runner: run selected set of modals and show side-by-side
# ---------------------------
def compare_modal_responses(image, text_query, selected_modals):
    """
    selected_modals: list of modal keys like ["ocr","caption","vqa","combined"]
    """
    results = []
    for m in selected_modals:
        t0 = time.time()
        if m == "ocr":
            out = handle_local_ocr(image, text_query)
        elif m == "caption":
            out = handle_hf_caption(image, text_query)
        elif m == "vqa":
            out = handle_hf_vqa(image, text_query)
        elif m == "combined":
            # combined: caption + OCR + simple ensemble
            cap = hf_caption(image) if HUGGINGFACE_TOKEN else ""
            ocr = ocr_from_image(image)
            merged = " ".join([cap, ocr])
            found = find_books_by_text(merged)
            if found:
                out = simple_cot_answer(found, text_query, ocr_text=ocr, caption=cap)
            else:
                out = f"Caption:'{cap}' | OCR:'{ocr}' -> no match"
        else:
            out = "Unknown modal"
        dt = time.time() - t0
        results.append({"modal": m, "response": out, "time_s": round(dt, 2)})
    return results

# ---------------------------
# UI: Bookstore Home + Chatbox multimodal
# ---------------------------
def serve_ui():
    with gr.Blocks(css="""
        .book-card {border:1px solid #eee;padding:10px;border-radius:8px;margin-bottom:8px}
        .sidebar {background:#fafafa;padding:12px;border-radius:8px}
    """) as demo:
        # Header
        with gr.Row():
            gr.Markdown("## 📚 BookStore AI — Home")
            gr.HTML("<p style='color:gray'>Demo multimodal: text, image, crop, compare modal outputs</p>")

        with gr.Row():
            # Left: Shop / catalog
            with gr.Column(scale=1):
                gr.Markdown("### 🛍️ Shop")
                for b in BOOKS_DB:
                    # For simplicity we just show title & price; you can add images
                    gr.Markdown(f"<div class='book-card'><b>{b['title']}</b><br><i>{b['author']}</i><br>💰 {b['price']} VND</div>", elem_classes="book-card")

            # Right: Chatbox panel
            with gr.Column(scale=1):
                gr.Markdown("### 🤖 Chatbox (Multimodal)")

                text_in = gr.Textbox(label="Nhập câu hỏi (hoặc để trống để dùng image)", placeholder="VD: Tổng giá bao nhiêu?")
                image_in = gr.Image(
                    label="Upload hoặc chụp ảnh",
                    type="pil",
                    sources=["upload", "webcam"]
                )
                modal_choices = gr.CheckboxGroup(label="Chọn modal để chạy (chọn nhiều để so sánh)", value=["ocr","caption","vqa"], choices=[
                    ("ocr","Local OCR (pytesseract)"),
                    ("caption","HF Caption (BLIP)"),
                    ("vqa","HF VQA (VILT)"),
                    ("combined","Combined (caption+ocr)")
                ])
                btn_compare = gr.Button("So sánh responses")
                compare_out = gr.Dataframe(headers=["Modal","Response","Time (s)"], interactive=False)

                # Single-run quick ask (runs the first selected modal)
                btn_ask = gr.Button("Hỏi nhanh (first selected)")
                quick_out = gr.Textbox(label="Kết quả nhanh")

                def on_compare(img, q, selected):
                    if not selected:
                        return gr.update(value=[]), gr.update(value="Vui lòng chọn ít nhất 1 modal")
                    res = compare_modal_responses(img, q, selected)
                    # turn into table rows
                    rows = [[r["modal"], r["response"], r["time_s"]] for r in res]
                    return rows

                def on_quick(img, q, selected):
                    if not selected:
                        return "Vui lòng chọn ít nhất 1 modal"
                    first = selected[0]
                    out = compare_modal_responses(img, q, [first])[0]["response"]
                    return out

                btn_compare.click(on_compare, inputs=[image_in, text_in, modal_choices], outputs=[compare_out])
                btn_ask.click(on_quick, inputs=[image_in, text_in, modal_choices], outputs=[quick_out])

                gr.Markdown("**Ghi chú:** Nếu bạn không có HUGGINGFACE_TOKEN thì HF modal sẽ báo lỗi và app sẽ dùng OCR thay thế.")

        # Footer
        gr.Markdown("---")
        gr.Markdown("© Demo — Multimodal BookStore")

    demo.launch(server_name="0.0.0.0", server_port=7860)

if __name__ == "__main__":
    serve_ui()
