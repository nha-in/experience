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
      input.dispatchEvent(new Event("input", { bubbles: true }));
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
    input.dispatchEvent(new Event("input", { bubbles: true }));
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


(() => {
  function updateSubmission(form) {
    const button = form.querySelector('[data-request-submit]');
    const reason = form.querySelector('[data-submit-reason]');
    if (!button) return;
    const missingFields = [...form.querySelectorAll('input, select, textarea')].filter(input => !input.disabled && !input.validity.valid);
    const missingFiles = [...form.querySelectorAll('[data-required-upload]')].filter(field => {
      const input = field.querySelector('input[type="file"]');
      const retained = [...field.querySelectorAll('[data-existing-file-remove]')].some(checkbox => !checkbox.checked);
      return !input?.files.length && !retained;
    });
    const missing = new Set([...missingFields.map(input => input.name), ...missingFiles.map(field => field.dataset.requiredUpload)]).size;
    const blocked = form.dataset.reviewBlocked === 'true';
    button.disabled = blocked || missing > 0;
    if (reason) reason.textContent = blocked ? 'Required approvals are pending. You can still save a draft.' : missing ? `${missing} ${missing === 1 ? 'field needs' : 'fields need'} attention before you can request review.` : 'All required fields are complete. Ready to request review.';
    const jump = form.querySelector('[data-submit-missing]');
    if (jump) jump.hidden = missing === 0;
  }

  function updateDecision(form) {
    const action = form.querySelector('[name="action"]:checked')?.value || 'approve';
    const note = form.querySelector('[name="note"]');
    const labels = { approve: ['Decision note', 'Record approval'], send_back: ['Reason for sending back', 'Send back to integrator'], query: ['Question', 'Send query'] };
    const label = form.querySelector('[data-decision-label]');
    if (label) label.textContent = labels[action][0];
    const button = form.querySelector('[data-decision-submit]');
    if (button) { button.textContent = labels[action][1]; button.disabled = action === 'approve' && form.dataset.approvalBlocked === 'true'; }
    if (note) note.required = action !== 'approve';
    form.querySelectorAll('[data-query-controls]').forEach(el => { el.hidden = action !== 'query'; });
    form.querySelectorAll('[data-approval-controls]').forEach(el => { el.hidden = action !== 'approve'; });
  }

  function initialize(scope = document) {
    scope.querySelectorAll?.('[data-review-form]').forEach(updateSubmission);
    scope.querySelectorAll?.('[data-decision-form]').forEach(updateDecision);
    scope.querySelectorAll?.('[data-revealed-secret]').forEach(secret => {
      if (secret.dataset.maskScheduled) return;
      secret.dataset.maskScheduled = 'true';
      setTimeout(() => { if (secret.isConnected) hideSecret(secret.closest('[data-secret-container], #secret-value')); }, 30000);
    });
  }

  function hideSecret(root, restoreFocus = false) {
    if (!root) return;
    const template = root.querySelector('template[data-secret-masked]');
    if (template) {
      root.replaceChildren(template.content.cloneNode(true), template);
      window.htmx?.process(root);
      if (restoreFocus) root.querySelector('button')?.focus();
      return;
    }
    const masked = document.createElement('span');
    masked.textContent = 'Hidden. Reload to reveal again.';
    root.replaceChildren(masked);
  }

  document.addEventListener('DOMContentLoaded', () => initialize());
  document.addEventListener('htmx:afterSettle', () => initialize());
  document.addEventListener('change', event => {
    const switcher = event.target.closest('[data-product-switch]');
    if (switcher) {
      switcher.form.requestSubmit();
    }
    const decision = event.target.closest('[data-decision-form]');
    if (decision) updateDecision(decision);
    const form = event.target.closest('[data-review-form]');
    if (form) updateSubmission(form);
  });
  document.addEventListener('input', event => {
    const form = event.target.closest('[data-review-form]');
    if (form) updateSubmission(form);
  });
  document.addEventListener('drop', event => {
    const form = event.target.closest('[data-review-form]');
    if (form) setTimeout(() => updateSubmission(form), 0);
  });
  document.addEventListener('click', event => {
    const hide = event.target.closest('[data-hide-secret]');
    if (hide) hideSecret(hide.closest('[data-secret-container], #secret-value'), true);
    const form = event.target.closest('[data-review-form]');
    if (form) setTimeout(() => updateSubmission(form), 0);
  });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) document.querySelectorAll('[data-revealed-secret]').forEach(secret => hideSecret(secret.closest('[data-secret-container], #secret-value')));
  });
})();

// Copy controls use only values already visible to the authorised account.
document.addEventListener("click", async (event) => {
  const copy = event.target.closest("[data-copy], [data-copy-value]");
  if (!copy || !navigator.clipboard) return;
  try {
    await navigator.clipboard.writeText(copy.dataset.copy ?? copy.dataset.copyValue);
    const idle = copy.querySelector("[data-icon-copy]");
    const done = copy.querySelector("[data-icon-done]");
    idle?.classList.add("hidden");
    done?.classList.remove("hidden");
    const label = copy.getAttribute("aria-label");
    const visibleLabel = copy.querySelector("[data-copy-label]");
    const originalLabel = visibleLabel?.textContent;
    if (visibleLabel) visibleLabel.textContent = "Copied";
    copy.setAttribute("aria-label", "Copied");
    setTimeout(() => {
      idle?.classList.remove("hidden");
      done?.classList.add("hidden");
      if (label) copy.setAttribute("aria-label", label);
      if (visibleLabel) visibleLabel.textContent = originalLabel;
    }, 1600);
  } catch { copy.setAttribute("aria-label", "Copy unavailable; select and copy the value"); }
});

(() => {
  const syncConditionalFields = (root) => {
    const scope = root instanceof Element ? root : document;
    scope.querySelectorAll("[data-show-when-field]").forEach((target) => {
      const form = target.closest("form");
      if (!form) return;
      const { showWhenField: name, showWhenValue: value } = target.dataset;
      const answered = [...form.querySelectorAll(`[name="${name}"]`)].some(
        (input) =>
          input.value === value &&
          (input.type === "checkbox" || input.type === "radio" ? input.checked : true),
      );
      target.hidden = !answered;
    });
  };
  document.addEventListener("change", (event) => {
    const form = event.target.closest?.("form");
    if (form) syncConditionalFields(form);
  });
  document.addEventListener("DOMContentLoaded", () => syncConditionalFields(document));
  document.body?.addEventListener?.("htmx:afterSwap", (event) =>
    syncConditionalFields(event.target),
  );
})();
