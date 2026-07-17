const $ = (selector) => document.querySelector(selector);

const state = {
  view: "active",
  saveTimer: null,
  refreshTimer: null,
  lastServerContent: null,
  editing: false,
};

const note = $("#note");
const deviceInput = $("#device-name");
const privateMode = $("#private-mode");
const saveStatus = $("#save-status");
const uploadStatus = $("#upload-status");
const connectionDot = $("#connection-dot");
const historyPanel = $("#history-panel");
const historyList = $("#history-list");
const historyEmpty = $("#history-empty");
const fileInput = $("#file-input");
const dropZone = $("#drop-zone");
const itemTemplate = $("#history-item-template");

function deviceName() {
  return (deviceInput.value.trim() || "Appareil inconnu").slice(0, 64);
}

function setStatus(text, kind = "") {
  saveStatus.textContent = text;
  saveStatus.className = `status ${kind}`;
}

function setOnline(ok) {
  connectionDot.className = `dot ${ok ? "ok" : "error"}`;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const message = typeof body === "object" ? body.message : body;
    throw new Error(message || `HTTP ${response.status}`);
  }
  setOnline(true);
  return body;
}

async function loadCurrentNote() {
  try {
    const content = await api("/content");
    if (!state.editing && content !== state.lastServerContent) {
      note.value = content;
      state.lastServerContent = content;
    }
  } catch (error) {
    setOnline(false);
  }
}

async function saveNote() {
  const content = note.value;
  setStatus("Envoi…");
  try {
    await api(`/save?device=${encodeURIComponent(deviceName())}&private=${privateMode.checked ? "1" : "0"}`, {
      method: "POST",
      headers: { "Content-Type": "text/plain; charset=utf-8" },
      body: content,
    });
    state.lastServerContent = content;
    state.editing = false;
    setStatus(privateMode.checked ? "Privé" : "Synchronisé", "ok");
    await loadHistory();
  } catch (error) {
    setOnline(false);
    setStatus("Erreur", "error");
  }
}

function queueSave() {
  state.editing = true;
  setStatus("Modifié");
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(saveNote, 650);
}

