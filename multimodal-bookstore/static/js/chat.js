// static/js/chat.js
const chatWindow = document.getElementById("chat-window");
const fileInput = document.getElementById("file-input");
const previewCanvas = document.getElementById("preview-canvas");
const previewCtx = previewCanvas.getContext("2d");
let currentImage = null;
let isDragging = false;
let startX=0, startY=0, curX=0, curY=0;

function addMessage(who, text){
  const div = document.createElement("div");
  div.style.margin = "6px 0";
  if(who==="user") div.innerHTML = `<div style="font-weight:600">You</div><div>${text}</div>`;
  else div.innerHTML = `<div style="font-weight:600;color:#0b74de">Assistant</div><div>${text}</div>`;
  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

fileInput.addEventListener("change", (e)=>{
  const f = e.target.files[0];
  if(!f) return;
  const reader = new FileReader();
  reader.onload = function(ev){
    loadPreviewImage(ev.target.result);
  }
  reader.readAsDataURL(f);
});

document.getElementById("preview-btn").addEventListener("click", ()=>{
  if(!currentImage) alert("Chọn ảnh trước");
});

function loadPreviewImage(dataUrl){
  const img = new Image();
  img.onload = function(){
    currentImage = img;
    // fit canvas
    const maxW = 320;
    const ratio = Math.min(maxW / img.width, 1);
    previewCanvas.width = Math.round(img.width * ratio);
    previewCanvas.height = Math.round(img.height * ratio);
    previewCtx.clearRect(0,0,previewCanvas.width, previewCanvas.height);
    previewCtx.drawImage(img, 0, 0, previewCanvas.width, previewCanvas.height);
  }
  img.src = dataUrl;
}

// Mouse events for drawing crop rectangle
previewCanvas.addEventListener("mousedown", (e)=>{
  if(!currentImage) return;
  isDragging = true;
  const r = previewCanvas.getBoundingClientRect();
  startX = e.clientX - r.left;
  startY = e.clientY - r.top;
});
previewCanvas.addEventListener("mousemove", (e)=>{
  if(!isDragging || !currentImage) return;
  const r = previewCanvas.getBoundingClientRect();
  curX = e.clientX - r.left;
  curY = e.clientY - r.top;
  // redraw
  previewCtx.clearRect(0,0,previewCanvas.width, previewCanvas.height);
  previewCtx.drawImage(currentImage, 0, 0, previewCanvas.width, previewCanvas.height);
  previewCtx.strokeStyle = "#00aaff";
  previewCtx.lineWidth = 2;
  previewCtx.strokeRect(startX, startY, curX - startX, curY - startY);
});
previewCanvas.addEventListener("mouseup", (e)=>{
  isDragging = false;
});

// crop helper: return base64 dataURL of cropped region relative to original image scale
function getCroppedDataURL(){
  if(!currentImage) return null;
  const x = Math.min(startX, curX);
  const y = Math.min(startY, curY);
  const w = Math.abs(curX - startX);
  const h = Math.abs(curY - startY);
  if(w < 5 || h < 5){
    // no crop -> send whole canvas
    return previewCanvas.toDataURL("image/png");
  }
  // map back to original image coordinates:
  const scale = currentImage.width / previewCanvas.width;
  const sx = Math.round(x * scale);
  const sy = Math.round(y * scale);
  const sw = Math.round(w * scale);
  const sh = Math.round(h * scale);
  // create temporary canvas
  const tmp = document.createElement("canvas");
  tmp.width = sw; tmp.height = sh;
  const tctx = tmp.getContext("2d");
  // draw portion of original image
  const orig = document.createElement("img");
  orig.src = currentImage.src;
  // wait image load (should be loaded)
  tctx.drawImage(currentImage, sx, sy, sw, sh, 0,0, sw, sh);
  return tmp.toDataURL("image/png");
}

// send text query to server
document.getElementById("ask-btn").addEventListener("click", async ()=>{
  const q = document.getElementById("text-query").value;
  if(!q) { alert("Nhập câu hỏi hoặc chọn ảnh"); return; }
  addMessage("user", q);
  const res = await fetch("/api/text-query", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({query:q})});
  const j = await res.json();
  addMessage("bot", j.reply);
  document.getElementById("text-query").value = "";
});

// send full image (no crop)
document.getElementById("upload-btn").addEventListener("click", async ()=>{
  if(!currentImage) { alert("Chưa có ảnh để gửi"); return; }
  const dataUrl = previewCanvas.toDataURL("image/png");
  addMessage("user", "<i>Image uploaded</i>");
  const res = await fetch("/api/upload-image", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({image:dataUrl})});
  const j = await res.json();
  addMessage("bot", JSON.stringify(j.reply));
});

// compare selected modals (uses crop if drawn)
document.getElementById("compare-btn").addEventListener("click", async ()=>{
  const select = document.getElementById("modal-select");
  const selected = Array.from(select.selectedOptions).map(o=>o.value);
  if(!currentImage){ alert("Chưa có ảnh để so sánh"); return; }
  const cropData = getCroppedDataURL();
  addMessage("user", `<i>Compare modals (${selected.join(",")})</i>`);
  const q = document.getElementById("text-query").value || "";
  const res = await fetch("/api/multimodal-reason", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({image: cropData, query: q})});
  const j = await res.json();
  addMessage("bot", JSON.stringify(j.reply));
});
