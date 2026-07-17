const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const mobileQuery = window.matchMedia("(max-width: 720px)");
const modalCloseTimers = new Map();

const state = {
  view: "active",
  saveTimer: null,
  refreshTimer: null,
  lastServerContent: null,
  editing: false,
  dragDepth: 0,
  activeModal: null,
  previousFocus: null,
};

const note = $("#note");
const deviceInput = $("#device-name");
const privateMode = $("#private-mode");
const privateIndicator = $("#private-indicator");
const saveStatus = $("#save-status");
const uploadStatus = $("#upload-status");
const connectionDot = $("#connection-dot");
const serverStateText = $("#server-state-text");
const serverDeviceLabel = $("#server-device-label");
const historyPanel = $("#history-panel");
const historyTitle = $("#history-title");
const historySubtitle = $("#history-subtitle");
const historyList = $("#history-list");
const historyEmpty = $("#history-empty");
const fileInput = $("#file-input");
const uploadDropZone = $("#upload-drop-zone");
const dragOverlay = $("#drag-overlay");
const itemTemplate = $("#history-item-template");
const sidebarBackdrop = $("#sidebar-backdrop");
const sidebarToggle = $("#sidebar-toggle");
const sidebarBrandToggle = $("#sidebar-brand-toggle");

function deviceName() {
  return (deviceInput.value.trim() || "Appareil inconnu").slice(0, 64);
}

function setStatus(text, kind = "") {
  saveStatus.textContent = text;
  saveStatus.className = `status ${kind}`;
}

function setOnline(ok) {
  connectionDot.className = `dot ${ok ? "ok" : "error"}`;
  serverStateText.textContent = ok ? "Serveur connecté" : "Serveur indisponible";
}

function updatePreferenceSummary() {
  privateIndicator.hidden = !privateMode.checked;
  serverDeviceLabel.textContent = deviceName();
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
    if (historyPanel.classList.contains("open")) await loadHistory();
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
  try {
    await navigator.clipboard.writeText(text);
  } catch (error) {
    const fallback = document.createElement("textarea");
    fallback.value = text;
    fallback.setAttribute("readonly", "");
    fallback.style.position = "fixed";
    fallback.style.opacity = "0";
    document.body.append(fallback);
    fallback.select();
    document.execCommand("copy");
    fallback.remove();
  }
  setStatus("Copié", "ok");
}

