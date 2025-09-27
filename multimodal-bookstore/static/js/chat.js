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
const coverPreview = document.getElementById("cover-preview");

// preview ảnh dưới input
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

// ------------------------- Chat -------------------------
async function sendMessage() {
  const msg = input.value.trim();
  if (!msg && !attachedFile) return;

  // hiển thị bubble user
  let previewHTML = "";
  if (attachedPreview) {
    previewHTML = `<img src="${attachedPreview}" style="max-width:150px; border-radius:6px;">`;
  }
  appendMsg("user", (previewHTML ? previewHTML + "<br>" : "") + msg, true);
  input.value = "";
  attachments.innerHTML = "";
  attachedFile = null;
  attachedPreview = null;

  // gửi API
  if (attachedFile) {
    const formData = new FormData();
    formData.append("file", attachedFile, attachedFile.name || "upload.png");
    if (msg) formData.append("query", msg);
    const res = await fetch("/api/query", { method: "POST", body: formData });
    const data = await res.json();
    appendMsg("bot", data.reply || "Uploaded");
  } else {
    const res = await fetch("/api/text-query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: msg }),
    });
    const data = await res.json();
    appendMsg("bot", data.reply);
  }

  // reset input + attach
  input.value = "";
  attachments.innerHTML = "";
  attachedFile = null;
  attachedPreview = null;
}
sendBtn.onclick = sendMessage;
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendMessage();
});

// ------------------------- Upload file preview -------------------------
fileInput.onchange = (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const url = URL.createObjectURL(file);
  addAttachment(file, url);
};

// ------------------------- Screen Crop -------------------------
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

  // Ẩn overlay để không bị chụp vào ảnh
  overlay.style.display = "none";
  overlay.innerHTML = "";

  // Chờ font + ảnh load xong
  await document.fonts.ready;
  const imgs = Array.from(document.images);
  await Promise.all(imgs.map(img => img.complete ? Promise.resolve() : new Promise(res => img.onload = res)));

  // Chuyển ảnh internet sang base64 để html2canvas render đúng
  for (let img of imgs) {
    if (!img.src.startsWith("data:")) {
      try {
        const resp = await fetch(img.src, { mode: "cors" });
        const blob = await resp.blob();
        const reader = new FileReader();
        reader.onloadend = () => { img.src = reader.result; };
        reader.readAsDataURL(blob);
      } catch (err) { console.warn("Cannot fetch image", img.src); }
    }
  }

  // Chụp canvas đúng vùng crop
  const canvas = await html2canvas(document.body, {
    x: rect.left, y: rect.top, width: rect.width, height: rect.height,
    scrollX: 0, scrollY: 0, useCORS: true, allowTaint: true, backgroundColor: null
  });

  canvas.toBlob(blob => {
    const url = URL.createObjectURL(blob);
    // Thêm vào attachment preview
    addAttachment(blob, url);
  });

  // Gỡ sự kiện
  overlay.removeEventListener("mousedown", startCrop);
  overlay.removeEventListener("mousemove", doCrop);
  overlay.removeEventListener("mouseup", finishCrop);
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
function openBookModal(title, author, price, cover) {
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-author").textContent = author;
  document.getElementById("modal-price").textContent = price + " VND";
  document.getElementById("modal-cover").src = cover;
  document.getElementById("book-modal").style.display = "flex";
}
function closeBookModal() {
  document.getElementById("book-modal").style.display = "none";
}
function openAddBookModal() {
  document.getElementById("add-book-modal").style.display = "flex";
}
function closeAddBookModal() {
  document.getElementById("add-book-modal").style.display = "none";
}

document.getElementById("new-cover").addEventListener("change", function (e) {
  const file = e.target.files[0];
  if (file) {
    const url = URL.createObjectURL(file);
    const img = document.getElementById("cover-preview-img");
    img.src = url;
    img.style.display = "block";
  }
});

document.getElementById("add-book-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const formData = new FormData(e.target);
  const res = await fetch("/api/add-book", { method: "POST", body: formData });
  const data = await res.json();
  alert(data.message);
  if (data.ok) {
    closeAddBookModal();
    location.reload();
  }
});
addBookBtn.addEventListener("click", openAddBookModal);

