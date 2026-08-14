(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const currentFolderId = $("#currentFolderId").value || "";

  /* ---------------------------------------------------------------
     Generic dropdown / popover handling (New button, user menu,
     per-card ⋮ menus). Only one menu open at a time.
  --------------------------------------------------------------- */
  function closeAllMenus(except) {
    $$(".dropdown-menu, .card-menu").forEach((m) => {
      if (m !== except) m.classList.add("hidden");
    });
  }

  document.addEventListener("click", (e) => {
    if (!e.target.closest(".dropdown-menu, .card-menu, #newBtn, #userChip, .card-menu-btn")) {
      closeAllMenus();
    }
  });

  const newBtn = $("#newBtn");
  const newMenu = $("#newMenu");
  newBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const willOpen = newMenu.classList.contains("hidden");
    closeAllMenus();
    newMenu.classList.toggle("hidden", !willOpen);
  });

  const userChip = $("#userChip");
  const userMenu = $("#userMenu");
  userChip.addEventListener("click", (e) => {
    e.stopPropagation();
    const willOpen = userMenu.classList.contains("hidden");
    closeAllMenus();
    userMenu.classList.toggle("hidden", !willOpen);
  });

  $$(".card-menu-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      e.preventDefault();
      const target = $("#menu-" + btn.dataset.menuTarget);
      const willOpen = target.classList.contains("hidden");
      closeAllMenus();
      target.classList.toggle("hidden", !willOpen);
    });
  });

  /* ---------------------------------------------------------------
     View toggle: grid vs list
  --------------------------------------------------------------- */
  const gridBtn = $("#gridViewBtn");
  const listBtn = $("#listViewBtn");
  const gridView = $("#gridView");
  const listView = $("#listView");

  function setView(mode) {
    const isGrid = mode === "grid";
    gridView.classList.toggle("hidden", !isGrid);
    listView.classList.toggle("hidden", isGrid);
    gridBtn.classList.toggle("active", isGrid);
    listBtn.classList.toggle("active", !isGrid);
    localStorage.setItem("cv_view", mode);
  }
  gridBtn.addEventListener("click", () => setView("grid"));
  listBtn.addEventListener("click", () => setView("list"));
  setView(localStorage.getItem("cv_view") || "grid");

  /* ---------------------------------------------------------------
     New folder modal
  --------------------------------------------------------------- */
  const folderModal = $("#folderModal");
  $("#menuNewFolder").addEventListener("click", () => {
    closeAllMenus();
    folderModal.classList.remove("hidden");
    $("#folderForm input[name=name]").focus();
  });
  $("#cancelFolderBtn").addEventListener("click", () => folderModal.classList.add("hidden"));
  folderModal.addEventListener("click", (e) => { if (e.target === folderModal) folderModal.classList.add("hidden"); });

  /* ---------------------------------------------------------------
     Rename modal (works for both files and folders)
  --------------------------------------------------------------- */
  const renameModal = $("#renameModal");
  const renameForm = $("#renameForm");
  const renameInput = $("#renameInput");

  function openRename(kind, id, name) {
    closeAllMenus();
    renameForm.action = `/${kind}/${id}/rename`;
    renameInput.value = name;
    renameModal.classList.remove("hidden");
    renameInput.focus();
    renameInput.select();
  }
  $$(".rename-file-btn").forEach((btn) => {
    btn.addEventListener("click", () => openRename("file", btn.dataset.id, btn.dataset.name));
  });
  $$(".rename-folder-btn").forEach((btn) => {
    btn.addEventListener("click", () => openRename("folder", btn.dataset.id, btn.dataset.name));
  });
  $("#cancelRenameBtn").addEventListener("click", () => renameModal.classList.add("hidden"));
  renameModal.addEventListener("click", (e) => { if (e.target === renameModal) renameModal.classList.add("hidden"); });

  /* ---------------------------------------------------------------
     Upload: via "File upload" menu item, or drag & drop anywhere
     onto the drive area. Files are sent to /upload where the server
     encrypts them (with the key derived from the user's password at
     login) before writing to storage/.
  --------------------------------------------------------------- */
  const fileInput = $("#fileInput");
  const dropZone = $("#dropZone");
  const dragOverlay = $("#dragOverlay");
  const uploadToast = $("#uploadToast");
  const uploadToastList = $("#uploadToastList");
  const uploadToastTitle = $("#uploadToastTitle");

  $("#menuUploadFiles").addEventListener("click", () => {
    closeAllMenus();
    fileInput.click();
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files.length) uploadFiles(fileInput.files);
    fileInput.value = "";
  });

  let dragCounter = 0;
  dropZone.addEventListener("dragenter", (e) => {
    e.preventDefault();
    dragCounter++;
    dragOverlay.classList.remove("hidden");
  });
  dropZone.addEventListener("dragover", (e) => e.preventDefault());
  dropZone.addEventListener("dragleave", () => {
    dragCounter--;
    if (dragCounter <= 0) { dragCounter = 0; dragOverlay.classList.add("hidden"); }
  });
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dragCounter = 0;
    dragOverlay.classList.add("hidden");
    if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
  });

  function iconFor(mime, name) {
    mime = mime || "";
    name = (name || "").toLowerCase();
    if (mime.startsWith("image/")) return "🖼️";
    if (mime.startsWith("video/")) return "🎬";
    if (mime.startsWith("audio/")) return "🎵";
    if (mime === "application/pdf") return "📕";
    if (mime.startsWith("text/") || name.endsWith(".txt") || name.endsWith(".md")) return "📝";
    if (mime.includes("zip") || mime.includes("compressed") || name.endsWith(".zip") || name.endsWith(".rar")) return "🗜️";
    if (name.endsWith(".doc") || name.endsWith(".docx")) return "📘";
    if (name.endsWith(".xls") || name.endsWith(".xlsx") || name.endsWith(".csv")) return "📊";
    if (name.endsWith(".ppt") || name.endsWith(".pptx")) return "📙";
    if (mime.includes("javascript") || /\.(py|js|ts|java|c|cpp|go|rs|html|css|json)$/.test(name)) return "💻";
    return "📄";
  }

  function uploadFiles(fileList) {
    const files = Array.from(fileList);
    uploadToast.classList.remove("hidden");
    uploadToastTitle.textContent = `Encrypting & uploading ${files.length} item${files.length > 1 ? "s" : ""}…`;
    uploadToastList.innerHTML = "";

    const rows = {};
    files.forEach((f) => {
      const row = document.createElement("div");
      row.className = "upload-item";
      row.innerHTML = `<span class="upload-item-icon">${iconFor(f.type, f.name)}</span>
                        <span class="upload-item-name">${f.name}</span>
                        <span class="upload-item-status">encrypting…</span>`;
      uploadToastList.appendChild(row);
      rows[f.name] = row.querySelector(".upload-item-status");
    });

    const formData = new FormData();
    files.forEach((f) => formData.append("files", f));
    formData.append("folder_id", currentFolderId);

    fetch("/upload", { method: "POST", body: formData })
      .then((r) => r.json())
      .then((data) => {
        if (data.ok) {
          uploadToastTitle.textContent = "Upload complete";
          (data.saved || []).forEach((name) => {
            if (rows[name]) { rows[name].textContent = "done"; rows[name].classList.add("done"); }
          });
          setTimeout(() => window.location.reload(), 700);
        } else {
          uploadToastTitle.textContent = "Upload failed";
          Object.values(rows).forEach((el) => { el.textContent = "error"; el.classList.add("error"); });
        }
      })
      .catch(() => {
        uploadToastTitle.textContent = "Upload failed";
        Object.values(rows).forEach((el) => { el.textContent = "error"; el.classList.add("error"); });
      });
  }

  $("#closeToastBtn").addEventListener("click", () => uploadToast.classList.add("hidden"));

  /* ---------------------------------------------------------------
     File preview modal - fetches decrypted content from
     /file/<id>/raw (server decrypts on the fly using the session key)
  --------------------------------------------------------------- */
  const previewModal = $("#previewModal");
  const previewTitle = $("#previewTitle");
  const previewBody = $("#previewBody");
  const previewDownload = $("#previewDownload");

  function openPreview(id, name, mime) {
    closeAllMenus();
    previewTitle.textContent = name;
    previewDownload.href = `/file/${id}/download`;
    previewBody.innerHTML = `<div class="no-preview">Loading preview…</div>`;
    previewModal.classList.remove("hidden");

    mime = mime || "";
    const rawUrl = `/file/${id}/raw`;

    if (mime.startsWith("image/")) {
      previewBody.innerHTML = `<img src="${rawUrl}" alt="${name}">`;
    } else if (mime.startsWith("video/")) {
      previewBody.innerHTML = `<video src="${rawUrl}" controls autoplay></video>`;
    } else if (mime.startsWith("audio/")) {
      previewBody.innerHTML = `<audio src="${rawUrl}" controls style="width:100%"></audio>`;
    } else if (mime === "application/pdf") {
      previewBody.innerHTML = `<iframe src="${rawUrl}"></iframe>`;
    } else if (mime.startsWith("text/") || mime === "application/json") {
      fetch(rawUrl).then((r) => r.text()).then((text) => {
        const pre = document.createElement("pre");
        pre.textContent = text.slice(0, 200000);
        previewBody.innerHTML = "";
        previewBody.appendChild(pre);
      }).catch(() => {
        previewBody.innerHTML = `<div class="no-preview">Couldn't load preview.</div>`;
      });
    } else {
      previewBody.innerHTML = `<div class="no-preview">No inline preview available for this file type.<br>Use Download to open it.</div>`;
    }
  }

  function wirePreviewTriggers(root) {
    $$(".card[data-type=file] .preview-trigger, .row-item[data-type=file] .preview-trigger", root).forEach((el) => {
      el.addEventListener("click", (e) => {
        e.preventDefault();
        const card = el.closest("[data-type=file]");
        openPreview(card.dataset.id, card.dataset.name, card.dataset.mime);
      });
    });
  }
  wirePreviewTriggers(document);

  $("#closePreviewBtn").addEventListener("click", () => {
    previewModal.classList.add("hidden");
    previewBody.innerHTML = "";
  });
  previewModal.addEventListener("click", (e) => {
    if (e.target === previewModal) {
      previewModal.classList.add("hidden");
      previewBody.innerHTML = "";
    }
  });

  /* ---------------------------------------------------------------
     Assign file-type icons to grid cards (computed client-side so
     we reuse the same mapping used for uploads/preview)
  --------------------------------------------------------------- */
  $$(".file-card").forEach((card) => {
    const icon = card.querySelector(".file-icon");
    if (icon) icon.textContent = iconFor(card.dataset.mime, card.dataset.name);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      previewModal.classList.add("hidden");
      folderModal.classList.add("hidden");
      renameModal.classList.add("hidden");
      previewBody.innerHTML = "";
      closeAllMenus();
    }
  });
})();