function createFileIcon(mime) {
  const icon = document.createElement("div");
  icon.className = "file-icon";
  icon.textContent = iconFor(mime);
  return icon;
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
        closeHistory();
        note.focus();
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

function setView(view) {
  state.view = view === "trash" ? "trash" : "active";
  $$(".tab").forEach((tab) => {
    const selected = tab.dataset.view === state.view;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", selected ? "true" : "false");
  });
  historyTitle.textContent = state.view === "trash" ? "Corbeille" : "Historique";
  historySubtitle.textContent = state.view === "trash" ? "Éléments supprimés" : "Textes et fichiers";
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

function setActiveSection(section) {
  $$(".nav-item[data-section]").forEach((item) => {
    item.classList.toggle("active", item.dataset.section === section);
  });
}

function closeMobileSidebar() {
  document.body.classList.remove("sidebar-mobile-open");
  sidebarBackdrop.hidden = true;
}

function openMobileSidebar() {
  document.body.classList.add("sidebar-mobile-open");
  sidebarBackdrop.hidden = false;
}

function setSidebarExpanded(expanded, persist = true) {
  document.body.classList.toggle("sidebar-collapsed", !expanded);
  sidebarToggle.setAttribute("aria-expanded", expanded ? "true" : "false");
  sidebarToggle.setAttribute("aria-label", expanded ? "Replier la barre latérale" : "Déplier la barre latérale");
  sidebarBrandToggle.setAttribute("aria-label", expanded ? "Replier la barre latérale" : "Déplier la barre latérale");
  if (persist) localStorage.setItem("shared-note-sidebar-expanded", expanded ? "1" : "0");
}

function toggleSidebar() {
  if (mobileQuery.matches) {
    if (document.body.classList.contains("sidebar-mobile-open")) closeMobileSidebar();
    else openMobileSidebar();
    return;
  }
  setSidebarExpanded(document.body.classList.contains("sidebar-collapsed"));
}

function closeHistory() {
  historyPanel.classList.remove("open");
  historyPanel.setAttribute("aria-hidden", "true");
  setActiveSection("note");
}

async function openHistory(view = "active") {
  closeAllModals(false);
  closeMobileSidebar();
  setView(view);
  historyPanel.classList.add("open");
  historyPanel.setAttribute("aria-hidden", "false");
  setActiveSection(state.view === "trash" ? "trash" : "history");
  await loadHistory();
}

function openModal(id) {
  const layer = document.getElementById(id);
  if (!layer) return;
  closeAllModals(false);
  closeMobileSidebar();
  closeHistory();
  const pending = modalCloseTimers.get(id);
  if (pending) clearTimeout(pending);
  modalCloseTimers.delete(id);
  state.previousFocus = document.activeElement;
  state.activeModal = id;
  layer.hidden = false;
  requestAnimationFrame(() => {
    layer.classList.add("open");
    const focusTarget = layer.querySelector("input:not([type='hidden']), button, [tabindex='0']");
    focusTarget?.focus({ preventScroll: true });
  });
}

function closeModal(id, restoreFocus = true) {
  const layer = document.getElementById(id);
  if (!layer || layer.hidden) return;
  layer.classList.remove("open");
  const timer = setTimeout(() => {
    layer.hidden = true;
    modalCloseTimers.delete(id);
  }, 170);
  modalCloseTimers.set(id, timer);
  if (state.activeModal === id) state.activeModal = null;
  if (restoreFocus && state.previousFocus instanceof HTMLElement) state.previousFocus.focus({ preventScroll: true });
  setActiveSection("note");
}

function closeAllModals(restoreFocus = true) {
  $$(".modal-layer:not([hidden])").forEach((layer) => closeModal(layer.id, restoreFocus));
}

function openUpload() {
  uploadStatus.textContent = "";
  openModal("upload-modal");
  setActiveSection("upload");
}

function openSettings() {
  openModal("settings-modal");
  setActiveSection("settings");
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
    await openHistory("active");
  } catch (error) {
    setOnline(false);
    uploadStatus.textContent = `Erreur : ${error.message}`;
  } finally {
    fileInput.value = "";
  }
}

function hasDraggedFiles(event) {
  return [...(event.dataTransfer?.types || [])].includes("Files");
}

function showDragOverlay() {
  dragOverlay.hidden = false;
}

function hideDragOverlay() {
  state.dragDepth = 0;
  dragOverlay.hidden = true;
}

note.addEventListener("input", queueSave);
note.addEventListener("focus", () => { state.editing = true; });
note.addEventListener("blur", () => {
  if (state.editing) saveNote();
});

deviceInput.value = localStorage.getItem("shared-note-device")
  || navigator.userAgentData?.platform
  || navigator.platform
  || "Laptop";
deviceInput.addEventListener("change", () => {
  localStorage.setItem("shared-note-device", deviceName());
  updatePreferenceSummary();
});

privateMode.checked = localStorage.getItem("shared-note-private") === "1";
privateMode.addEventListener("change", () => {
  localStorage.setItem("shared-note-private", privateMode.checked ? "1" : "0");
  setStatus(privateMode.checked ? "Mode privé" : "Prêt");
  updatePreferenceSummary();
});
updatePreferenceSummary();

setSidebarExpanded(localStorage.getItem("shared-note-sidebar-expanded") === "1", false);

$("#copy-note").addEventListener("click", () => copyText(note.value));
$("#clear-note").addEventListener("click", () => {
  note.value = "";
  queueSave();
  note.focus();
});
$("#close-history").addEventListener("click", closeHistory);
$("#sidebar-toggle").addEventListener("click", toggleSidebar);
sidebarBrandToggle.addEventListener("click", toggleSidebar);
$("#mobile-menu").addEventListener("click", openMobileSidebar);
sidebarBackdrop.addEventListener("click", closeMobileSidebar);

$$(".nav-item[data-section]").forEach((item) => {
  item.addEventListener("click", () => {
    const section = item.dataset.section;
    if (section === "note") {
      closeMobileSidebar();
      closeAllModals(false);
      closeHistory();
      note.focus();
    } else if (section === "upload") {
      openUpload();
    } else if (section === "history") {
      openHistory("active");
    } else if (section === "trash") {
      openHistory("trash");
    } else if (section === "settings") {
      openSettings();
    }
  });
});

$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => openHistory(tab.dataset.view));
});