function formatBytes(value) {
  if (value == null) return "";
  const units = ["o", "Ko", "Mo", "Go", "To"];
  let size = Number(value);
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  const digits = index === 0 || size >= 10 ? 0 : 1;
  return `${size.toFixed(digits)} ${units[index]}`;
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  return new Intl.DateTimeFormat("fr-FR", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function iconFor(mime) {
  if (!mime) return "◇";
  if (mime.startsWith("image/")) return "▧";
  if (mime.startsWith("video/")) return "▶";
  if (mime.startsWith("audio/")) return "♫";
  if (mime === "application/pdf") return "PDF";
  if (mime.includes("zip") || mime.includes("compressed")) return "ZIP";
  return "▤";
}

function makeButton(label, action, className = "subtle") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `button ${className}`;
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

async function copyText(text) {
  await navigator.clipboard.writeText(text);
  setStatus("Copié", "ok");
}

function renderItem(item) {
  const node = itemTemplate.content.firstElementChild.cloneNode(true);
  node.classList.toggle("pinned", Boolean(item.pinned));

  const previewSlot = node.querySelector(".preview-slot");
  if (item.content_type === "file") {
    if (item.preview_url) {
      const image = document.createElement("img");
      image.src = `${item.preview_url}?v=${encodeURIComponent(item.created_at)}`;
      image.alt = "";
      image.loading = "lazy";
      image.addEventListener("error", () => {
        image.replaceWith(createFileIcon(item.mime_type));
      });
      previewSlot.append(image);
    } else {
      previewSlot.append(createFileIcon(item.mime_type));
    }
  }

  node.querySelector(".type-badge").textContent = item.content_type;
  node.querySelector("time").textContent = formatDate(item.created_at);
  node.querySelector(".item-title").textContent =
    item.content_type === "file" ? item.original_name : item.text_content.slice(0, 80) || "Note vide";

  const textPreview = node.querySelector(".text-preview");
  if (item.content_type === "file") {
    textPreview.remove();
  } else {
    textPreview.textContent = item.text_content;
  }

  const meta = [];
  if (item.device_name) meta.push(item.device_name);
  if (item.size != null) meta.push(formatBytes(item.size));
  if (item.mime_type) meta.push(item.mime_type);
  node.querySelector(".item-meta").textContent = meta.join(" · ");

  const actions = node.querySelector(".item-actions");
  if (state.view === "trash") {
    actions.append(makeButton("Restaurer", async () => {
      await api(`/files/${item.uuid}/restore`, { method: "POST" });
      await loadHistory();
    }));
  } else {
    if (item.content_type === "file") {
      actions.append(makeButton("Télécharger", () => {
        window.location.href = item.download_url;
      }));
    } else {
      actions.append(makeButton("Copier", () => copyText(item.text_content)));
      actions.append(makeButton("Restaurer", () => {
        note.value = item.text_content;
        queueSave();
      }));
    }
    actions.append(makeButton(item.pinned ? "Désépingler" : "Épingler", async () => {
      await api(`/files/${item.uuid}/pin`, { method: "POST" });
      await loadHistory();
    }));
    actions.append(makeButton("Supprimer", async () => {
      await api(`/files/${item.uuid}`, { method: "DELETE" });
      await loadHistory();
    }, "danger-ghost"));
  }
  return node;
}

function createFileIcon(mime) {
  const icon = document.createElement("div");
  icon.className = "file-icon";
  icon.textContent = iconFor(mime);
  return icon;
}

async function loadHistory() {
  try {
    const query = state.view === "trash" ? "?trash=1" : "";
    const items = await api(`/history${query}`);
    const fragment = document.createDocumentFragment();
    items.forEach((item) => fragment.append(renderItem(item)));
    historyList.replaceChildren(fragment);
    historyEmpty.hidden = items.length > 0;
  } catch (error) {
    setOnline(false);
  }
}

async function uploadFiles(files) {
  if (!files || files.length === 0) return;
  const form = new FormData();
  form.append("device_name", deviceName());
  [...files].forEach((file) => form.append("files", file, file.name || "clipboard-image.png"));
  uploadStatus.textContent = `Envoi de ${files.length} fichier${files.length > 1 ? "s" : ""}…`;
  try {
    await api("/upload", { method: "POST", body: form });
    uploadStatus.textContent = "Envoyé ✓";
    historyPanel.classList.add("open");
    await loadHistory();
    setTimeout(() => { uploadStatus.textContent = ""; }, 2200);
  } catch (error) {
    setOnline(false);
    uploadStatus.textContent = `Erreur : ${error.message}`;
  } finally {
    fileInput.value = "";
  }
}

note.addEventListener("input", queueSave);
note.addEventListener("focus", () => { state.editing = true; });
note.addEventListener("blur", () => {
  if (state.editing) saveNote();
});

deviceInput.value = localStorage.getItem("shared-note-device") || navigator.platform || "Laptop";
deviceInput.addEventListener("change", () => {
  localStorage.setItem("shared-note-device", deviceName());
});
privateMode.checked = localStorage.getItem("shared-note-private") === "1";
privateMode.addEventListener("change", () => {
  localStorage.setItem("shared-note-private", privateMode.checked ? "1" : "0");
  setStatus(privateMode.checked ? "Mode privé" : "Prêt");
});

$("#copy-note").addEventListener("click", () => copyText(note.value));
$("#clear-note").addEventListener("click", () => {
  note.value = "";
  queueSave();
  note.focus();
});
$("#history-toggle").addEventListener("click", () => historyPanel.classList.toggle("open"));
$("#close-history").addEventListener("click", () => historyPanel.classList.remove("open"));

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((button) => button.classList.remove("active"));
    tab.classList.add("active");
    state.view = tab.dataset.view;
    loadHistory();
  });
});

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") fileInput.click();
});
fileInput.addEventListener("change", () => uploadFiles(fileInput.files));

["dragenter", "dragover"].forEach((name) => {
  dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    dropZone.classList.add("dragging");
  });
});
["dragleave", "drop"].forEach((name) => {
  dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
  });
});
dropZone.addEventListener("drop", (event) => uploadFiles(event.dataTransfer.files));

document.addEventListener("paste", (event) => {
  const files = [...(event.clipboardData?.items || [])]
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter(Boolean);
  if (files.length) {
    event.preventDefault();
    uploadFiles(files);
  }
});

async function refresh() {
  await Promise.all([loadCurrentNote(), loadHistory()]);
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(refresh, 2500);
}

refresh();
