(() => {
  const selectedFiles = new WeakMap();

  const fileKey = (file) => `${file.name}:${file.size}:${file.lastModified}`;

  const formatBytes = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const setInputFiles = (input, files) => {
    const transfer = new DataTransfer();
    files.forEach((file) => transfer.items.add(file));
    input.files = transfer.files;
  };

  const updateExistingFile = (checkbox) => {
    const row = checkbox.closest("[data-existing-file]");
    const toggle = row?.querySelector("[data-existing-file-toggle]");
    const removeIcon = row?.querySelector("[data-remove-icon]");
    const undoIcon = row?.querySelector("[data-undo-icon]");
    const root = checkbox.closest("[data-file-upload]");
    removeIcon?.classList.toggle("hidden", checkbox.checked);
    undoIcon?.classList.toggle("hidden", !checkbox.checked);
    if (toggle && root) {
      toggle.title = checkbox.checked
        ? root.dataset.undoLabel
        : root.dataset.removeLabel;
    }
  };

  const makeSelectedFileRow = (root, input, file, index) => {
    const item = document.createElement("li");
    item.className = "flex min-h-12 items-center gap-2.5 px-3 py-2";

    const icon = document.createElement("span");
    icon.className =
      "flex size-8 shrink-0 items-center justify-center rounded-md border border-primary/20 bg-card text-primary";
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML =
      '<svg viewBox="0 0 24 24" class="size-4" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M8 13h8M8 17h6"/></svg>';

    const details = document.createElement("span");
    details.className = "min-w-0 flex-1";
    const name = document.createElement("span");
    name.className = "block truncate text-xs font-medium";
    name.textContent = file.name;
    const size = document.createElement("span");
    size.className = "block text-[11px] text-soft-foreground";
    size.textContent = formatBytes(file.size);
    details.append(name, size);

    const badge = document.createElement("span");
    badge.className = "ui-badge ui-badge--muted ui-badge--size-xs";
    badge.textContent = root.dataset.newLabel;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className =
      "ui-btn ui-btn--ghost ui-btn--size-icon-xs shrink-0 text-muted-foreground";
    remove.title = root.dataset.removeLabel;
    remove.setAttribute("aria-label", `${root.dataset.removeLabel}: ${file.name}`);
    remove.innerHTML =
      '<svg viewBox="0 0 24 24" class="size-4" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M18 6 6 18M6 6l12 12"/></svg>';
    remove.addEventListener("click", () => {
      const files = selectedFiles.get(input) || [];
      files.splice(index, 1);
      selectedFiles.set(input, files);
      setInputFiles(input, files);
      renderUpload(root);
    });

    item.append(icon, details, badge, remove);
    return item;
  };

  const renderUpload = (root) => {
    const input = root.querySelector('input[type="file"]');
    if (!input) return;
    const files = selectedFiles.get(input) || [];
    const selectedPanel = root.querySelector("[data-selected-files]");
    const selectedList = root.querySelector("[data-selected-file-list]");
    const selectedCount = root.querySelector("[data-selected-count]");
    const summary = root.querySelector("[data-file-summary]");
    const limitError = root.querySelector("[data-file-limit-error]");
    const dropzone = root.querySelector("[data-file-dropzone]");
    const isMultiple = root.dataset.multiple === "true";
    const maxFiles = Number.parseInt(root.dataset.maxFiles, 10) || 0;
    const retainedCount = Array.from(
      root.querySelectorAll("[data-existing-file-remove]"),
    ).filter((checkbox) => !checkbox.checked).length;
    const finalCount = isMultiple
      ? retainedCount + files.length
      : files.length
        ? 1
        : retainedCount;

    selectedList?.replaceChildren(
      ...files.map((file, index) =>
        makeSelectedFileRow(root, input, file, index),
      ),
    );
    selectedPanel?.classList.toggle("hidden", files.length === 0);
    if (selectedCount) {
      selectedCount.textContent = `${files.length} ${
        files.length === 1 ? root.dataset.fileLabel : root.dataset.filesLabel
      }`;
    }
    if (summary) {
      const countLabel =
        finalCount === 1 ? root.dataset.fileLabel : root.dataset.filesLabel;
      summary.textContent = maxFiles
        ? `${finalCount} of ${maxFiles} ${root.dataset.filesLabel} ${root.dataset.afterSaveLabel}`
        : `${finalCount} ${countLabel} ${root.dataset.afterSaveLabel}`;
    }

    const overLimit = maxFiles > 0 && finalCount > maxFiles;
    limitError?.classList.toggle("hidden", !overLimit);
    if (limitError) limitError.textContent = root.dataset.limitMessage;
    dropzone?.classList.toggle("border-destructive", overLimit);
    input.setCustomValidity(overLimit ? root.dataset.limitMessage : "");
  };

  const mergeSelectedFiles = (root, incomingFiles) => {
    const input = root.querySelector('input[type="file"]');
    if (!input) return;
    const previous = selectedFiles.get(input) || [];
    const candidates =
      root.dataset.multiple === "true"
        ? [...previous, ...incomingFiles]
        : incomingFiles.slice(-1);
    const uniqueFiles = [];
    const keys = new Set();
    candidates.forEach((file) => {
      const key = fileKey(file);
      if (!keys.has(key)) {
        keys.add(key);
        uniqueFiles.push(file);
      }
    });
    selectedFiles.set(input, uniqueFiles);
    setInputFiles(input, uniqueFiles);
    renderUpload(root);
  };

  const initializeUpload = (root) => {
    if (root.dataset.uploadInitialized === "true") return;
    const input = root.querySelector('input[type="file"]');
    const dropzone = root.querySelector("[data-file-dropzone]");
    if (!input || !dropzone) return;
    root.dataset.uploadInitialized = "true";
    selectedFiles.set(input, Array.from(input.files || []));

    input.addEventListener("change", () => {
      mergeSelectedFiles(root, Array.from(input.files || []));
    });
    root.querySelectorAll("[data-existing-file-remove]").forEach((checkbox) => {
      updateExistingFile(checkbox);
      checkbox.addEventListener("change", () => {
        updateExistingFile(checkbox);
        renderUpload(root);
      });
    });
    ["dragenter", "dragover"].forEach((eventName) => {
      dropzone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropzone.classList.add("border-primary", "bg-primary/5");
      });
    });
    ["dragleave", "drop"].forEach((eventName) => {
      dropzone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropzone.classList.remove("border-primary", "bg-primary/5");
      });
    });
    dropzone.addEventListener("drop", (event) => {
      mergeSelectedFiles(root, Array.from(event.dataTransfer?.files || []));
    });
    renderUpload(root);
  };

  const initializeUploads = (scope = document) => {
    if (scope.matches?.("[data-file-upload]")) initializeUpload(scope);
    scope.querySelectorAll?.("[data-file-upload]").forEach(initializeUpload);
  };

  document.addEventListener("DOMContentLoaded", () => initializeUploads());
  document.addEventListener("htmx:afterSwap", (event) =>
    initializeUploads(event.detail.elt),
  );
})();
