// ------------------------- Element refs -------------------------
const sendBtn = document.getElementById("send-btn");
const input = document.getElementById("user-input");
const chat = document.getElementById("chat-messages");
const fileInput = document.getElementById("file-upload");
const cropBtn = document.getElementById("crop-btn");
const overlay = document.getElementById("screen-crop-overlay");

const addBookBtn = document.getElementById("add-book-btn");
const addBookModal = document.getElementById("add-book-modal");
const addBookForm = document.getElementById("add-book-form");
const coverInput = document.getElementById("new-cover");
const coverPreviewImg = document.getElementById("cover-preview-img");
const attachments = document.getElementById("input-attachments");

let attachedFile = null;
let attachedPreview = null;

// ------------------------- Utils -------------------------
function getTime() {
  const d = new Date();
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function appendMsg(who, content, isHTML = false) {
  const div = document.createElement("div");
  div.className = "msg " + who;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (isHTML) bubble.innerHTML = content;
  else bubble.textContent = content;

  const time = document.createElement("span");
  time.className = "time";
  time.textContent = getTime();

  bubble.appendChild(time);
  div.appendChild(bubble);
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

// ------------------------- Attachment -------------------------
function addAttachment(file, previewURL) {
  attachments.innerHTML = `
    <div class="attachment-item" style="position:relative; display:inline-block;">
      <img src="${previewURL}" style="max-width:80px; border-radius:6px; border:1px solid #ccc;">
      <button id="cancel-attach" style="
        position:absolute; top:-6px; right:-6px;
        background:orange; border:none; color:white;
        border-radius:50%; width:20px; height:20px;
        cursor:pointer; font-size:12px; line-height:18px;
      ">✕</button>
    </div>
  `;
  attachedFile = file;
  attachedPreview = previewURL;

  document.getElementById("cancel-attach").onclick = () => {
    attachments.innerHTML = "";
    attachedFile = null;
    attachedPreview = null;
  };
}

// ------------------------- Send message -------------------------
async function sendMessage(fileToSend = null, b64ToSend = null) {
  const msg = input.value.trim();
  if (!msg && !fileToSend && !b64ToSend) return;

  // --- Append message user trước khi gửi ---
  let userContent = "";
  if (b64ToSend) userContent = `<img src="${b64ToSend}" style="max-width:150px; border-radius:6px;"><br>${msg}`;
  else if (fileToSend) userContent = `<img src="${URL.createObjectURL(fileToSend)}" style="max-width:150px; border-radius:6px;"><br>${msg}`;
  else userContent = msg;
  appendMsg("user", userContent, true);

  // --- Xóa input box, giữ attachment preview nếu muốn ---
  input.value = "";

  // --- Hiển thị AI is thinking ---
  const thinkingDiv = document.createElement("div");
  thinkingDiv.className = "msg bot";
  thinkingDiv.id = "ai-thinking";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = "🤖 AI is thinking...";
  bubble.style.fontStyle = "italic";
  thinkingDiv.appendChild(bubble);
  chat.appendChild(thinkingDiv);
  chat.scrollTop = chat.scrollHeight;

  try {
    let res, data;
    if (fileToSend) {
      const formData = new FormData();
      formData.append("file", fileToSend, fileToSend.name || "upload.png");
      if (msg) formData.append("query", msg);
      res = await fetch("/api/query", { method: "POST", body: formData });
      data = await res.json();
    } else if (b64ToSend) {
      res = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image: b64ToSend, query: msg })
      });
      data = await res.json();
    } else {
      res = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: msg })
      });
      data = await res.json();
    }

    // --- Xóa "AI is thinking..." ---
    const thinkingNode = document.getElementById("ai-thinking");
    if (thinkingNode) thinkingNode.remove();

    // --- Hiển thị phản hồi AI, chỉ show cover nếu backend trả về ---
    let content = data.reply || "";
    if (data.cover) {
      content = `<img src="${data.cover}" style="max-width:150px; border-radius:6px;"><br>` + content;
    }
    appendMsg("bot", content, true);

  } catch (err) {
    const thinkingNode = document.getElementById("ai-thinking");
    if (thinkingNode) thinkingNode.remove();
    appendMsg("bot", `⚠️ Lỗi gửi dữ liệu: ${err}`);
  }

  // --- Reset attachment preview ---
  attachments.innerHTML = "";
  attachedFile = null;
  attachedPreview = null;
}

