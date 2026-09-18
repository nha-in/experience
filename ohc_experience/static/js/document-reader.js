// Offers what a document the integrator just chose appears to say. Every value
// is proposed only: the fields stay editable and the server validates them
// again on save, so a wrong reading costs a correction rather than a bad record.
(() => {
  // Form media can be evaluated again when HTMX inserts another form.
  if (window.experienceDocumentReader) {
    window.experienceDocumentReader();
    return;
  }

  const selector = 'input[type="file"][data-read-document]';
  const controllers = new WeakMap();
  const watched = new WeakSet();
  const notes = new WeakMap();
  const TIMEOUT = 60000;
  const FILLED_TEXT = "Filled from the document. Please verify.";
  // The upload script rebuilds the FileList, so identity is not dependable.
  const fingerprint = (file) =>
    file && `${file.name}:${file.size}:${file.lastModified}`;

  const noteId = (control) =>
    `${control.id || control.name}_document_read_note`;

  function describedBy(control, id, present) {
    const ids = (control.getAttribute("aria-describedby") || "")
      .split(/\s+/)
      .filter((value) => value && value !== id);
    if (present) ids.push(id);
    if (ids.length) control.setAttribute("aria-describedby", ids.join(" "));
    else control.removeAttribute("aria-describedby");
  }

  // The field's own help text can describe how the value was guessed. Once the
  // document supplies it that sentence is wrong, so it waits until the reading
  // is undone rather than contradicting the note beside it.
  function helpText(control, document) {
    return control.id
      ? document.getElementById(`${control.id}_helptext`)
      : null;
  }

  function showHelpText(control, document, visible) {
    const help = helpText(control, document);
    if (!help || (visible && help.dataset.documentReadHidden === undefined))
      return;
    help.hidden = !visible;
    if (visible) delete help.dataset.documentReadHidden;
    else help.dataset.documentReadHidden = "";
    describedBy(control, help.id, visible);
  }

  function clearNote(control, document) {
    const note = notes.get(control);
    if (!note) return;
    note.remove();
    notes.delete(control);
    describedBy(control, noteId(control), false);
    showHelpText(control, document, true);
  }

  // Said beside the field itself, because the one message under the file input
  // does not tell the integrator which values moved.
  function addNote(control, document) {
    clearNote(control, document);
    const note = document.createElement("p");
    note.id = noteId(control);
    // Hint styling, not the error palette: the value is fine, it wants a look.
    note.className = "ui-hint font-medium";
    note.dataset.documentReadNote = "filled";
    note.textContent = FILLED_TEXT;
    (control.closest?.(".ui-field") || control.parentElement)?.append(note);
    notes.set(control, note);
    describedBy(control, note.id, true);
    showHelpText(control, document, false);
  }

  function inputsWithin(root) {
    return [
      ...(root.matches?.(selector) ? [root] : []),
      ...(root.querySelectorAll?.(selector) || []),
    ];
  }

  function attach(input) {
    const form = input.closest("form");
    const field = input.closest("[data-read-document-field]");
    const status = field?.querySelector("[data-read-document-status]");
    const overlay = field?.querySelector("[data-read-document-overlay]");
    // The overlay is absolute, so the section has to be its containing block.
    const region = input.closest("fieldset") || field;
    if (!form || !status) return;

    const doc = () => input.ownerDocument || globalThis.document;

    let request;
    let sequence = 0;
    let disposed = false;
    let filling = false;
    let positioned = false;

    // `quiet` keeps the sentence for a screen reader without printing it: the
    // spinner and the notes beside each field already show it on screen.
    function message(text, { quiet = false } = {}) {
      status.textContent = text;
      status.hidden = !text;
      if (text && quiet) status.classList.add("sr-only");
      else status.classList.remove("sr-only");
    }

    function setBusy(busy) {
      if (!overlay) return;
      if (busy && !positioned) {
        positioned = !region.classList.contains("relative");
        region.classList.add("relative");
      }
      if (!busy && positioned) {
        region.classList.remove("relative");
        positioned = false;
      }
      overlay.hidden = !busy;
    }

    // Once the integrator touches the field they have verified it themselves.
    function watch(control) {
      if (watched.has(control)) return;
      watched.add(control);
      const verified = () => {
        if (filling) return;
        clearNote(control, doc());
        control.dataset.documentRead = "false";
      };
      control.addEventListener("input", verified);
      control.addEventListener("change", verified);
    }

    // A value the integrator typed is theirs; blanks, earlier readings and
    // values a script derived are replaced, so re-reading never undoes a
    // correction.
    function fill(name, value) {
      const control = form.querySelector(`[name="${name}"]`);
      if (!control) return false;
      clearNote(control, doc());
      if (!value) return false;
      const derived =
        control.dataset.autofilled === "true" ||
        control.dataset.documentRead === "true";
      if (control.value && !derived) return false;
      if (
        control.tagName === "SELECT" &&
        ![...control.options].some((option) => option.value === value)
      )
        return false;
      filling = true;
      control.value = value;
      control.dataset.documentRead = "true";
      // Selects are searchable, and dates derive the fields that follow them,
      // so the rest of the form has to hear about the new value.
      control.dispatchEvent(new Event("change", { bubbles: true }));
      filling = false;
      addNote(control, doc());
      watch(control);
      return true;
    }

    function apply(fields) {
      setBusy(false);
      const names = Object.keys(fields);
      const filled = names.filter((name) => fill(name, fields[name]));
      const missing = names.filter((name) => !fields[name]);
      if (filled.length) {
        // Each filled field carries its own note, so nothing is said here.
        message("");
        return;
      }
      message(
        missing.length === names.length
          ? "Nothing could be read from this document. Enter the details below."
          : "The details below already differ from the document. Check them before saving.",
      );
    }

    function cancel() {
      sequence += 1;
      request?.abort();
      request = null;
      field.removeAttribute("aria-busy");
    }

    async function read(file, version) {
      const activeRequest = new AbortController();
      request = activeRequest;
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        activeRequest.abort();
      }, TIMEOUT);
      const isCurrent = () =>
        !disposed &&
        input.isConnected &&
        version === sequence &&
        fingerprint(input.files?.[0]) === fingerprint(file);
      try {
        const body = new FormData();
        body.append("intent", "read");
        body.append("field", input.dataset.readDocument);
        body.append(input.dataset.readDocument, file);
        const response = await fetch(
          new URL(form.action || window.location.href, window.location.href),
          {
            method: "POST",
            headers: {
              Accept: "application/json",
              "X-CSRFToken":
                form.querySelector('[name="csrfmiddlewaretoken"]')?.value || "",
            },
            credentials: "same-origin",
            body,
            signal: activeRequest.signal,
          },
        );
        if (!isCurrent()) return;
        if (response.status === 429) {
          setBusy(false);
          message(
            "Too many documents have been read recently. Enter the details below.",
          );
          return;
        }
        // The document itself is the problem, so another attempt cannot help.
        if (response.status === 422) {
          const refused = await response.json().catch(() => ({}));
          if (!isCurrent()) return;
          setBusy(false);
          message(
            typeof refused.error === "string" && refused.error
              ? refused.error
              : "This document could not be read. Enter the details below.",
          );
          return;
        }
        if (!response.ok) throw new Error("Document reading unavailable");
        const result = await response.json();
        if (!isCurrent()) return;
        if (
          result.name !== file.name ||
          !result.fields ||
          typeof result.fields !== "object" ||
          Object.values(result.fields).some(
            (value) => typeof value !== "string",
          )
        ) {
          throw new Error("Invalid reading response");
        }
        apply(result.fields);
      } catch (error) {
        if (!isCurrent() || (error.name === "AbortError" && !timedOut)) return;
        setBusy(false);
        message("Autofill failed. Enter the details below.");
      } finally {
        clearTimeout(timer);
        if (isCurrent()) {
          request = null;
          field.removeAttribute("aria-busy");
        }
      }
    }

    function schedule() {
      cancel();
      setBusy(false);
      const file = input.files?.[0];
      if (!file) {
        message("");
        return;
      }
      message("Reading the certificate…", { quiet: true });
      setBusy(true);
      field.setAttribute("aria-busy", "true");
      read(file, sequence);
    }

    input.addEventListener("change", schedule);
    controllers.set(input, {
      refresh() {},
      dispose() {
        disposed = true;
        cancel();
        setBusy(false);
      },
    });
  }

  function initialize(root = document) {
    inputsWithin(root).forEach((input) => {
      if (controllers.has(input)) controllers.get(input).refresh();
      else attach(input);
    });
  }

  window.experienceDocumentReader = initialize;
  document.addEventListener("DOMContentLoaded", () => initialize());
  document.addEventListener("htmx:load", (event) =>
    initialize(event.detail?.elt || event.target),
  );
})();
