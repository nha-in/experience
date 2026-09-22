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
  // Offer a derived date without taking the field away: once the integrator
  // edits the target themselves, their value is never overwritten.
  // The period counts the start date, so it ends the day before the
  // anniversary. Date.UTC normalises what that lands on, including a 29
  // February audit and a period that rolls back into the previous month.
  function periodEnd(value, years) {
    const [year, month, day] = value.split('-').map(Number);
    if (!year || !month || !day) return '';
    return new Date(Date.UTC(year + years, month - 1, day - 1)).toISOString().slice(0, 10);
  }

  function autofillFromDate(source) {
    const form = source.closest('form');
    const years = Number.parseInt(source.dataset.autofillYears, 10);
    if (!form || !Number.isFinite(years)) return;
    form.querySelectorAll(`[name="${source.dataset.autofillTarget}"]`).forEach(target => {
      if (target.value && target.dataset.autofilled !== 'true') return;
      target.value = source.value ? periodEnd(source.value, years) : '';
      target.dataset.autofilled = target.value ? 'true' : 'false';
    });
  }

  function updateWasaFields(form) {
    const choice = form.querySelector('[name="use_product_wasa"]');
    if (!choice) return;
    form.querySelectorAll('[data-wasa-source-options]').forEach(options => {
      options.hidden = !choice.checked;
    });
    form.querySelectorAll('[data-wasa-upload]').forEach(field => {
      field.hidden = choice.checked;
      field.querySelectorAll('input, select, textarea').forEach(input => {
        input.disabled = choice.checked;
        if (['wasa_agency', 'wasa_date', 'wasa_valid_until'].includes(input.name)) {
          input.required = !choice.checked;
        }
      });
      if (field.dataset.wasaFromProduct === 'true') {
        const savedFiles = field.querySelector('[data-existing-files]');
        if (savedFiles) savedFiles.parentElement.hidden = true;
        field.querySelectorAll('[data-existing-file-remove]').forEach(input => {
          input.checked = true;
          input.disabled = true;
        });
        const summary = field.querySelector('[data-file-summary]');
        if (summary) summary.textContent = 'Upload a new certificate';
      }
    });
  }

  // Chrome rebuilds a date field whenever its min is assigned, even to the
  // value it already holds, and the rebuild wipes a date typed halfway. This
  // runs on every keystroke, so only a new value is written.
  function setMin(input, value) {
    if (input.min !== value) input.min = value;
  }

  function updateDateConstraints(form) {
    const start = form.querySelector('[name="start_date"]');
    const end = form.querySelector('[name="end_date"]');
    const demo = form.querySelector('[name="tentative_demo_date"]');
    if (start && end) setMin(end, start.value || '');
    form.querySelectorAll('[data-testing-dates]').forEach(fields => {
      const ownStart = fields.querySelector('[data-testing-start]');
      const ownEnd = fields.querySelector('[data-testing-end]');
      if (ownStart && ownEnd) setMin(ownEnd, ownStart.value || '');
    });
    // The server caps testing at today; a demo cannot be earlier than today
    // even when testing ended in the past or its end date is cleared.
    if (end && demo) setMin(demo, end.value > end.max ? end.value : end.max);
    // The server floors a certificate's expiry at today, which no audit date
    // can undercut: the audit itself is capped at today. So nothing here moves
    // it; the only work left is to say why an earlier date is refused.
    flagExpiredCertificate(form);
  }

  // An expired certificate is refused on submission. Say so beside the field
  // as soon as a date lands there, however it came: typed, picked, or read off
  // the certificate, whose reader announces its values with a change event.
  function flagExpiredCertificate(form) {
    const expiry = form.querySelector('[name="wasa_valid_until"]');
    const message = expiry?.dataset?.expiredMessage;
    if (!message) return;
    const field = expiry.closest('.ui-field') || expiry.parentElement;
    const expired = !expiry.disabled && Boolean(expiry.value) && expiry.value < expiry.min;
    // A refused submission prints the same sentence; take that copy as ours.
    const said = [...field.querySelectorAll('.ui-error')].find(error => error.textContent.trim() === message);
    if (expired === Boolean(said)) return;
    const id = `${expiry.id}_errors`;
    if (said) {
      const box = said.parentElement;
      said.remove();
      if (!box.children.length) box.remove();
    } else {
      let box = document.getElementById(id);
      if (!box) {
        box = document.createElement('div');
        box.id = id;
        field.append(box);
      }
      const error = document.createElement('span');
      error.className = 'ui-error';
      error.textContent = message;
      box.append(error);
    }
    // Another error the server printed keeps the field invalid on its own.
    const invalid = Boolean(document.getElementById(id));
    const described = new Set((expiry.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
    if (invalid) {
      described.add(id);
      expiry.setAttribute('aria-invalid', 'true');
    } else {
      described.delete(id);
      expiry.removeAttribute('aria-invalid');
    }
    expiry.setAttribute('aria-describedby', [...described].join(' '));
  }

  function updateMilestoneSelection(form, changed) {
    const current = form.dataset.currentMilestoneCode;
    if (!current || form.dataset.autoApprove === 'true') return;
    const choices = [...form.querySelectorAll('[name="additional_reviews"]')];
    const byId = new Map(choices.map(choice => [choice.dataset.reviewId, choice]));
    const requires = choice => (choice.dataset.requires || '').split(/\s+/).filter(Boolean);
    if (changed?.dataset?.reviewId && !changed.checked) {
      const removed = new Set([changed.dataset.reviewId]);
      let updated;
      do {
        updated = false;
        for (const choice of choices) {
          if (choice.checked && requires(choice).some(id => removed.has(id))) {
            choice.checked = false;
            removed.add(choice.dataset.reviewId);
            updated = true;
          }
        }
      } while (updated);
    }
    const visited = new Set();
    function includeRequirements(choice) {
      if (visited.has(choice.dataset.reviewId)) return;
      visited.add(choice.dataset.reviewId);
      for (const id of requires(choice)) {
        const required = byId.get(id);
        if (required && !required.disabled) {
          required.checked = true;
          includeRequirements(required);
        }
      }
    }
    choices.filter(choice => choice.checked).forEach(includeRequirements);
    form.querySelectorAll('[data-milestone-dates]').forEach(fields => {
      const choice = byId.get(fields.dataset.milestoneDates);
      const selected = Boolean(choice?.checked && !choice.disabled);
      fields.hidden = !selected;
      fields.querySelectorAll('[data-testing-start], [data-testing-end]').forEach(input => {
        input.disabled = !selected;
        input.required = selected;
      });
    });
    const codes = [current, ...choices.filter(choice => choice.checked).map(choice => choice.dataset.milestoneCode)];
    const label = form.querySelector('[data-submit-label]');
    if (label) label.textContent = codes.length > 2
      ? `Submit ${codes.length} milestones`
      : `Submit ${codes.join(' + ')}`;
    const summary = form.querySelector('[data-milestone-selection-summary]');
    if (summary) summary.textContent = codes.length > 1
      ? `${codes.join(', ')} will be submitted together.`
      : `Only ${current} will be submitted.`;
  }

  function updateSubmission(form) {
    updateMilestoneSelection(form);
    updateWasaFields(form);
    updateDateConstraints(form);
    const button = form.querySelector('[data-request-submit]');
    const reason = form.querySelector('[data-submit-reason]');
    if (!button) return;
    const available = input => !input.matches(':disabled') && !input.closest('[hidden]');
    const missingFields = [...form.querySelectorAll('input, select, textarea')].filter(input => available(input) && !input.validity.valid);
    const missingGroups = [...form.querySelectorAll('[data-required-checkbox-group]')].filter(group => {
      const choices = [...group.querySelectorAll('input[type="checkbox"]')].filter(available);
      return choices.length > 0 && !choices.some(input => input.checked);
    });
    const missingFiles = [...form.querySelectorAll('[data-required-upload]')].filter(field => {
      const input = field.querySelector('input[type="file"]');
      if (!input || !available(input)) return false;
      const retained = [...field.querySelectorAll('[data-existing-file-remove]')].some(checkbox => !checkbox.checked);
      return !input?.files.length && !retained;
    });
    const missing = new Set([
      ...missingFields.map(input => input.name),
      ...missingGroups.map(group => group.dataset.requiredCheckboxGroup),
      ...missingFiles.map(field => field.dataset.requiredUpload),
    ]).size;
    const autoApprove = form.dataset.autoApprove === 'true';
    const approvedUpdate = form.dataset.approvedUpdates === 'true';
    const action = approvedUpdate ? 'submit your update' : autoApprove ? 'record participation' : 'request review';
    button.disabled = missing > 0;
    // Complete says nothing: the enabled button is the message.
    if (reason) reason.textContent = missing
      ? `${missing} ${missing === 1 ? 'field needs' : 'fields need'} attention before you can ${action}.`
      : '';
    const jump = form.querySelector('[data-submit-missing]');
    if (jump) jump.hidden = missing === 0;
  }

  function updateDecision(form) {
    const action = form.querySelector('[name="action"]:checked')?.value || 'approve';
    const note = form.querySelector('[name="note"]');
    const labels = { approve: ['Decision note', 'Record approval'], reject: ['Note for the integrator', 'Reject request'], query: ['Question', 'Send query'] };
    const label = form.querySelector('[data-decision-label]');
    if (label) label.textContent = labels[action][0];
    // Forms that list reasons ask for one; the rest take the note alone.
    const reason = form.querySelector('[data-reason-select]');
    const chosen = reason?.options[reason.selectedIndex];
    const hint = form.querySelector('[data-reason-hint]');
    if (hint) hint.textContent = reason && !reason.value
      ? 'Choose a reason before rejecting.'
      : 'The integrator sees this reason above your note.';
    const button = form.querySelector('[data-decision-submit]');
    if (button) {
      button.textContent = labels[action][1];
      // Prerequisites hold every decision but a query; open queries hold approval.
      button.disabled = (action !== 'query' && form.dataset.decisionBlocked === 'true')
        || (action === 'approve' && form.dataset.approvalBlocked === 'true')
        || (action === 'reject' && Boolean(reason) && !reason.value);
    }
    // Every decision needs the reviewer's own words, except a rejection a listed
    // reason already explains. Other, and a form with no list to choose from,
    // leave the note carrying the reason.
    if (note) {
      note.required = action !== 'reject'
        || !reason || 'noteRequired' in (chosen?.dataset || {});
      // The floor guards a required note. Zero is the unconstrained default,
      // for the aside beside a listed reason.
      note.minLength = note.required ? Number(note.dataset.noteMinlength) : 0;
    }
    form.querySelectorAll('[data-query-controls]').forEach(el => { el.hidden = action !== 'query'; });
    form.querySelectorAll('[data-approval-controls]').forEach(el => { el.hidden = action !== 'approve'; });
    form.querySelectorAll('[data-reject-controls]').forEach(el => { el.hidden = action !== 'reject'; });
  }

  function initialize(scope = document) {
    // Drafts and rejected submissions can arrive with the audit date saved and
    // the expiry still blank; fill it before counting what needs attention.
    scope.querySelectorAll?.('[data-autofill-target]').forEach(autofillFromDate);
    scope.querySelectorAll?.('[data-review-form]').forEach(updateDateConstraints);
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
    const source = event.target.closest('[data-autofill-target]');
    if (source) autofillFromDate(source);
    const edited = event.target.closest('[data-autofilled]');
    if (edited && edited !== source) edited.dataset.autofilled = 'false';
    const form = event.target.closest('[data-review-form]');
    if (form) updateMilestoneSelection(form, event.target);
    // Conditional fields are synchronized by another change listener below.
    if (form) queueMicrotask(() => updateSubmission(form));
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
  const copy = event.target.closest("[data-copy], [data-copy-value], [data-copy-from]");
  if (!copy || !navigator.clipboard) return;
  // data-copy-from names an element whose visible text is copied, leaving out its hidden parts.
  const source = copy.dataset.copyFrom && document.getElementById(copy.dataset.copyFrom);
  try {
    await navigator.clipboard.writeText(
      source ? source.innerText.replace(/\s+/g, " ").trim() : copy.dataset.copy ?? copy.dataset.copyValue,
    );
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

// Assign and Reassign open the panel straight onto the reviewer list. Closing it,
// with Cancel or Escape, forgets a choice that was never saved. Toggle events do
// not bubble, so this listens while capturing.
document.addEventListener("toggle", (event) => {
  const panel = event.target;
  if (!panel.matches?.("details[data-assign-panel]")) return;
  if (!panel.open) {
    panel.querySelector("form")?.reset();
    return;
  }
  const search = panel.querySelector('[role="combobox"]');
  search?.focus();
  search?.click();
}, true);
document.addEventListener("keydown", (event) => {
  const panel = event.target.closest?.("details[data-assign-panel][open]");
  if (event.key !== "Escape" || !panel) return;
  panel.open = false;
  panel.querySelector("summary").focus();
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

// The reference environment's credential fields fill in its run commands as they are
// typed, escaping single quotes the way each shell needs. The form never submits.
(() => {
  document.addEventListener("input", (event) => {
    const input = event.target.closest?.("[data-reference-credential]");
    const form = input?.closest("[data-reference-run]");
    if (!form) return;
    const value = input.value.trim();
    form
      .querySelectorAll(`[data-credential-slot="${input.dataset.referenceCredential}"]`)
      .forEach((slot) => {
        const { singleQuote } = slot.closest("[data-single-quote]").dataset;
        slot.textContent = value ? value.replaceAll("'", singleQuote) : input.placeholder;
      });
  });
  document.addEventListener(
    "submit",
    (event) => {
      if (!event.target.matches?.("[data-reference-run]")) return;
      event.preventDefault();
      event.stopPropagation();
    },
    true,
  );
})();

// Choosing a skill scrolls the install panel back into view; the form never submits.
(() => {
  document.addEventListener("change", (event) => {
    const page = event.target.closest?.("[data-agent-skills]");
    if (!page || event.target.name !== "skill") return;
    page
      .querySelector("[data-agent-skills-install]")
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });
  document.addEventListener(
    "submit",
    (event) => {
      if (!event.target.matches?.("[data-agent-skills]")) return;
      event.preventDefault();
      event.stopPropagation();
    },
    true,
  );
})();

// Product reviews stay compact until a reviewer opens a request or follows its link.
(() => {
  function revealReview(hash) {
    if (!hash || hash === '#') return;
    let id;
    try { id = decodeURIComponent(hash.slice(1)); } catch { return; }
    const target = document.getElementById(id);
    const panel = target?.closest('details[data-product-review-panel]');
    if (!panel) return;
    const approved = panel.closest('details[data-approved-reviews]');
    if (approved) approved.open = true;
    panel.open = true;
    target.scrollIntoView({ block: 'start' });
  }

  function prepareBulkDecision(form, event) {
    const note = form.querySelector('[name="note"]');
    if (!note) return;
    note.required = true;
    note.setCustomValidity('');
    const panel = form.querySelector('[data-product-bulk-note]');
    if (panel) panel.open = true;
    // The floor is the one the markup already declares, so it is stated once.
    const written = note.value.trim();
    if (written && written.length >= note.minLength) return;
    event.preventDefault();
    event.stopPropagation();
    note.setCustomValidity(written
      ? `Use at least ${note.minLength} characters.`
      : 'Enter the shared decision note before continuing.');
    note.focus();
    note.reportValidity();
  }

  document.addEventListener('DOMContentLoaded', () => revealReview(window.location.hash));
  document.addEventListener('htmx:afterSettle', () => revealReview(window.location.hash));
  window.addEventListener('hashchange', () => revealReview(window.location.hash));
  document.addEventListener('click', event => {
    const link = event.target.closest?.('a[href^="#"]');
    if (link) revealReview(link.getAttribute('href'));
    const button = event.target.closest?.('[data-product-bulk-form] button[name="action"]');
    if (button) prepareBulkDecision(button.form, event);
  }, true);
  document.addEventListener('submit', event => {
    if (event.target.matches?.('[data-product-bulk-form]')) {
      prepareBulkDecision(event.target, event);
    }
  }, true);
  document.addEventListener('input', event => {
    if (event.target.matches?.('[data-product-bulk-form] [name="note"]')) {
      event.target.setCustomValidity('');
    }
  });
})();