// ------------------------- Send cropped image (preview first) -------------------------
function previewCrop(b64) {
  addAttachment(null, b64); // chỉ preview
}

// ------------------------- File preview -------------------------
fileInput.onchange = (e) => {
  const file = e.target.files[0];
  if (!file) return;
  
  const reader = new FileReader();
  reader.onload = (ev) => {
    const b64 = ev.target.result;
    addAttachment(file, b64); // vừa set attachedFile, vừa preview
  };
  reader.readAsDataURL(file);
};

// ------------------------- Crop screen -------------------------
let cropDiv = null, startX = 0, startY = 0, isDragging = false;

function startCrop(e) {
  startX = e.clientX; startY = e.clientY;
  cropDiv = document.createElement("div");
  Object.assign(cropDiv.style, {
    position: "absolute",
    border: "2px dashed red",
    background: "rgba(255,255,255,0.1)",
    left: startX + "px",
    top: startY + "px",
    zIndex: 10000
  });
  overlay.appendChild(cropDiv);
  isDragging = true;
}

function doCrop(e) {
  if (!isDragging) return;
  const w = e.clientX - startX, h = e.clientY - startY;
  cropDiv.style.width = Math.abs(w) + "px";
  cropDiv.style.height = Math.abs(h) + "px";
  cropDiv.style.left = (w < 0 ? e.clientX : startX) + "px";
  cropDiv.style.top = (h < 0 ? e.clientY : startY) + "px";
}

async function finishCrop(e) {
  if (!isDragging) return;
  isDragging = false;

  const rect = cropDiv.getBoundingClientRect();
  overlay.style.display = "none";
  overlay.innerHTML = "";

  const canvas = await html2canvas(document.body, {
    x: rect.left, y: rect.top, width: rect.width, height: rect.height,
    scrollX: 0, scrollY: 0, useCORS: true, allowTaint: true, backgroundColor: null
  });

  const b64 = canvas.toDataURL("image/png");
  previewCrop(b64); // chỉ preview, không gửi
}

cropBtn.onclick = () => {
  overlay.style.display = "block";
  overlay.addEventListener("mousedown", startCrop);
  overlay.addEventListener("mousemove", doCrop);
  overlay.addEventListener("mouseup", finishCrop);

  const escHandler = e => {
    if (e.key === "Escape") {
      overlay.style.display = "none";
      overlay.innerHTML = "";
      overlay.removeEventListener("mousedown", startCrop);
      overlay.removeEventListener("mousemove", doCrop);
      overlay.removeEventListener("mouseup", finishCrop);
      document.removeEventListener("keydown", escHandler);
    }
  };
  document.addEventListener("keydown", escHandler);
};

// ------------------------- Book modal -------------------------
function openAddBookModal() { addBookModal.style.display = "flex"; }
function closeAddBookModal() { addBookModal.style.display = "none"; }

coverInput.addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (file) coverPreviewImg.src = URL.createObjectURL(file);
});

addBookForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const formData = new FormData(e.target);
  const res = await fetch("/api/add-book", { method: "POST", body: formData });
  const data = await res.json();
  alert(data.message);
  if (data.ok) { closeAddBookModal(); location.reload(); }
});

addBookBtn.addEventListener("click", openAddBookModal);

// ------------------------- Send button & Enter key -------------------------
sendBtn.onclick = () => sendMessage(attachedFile, attachedPreview);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendMessage(attachedFile, attachedPreview);
});
