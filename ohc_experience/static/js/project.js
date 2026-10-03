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
  // The end of a period that a date field opens: the period counts its start
  // date, so it ends the day before the anniversary. Date.UTC normalises what
  // that lands on, including a 29 February audit and a period that rolls back
  // into the previous month.
  function periodEnd(value, years) {
    const [year, month, day] = value.split('-').map(Number);
    if (!year || !month || !day) return '';
    return new Date(Date.UTC(year + years, month - 1, day - 1)).toISOString().slice(0, 10);
  }

  // Day first, as every date a person reads is written.
  function dayFirst(value) {
    const [year, month, day] = value.split('-');
    return `${day}/${month}/${year}`;
  }

  // Whether the derived date may take the field: one that is empty, or still
  // holds what the date it follows gave it, whoever saved it there. Anything
  // else the integrator set by hand, and it stays; a date the audit date does
  // not explain is noted beside the field instead.
  function derivable(target, derived = '') {
    return !target.value || target.dataset.autofilled === 'true' || target.value === derived;
  }

  function autofillFromDate(source) {
    const form = source.closest('form');
    const years = Number.parseInt(source.dataset.autofillYears, 10);
    if (!form || !Number.isFinite(years)) return;
    // What the date gave its target before this change. A draft saves the
    // derived expiry like any other answer, so only its value says it was
    // derived; on the page as it arrives, that is what the date gives now.
    const before = source.dataset.autofillFrom ?? source.value;
    const derived = before ? periodEnd(before, years) : '';
    form.querySelectorAll(`[name="${source.dataset.autofillTarget}"]`).forEach(target => {
      if (!derivable(target, derived)) return;
      target.value = source.value ? periodEnd(source.value, years) : '';
      target.dataset.autofilled = target.value ? 'true' : 'false';
    });
    source.dataset.autofillFrom = source.value;
  }

  // A date set by hand to other than the end of the period its source opens,
  // for a certificate stating another period. The form accepts it, so this is
  // a note in the warning colour, never an error, and the field stays valid;
  // the reviewer is shown the same difference. A lapsed date is left to the
  // error that refuses it.
  function notePeriod(target, expected) {
    const message = target.dataset.periodMessage;
    if (!message) return;
    const lapsed = Boolean(target.min) && target.value < target.min;
    const text = !target.disabled && target.value && expected && target.value !== expected && !lapsed
      ? message.replace('{date}', dayFirst(expected))
      : '';
    const id = `${target.id}_period_note`;
    let note = document.getElementById(id);
    if ((note?.textContent || '') === text) return;
    if (text) {
      if (!note) {
        note = document.createElement('p');
        note.id = id;
        note.className = 'ui-warning';
        (target.closest('.ui-field') || target.parentElement).append(note);
      }
      note.textContent = text;
    } else {
      note.remove();
    }
    const described = new Set((target.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
    if (text) described.add(id);
    else described.delete(id);
    target.setAttribute('aria-describedby', [...described].join(' '));
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

  // A milestone submitted alongside this one was usually tested over the same
  // days, so its dates start as this milestone's own and follow them until the
  // integrator changes them. The field being typed in is left alone: a date
  // typed halfway reads as empty.
  function prefillTestingDates(form) {
    const own = {
      '[data-testing-start]': form.querySelector('[name="start_date"]'),
      '[data-testing-end]': form.querySelector('[name="end_date"]'),
    };
    form.querySelectorAll('[data-milestone-dates]').forEach(fields => {
      for (const [selector, source] of Object.entries(own)) {
        const target = fields.querySelector(selector);
        if (!source?.value || !target || target === document.activeElement || !derivable(target)) continue;
        target.value = source.value;
        target.dataset.autofilled = 'true';
      }
    });
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
    // can undercut: the audit itself is capped at today. There is no ceiling:
    // a certificate states its own period, and one other than the year its
    // audit opens is noted beside the field.
    form.querySelectorAll('[data-autofill-target]').forEach(source => {
      const years = Number.parseInt(source.dataset.autofillYears, 10);
      if (!Number.isFinite(years)) return;
      form.querySelectorAll(`[name="${source.dataset.autofillTarget}"]`).forEach(target => {
        notePeriod(target, source.value ? periodEnd(source.value, years) : '');
      });
    });
    flagExpiredCertificate(form);
  }

  // An expiry that has lapsed is refused on submission. Say so beside the
  // field as soon as a date lands there, however it came: typed, or derived
  // from an audit date, including one the certificate reader filled in and
  // announced with a change event.
  function flagExpiredCertificate(form) {
    const expiry = form.querySelector('[name="wasa_valid_until"]');
    const message = expiry?.dataset?.expiredMessage;
    if (!message) return;
    const field = expiry.closest('.ui-field') || expiry.parentElement;
    const live = !expiry.disabled && expiry.value && expiry.value < expiry.min ? message : '';
    // A refused submission prints the same sentence; take that copy as ours.
    const said = [...field.querySelectorAll('.ui-error')].filter(error => error.textContent.trim() === message);
    if (said.length === (live ? 1 : 0)) return;
    const id = `${expiry.id}_errors`;
    said.forEach(error => {
      const box = error.parentElement;
      error.remove();
      if (!box.children.length) box.remove();
    });
    if (live) {
      let box = document.getElementById(id);
      if (!box) {
        box = document.createElement('div');
        box.id = id;
        field.append(box);
      }
      const error = document.createElement('span');
      error.className = 'ui-error';
      error.textContent = live;
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
    prefillTestingDates(form);
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
    const blocked = form.dataset.submitBlocked === 'true';
    button.disabled = blocked || missing > 0;
    // Complete says nothing: the enabled button is the message.
    if (reason && !blocked) reason.textContent = missing
      ? `${missing} ${missing === 1 ? 'field needs' : 'fields need'} attention before you can ${action}.`
      : '';
    const jump = form.querySelector('[data-submit-missing]');
    if (jump) jump.hidden = missing === 0;
  }

  function updateDecision(form) {
    const action = form.querySelector('[name="action"]:checked')?.value || 'approve';
    const note = form.querySelector('[name="note"]');
    const labels = { approve: ['Decision note', 'Record approval'], reject: ['Note for the integrator', 'Reject request'], query: ['Query', 'Send queries'] };
    const label = form.querySelector('[data-decision-label]');
    if (label) label.textContent = labels[action][0];
    // Forms that list reasons ask for one; the rest take the note alone.
    const reason = form.querySelector('[data-reason-select]');
    const chosen = reason?.options[reason.selectedIndex];
    const hint = form.querySelector('[data-reason-hint]');
    if (hint) hint.textContent = reason && !reason.value
      ? 'Choose a reason before rejecting.'
      : 'The integrator sees this reason above your note.';
    const queued = updateQueries(form, action);
    const button = form.querySelector('[data-decision-submit]');
    if (button) {
      button.textContent = action === 'query'
        ? (queued ? `Send ${queued} ${queued === 1 ? 'query' : 'queries'}` : 'Send queries')
        : labels[action][1];
      // Prerequisites hold every decision but a query; open queries hold approval;
      // a query needs something to send.
      button.disabled = (action !== 'query' && form.dataset.decisionBlocked === 'true')
        || (action === 'approve' && form.dataset.approvalBlocked === 'true')
        || (action === 'reject' && Boolean(reason) && !reason.value)
        || (action === 'query' && !queued);
    }
    // Every decision needs the reviewer's own words, except a rejection a listed
    // reason already explains. Other, and a form with no list to choose from,
    // leave the note carrying the reason. A query's words are in its boxes.
    if (note) {
      note.required = action !== 'query' && (action !== 'reject'
        || !reason || 'noteRequired' in (chosen?.dataset || {}));
      // The floor guards a required note. Zero is the unconstrained default,
      // for the aside beside a listed reason.
      note.minLength = note.required ? Number(note.dataset.noteMinlength) : 0;
      // The asterisk beside the note's label shows only while the note is required.
      const marker = form.querySelector('[data-note-marker]');
      if (marker) marker.hidden = !note.required;
    }
    form.querySelectorAll('[data-query-controls]').forEach(el => { el.hidden = action !== 'query'; });
    form.querySelectorAll('[data-note-controls]').forEach(el => { el.hidden = action === 'query'; });
    form.querySelectorAll('[data-approval-controls]').forEach(el => { el.hidden = action !== 'approve'; });
    form.querySelectorAll('[data-reject-controls]').forEach(el => { el.hidden = action !== 'reject'; });
  }

  // The query boxes beside a review's submitted form, each joined to its
  // decision form by the form's id: Query opens one under its field, Remove
  // closes and clears it.
  function queryBoxes(form) {
    return form.id ? [...document.querySelectorAll(`[data-query-for="${form.id}"] [data-query-draft]`)] : [];
  }

  function queryButton(form, box) {
    return document.querySelector(`[data-query-form="${form.id}"][data-query-field="${box.dataset.queryDraft}"]`);
  }

  // Lists the open queries in the decision form, and says how many go in one
  // send. A query goes only when Query is chosen: under another decision it is set
  // aside, neither sent nor holding the form back.
  function updateQueries(form, action) {
    const querying = action === 'query';
    const open = queryBoxes(form).filter(box => {
      const question = box.querySelector('textarea');
      if (question) question.disabled = box.hidden || !querying;
      box.closest('[data-query-row]')?.toggleAttribute('data-query-selected', querying && !box.hidden);
      // A field with its box open needs no Query beside it.
      const button = queryButton(form, box)?.closest('[data-query-button]');
      if (button) button.hidden = !box.hidden;
      return !box.hidden;
    });
    const queries = `${open.length} ${open.length === 1 ? 'query' : 'queries'}`;
    const count = form.querySelector('[data-query-count]');
    if (count) {
      count.textContent = `${queries} to send`;
      count.hidden = !open.length;
    }
    const list = form.querySelector('[data-query-list]');
    const template = form.querySelector('template[data-query-list-item]');
    if (list && template) {
      list.replaceChildren(...open.map(box => {
        const entry = template.content.firstElementChild.cloneNode(true);
        const about = box.dataset.queryDraft === 'form' ? 'the whole form' : box.dataset.queryLabel;
        const link = entry.querySelector('[data-query-link]');
        link.textContent = box.dataset.queryLabel;
        link.href = `#${box.id}`;
        entry.querySelector('[data-query-excerpt]').textContent = box.querySelector('textarea')?.value.trim() || 'Nothing written yet.';
        const remove = entry.querySelector('[data-query-remove]');
        remove.dataset.queryBox = box.id;
        remove.querySelector('[data-query-remove-label]').textContent = `Remove the query about ${about}`;
        return entry;
      }));
      list.hidden = !open.length;
    }
    const aside = form.querySelector('[data-query-aside]');
    if (aside) {
      aside.textContent = open.length === 1
        ? 'Your query is not sent with this decision. Choose Query to send it.'
        : `Your ${queries} are not sent with this decision. Choose Query to send them.`;
      aside.hidden = querying || !open.length;
    }
    return open.length;
  }

  // Query beside a field opens its query box and chooses Query in the decision
  // form, without the page load its link falls back to.
  function openQuery(link) {
    const form = document.getElementById(link.dataset.queryForm);
    const query = form?.querySelector('[name="action"][value="query"]');
    const box = queryBoxes(form || {}).find(candidate => candidate.dataset.queryDraft === link.dataset.queryField);
    if (!query || query.disabled || !box) return false;
    query.checked = true;
    box.hidden = false;
    updateDecision(form);
    box.querySelector('textarea')?.focus();
    return true;
  }

  // Remove clears a query and closes its box. Focus stays where the
  // reviewer was: on the field's Query, or in the decision form's list.
  function removeQuery(button) {
    const box = button.dataset.queryBox ? document.getElementById(button.dataset.queryBox) : button.closest('[data-query-draft]');
    const question = box?.querySelector('textarea');
    const form = question?.form;
    if (!form) return;
    question.value = '';
    box.hidden = true;
    updateDecision(form);
    const next = button.dataset.queryBox
      ? form.querySelector('[data-query-list] [data-query-remove]') || form.querySelector('[name="action"][value="query"]')
      : queryButton(form, box);
    next?.focus();
  }

  function initialize(scope = document) {
    // Drafts and rejected submissions can arrive with the audit date saved and
    // the expiry still blank; fill it before counting what needs attention.
    scope.querySelectorAll?.('[data-autofill-target]').forEach(source => autofillFromDate(source));
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
    // A query box belongs to its decision form only through the form attribute.
    const decision = event.target.form;
    if (decision?.matches?.('[data-decision-form]') && event.target.closest('[data-query-draft]')) updateDecision(decision);
  });
  document.addEventListener('drop', event => {
    const form = event.target.closest('[data-review-form]');
    if (form) setTimeout(() => updateSubmission(form), 0);
  });
  document.addEventListener('click', event => {
    const open = event.target.closest('[data-query-field]');
    if (open && openQuery(open)) event.preventDefault();
    const remove = event.target.closest('[data-query-remove]');
    if (remove) removeQuery(remove);
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

// Assign, Reassign and a ticket's Change priority open their panel straight onto
// the list. Closing it, with Cancel or Escape, forgets a choice that was never
// saved. Toggle events do not bubble, so this listens while capturing.
document.addEventListener("toggle", (event) => {
  const panel = event.target;
  if (!panel.matches?.("details[data-picker-panel]")) return;
  if (!panel.open) {
    panel.querySelector("form")?.reset();
    return;
  }
  const search = panel.querySelector('[role="combobox"]');
  search?.focus();
  search?.click();
}, true);
document.addEventListener("keydown", (event) => {
  const panel = event.target.closest?.("details[data-picker-panel][open]");
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