$$("[data-close-modal]").forEach((button) => {
  button.addEventListener("click", () => closeModal(button.dataset.closeModal));
});

$$(".modal-layer").forEach((layer) => {
  layer.addEventListener("click", (event) => {
    if (event.target === layer) closeModal(layer.id);
  });
});

uploadDropZone.addEventListener("click", () => fileInput.click());
uploadDropZone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});
$("#choose-files").addEventListener("click", (event) => {
  event.stopPropagation();
  fileInput.click();
});
fileInput.addEventListener("change", () => uploadFiles(fileInput.files));

["dragenter", "dragover"].forEach((name) => {
  uploadDropZone.addEventListener(name, (event) => {
    event.preventDefault();
    event.stopPropagation();
    uploadDropZone.classList.add("dragging");
  });
});
["dragleave", "drop"].forEach((name) => {
  uploadDropZone.addEventListener(name, (event) => {
    event.preventDefault();
    event.stopPropagation();
    uploadDropZone.classList.remove("dragging");
  });
});
uploadDropZone.addEventListener("drop", (event) => uploadFiles(event.dataTransfer.files));

document.addEventListener("dragenter", (event) => {
  if (!hasDraggedFiles(event)) return;
  event.preventDefault();
  state.dragDepth += 1;
  showDragOverlay();
});
document.addEventListener("dragover", (event) => {
  if (!hasDraggedFiles(event)) return;
  event.preventDefault();
});
document.addEventListener("dragleave", (event) => {
  if (!hasDraggedFiles(event)) return;
  state.dragDepth = Math.max(0, state.dragDepth - 1);
  if (state.dragDepth === 0) hideDragOverlay();
});
document.addEventListener("drop", (event) => {
  if (!hasDraggedFiles(event)) return;
  event.preventDefault();
  const files = event.dataTransfer.files;
  hideDragOverlay();
  uploadFiles(files);
});

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

document.addEventListener("keydown", (event) => {
  const modifier = event.ctrlKey || event.metaKey;
  if (modifier && event.key.toLowerCase() === "s") {
    event.preventDefault();
    clearTimeout(state.saveTimer);
    saveNote();
    return;
  }
  if (modifier && event.key.toLowerCase() === "b") {
    event.preventDefault();
    toggleSidebar();
    return;
  }
  if (modifier && event.shiftKey && event.key.toLowerCase() === "h") {
    event.preventDefault();
    openHistory("active");
    return;
  }
  if (event.key === "Escape") {
    if (!dragOverlay.hidden) {
      hideDragOverlay();
    } else if (state.activeModal) {
      closeModal(state.activeModal);
    } else if (historyPanel.classList.contains("open")) {
      closeHistory();
    } else if (document.body.classList.contains("sidebar-mobile-open")) {
      closeMobileSidebar();
    }
  }
});

mobileQuery.addEventListener("change", () => closeMobileSidebar());

async function refresh() {
  await loadCurrentNote();
  if (historyPanel.classList.contains("open")) await loadHistory();
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(refresh, 2500);
}

refresh();
