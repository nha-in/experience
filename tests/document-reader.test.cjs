const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = readFileSync(
  join(__dirname, "../ohc_experience/static/js/document-reader.js"),
  "utf8",
);

const TODAY = "2026-03-10";
const AGENCY = "M/s A3S Tech & Company";

// A small DOM harness keeps the async behavior tests dependency-free.
function createPage(initial = {}) {
  class Element extends EventTarget {
    constructor(value = "") {
      super();
      this.value = value;
      this.attributes = new Map();
      this.hidden = false;
      this.textContent = "";
      this.isConnected = true;
      this.dataset = {};
      this.tagName = "INPUT";
      this.children = [];
      this.parentElement = null;
      const tokens = new Set();
      this.classList = {
        add: (name) => tokens.add(name),
        remove: (name) => tokens.delete(name),
        contains: (name) => tokens.has(name),
      };
    }
    setAttribute(name, value) {
      this.attributes.set(name, value);
    }
    getAttribute(name) {
      return this.attributes.get(name) ?? null;
    }
    removeAttribute(name) {
      this.attributes.delete(name);
    }
    append(child) {
      child.parentElement = this;
      this.children.push(child);
    }
    remove() {
      const siblings = this.parentElement?.children;
      if (siblings) siblings.splice(siblings.indexOf(this), 1);
      this.parentElement = null;
    }
  }
  const wrappers = new Map();
  function wrap(control) {
    const wrapper = new Element();
    wrappers.set(control, wrapper);
    control.closest = (selector) => (selector === ".ui-field" ? wrapper : null);
    return control;
  }
  const notesUnder = (control) =>
    wrappers.get(control).children.filter((child) => child.id);
  const input = new Element();
  const agency = wrap(new Element(initial.agency || ""));
  const auditDate = wrap(new Element(initial.auditDate || ""));
  const validUntil = wrap(new Element(initial.validUntil || ""));
  const csrf = new Element("token");
  const status = new Element();
  const retry = new Element();
  const overlay = new Element();
  const fieldset = new Element();
  const field = new Element();
  const form = new Element();
  const document = new Element();
  const window = new Element();
  document.createElement = () => new Element();
  agency.name = "wasa_agency";
  auditDate.name = "wasa_date";
  validUntil.name = "wasa_valid_until";
  [agency, auditDate, validUntil].forEach((control) => {
    control.id = `id_${control.name}`;
  });
  // Only the expiry carries help text, as on the real form.
  const expiryHelp = new Element();
  expiryHelp.id = "id_wasa_valid_until_helptext";
  expiryHelp.textContent = "Filled in to cover one year from the audit date.";
  validUntil.setAttribute("aria-describedby", expiryHelp.id);
  document.getElementById = (id) => (id === expiryHelp.id ? expiryHelp : null);
  overlay.hidden = true;
  agency.tagName = "SELECT";
  agency.options = [{ value: "" }, { value: AGENCY }];
  if (initial.autofilled) validUntil.dataset.autofilled = "true";
  input.files = [];
  input.dataset = { readDocument: "wasa_certificate" };
  input.matches = (selector) =>
    selector === 'input[type="file"][data-read-document]';
  input.closest = (selector) =>
    ({ form, "[data-read-document-field]": field, fieldset })[selector];
  form.action = "";
  form.querySelector = (selector) =>
    ({
      '[name="wasa_agency"]': agency,
      '[name="wasa_date"]': auditDate,
      '[name="wasa_valid_until"]': validUntil,
      '[name="csrfmiddlewaretoken"]': csrf,
    })[selector];
  field.querySelector = (selector) =>
    ({
      "[data-read-document-status]": status,
      "[data-read-document-retry]": retry,
      "[data-read-document-overlay]": overlay,
    })[selector];
  document.querySelectorAll = () => (input.isConnected ? [input] : []);
  window.location = { href: "https://example.test/products/ABC/tracks/PHR/" };
  const requests = [];
  const timers = new Map();
  let now = 0;
  let timerId = 0;
  const context = vm.createContext({
    document,
    window,
    Event,
    URL,
    AbortController,
    FormData: class {
      constructor() {
        this.entries = [];
      }
      append(name, value) {
        this.entries.push([name, value]);
      }
    },
    setTimeout(callback, delay) {
      timers.set(++timerId, { callback, due: now + delay });
      return timerId;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    fetch(url, options) {
      return new Promise((resolve, reject) => {
        options.signal?.addEventListener("abort", () => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        });
        requests.push({ url, options, resolve, reject });
      });
    },
  });
  // The first run defines the reader; the second is the re-entry that form
  // media performs, and is what attaches without a DOMContentLoaded.
  vm.runInContext(script, context);
  vm.runInContext(script, context);

  function tick(duration = 60000) {
    now += duration;
    for (;;) {
      const next = [...timers].find(([, timer]) => timer.due <= now);
      if (!next) break;
      timers.delete(next[0]);
      next[1].callback();
    }
  }
  function choose(name = "wasa.pdf", size = 1024) {
    input.files = [{ name, size, lastModified: 1 }];
    input.dispatchEvent(new Event("change"));
  }
  async function flush() {
    for (let index = 0; index < 8; index += 1) await Promise.resolve();
  }
  async function respond(
    index,
    fields,
    responseStatus = 200,
    name = "wasa.pdf",
  ) {
    const request = requests[index];
    request.resolve({
      ok: responseStatus === 200,
      status: responseStatus,
      json: async () =>
        responseStatus === 200
          ? { name, fields }
          : { error: fields?.error, retryable: responseStatus !== 422 },
    });
    await flush();
  }
  return {
    input,
    agency,
    auditDate,
    validUntil,
    status,
    retry,
    field,
    requests,
    choose,
    tick,
    respond,
    flush,
    notesUnder,
    overlay,
    fieldset,
    expiryHelp,
  };
}

const read = {
  wasa_agency: AGENCY,
  wasa_date: TODAY,
  wasa_valid_until: "2027-03-09",
};

test("fills the empty fields from the document without announcing it twice", async () => {
  const page = createPage();
  page.choose();
  assert.equal(page.requests.length, 1);
  const { options } = page.requests[0];
  assert.equal(options.method, "POST");
  assert.equal(options.headers["X-CSRFToken"], "token");
  assert.deepEqual(
    options.body.entries.map(([name]) => name),
    ["intent", "field", "wasa_certificate"],
  );
  assert.deepEqual(options.body.entries[0], ["intent", "read"]);
  assert.equal(page.field.attributes.get("aria-busy"), "true");
  await page.respond(0, read);
  assert.equal(page.agency.value, AGENCY);
  assert.equal(page.auditDate.value, TODAY);
  assert.equal(page.validUntil.value, "2027-03-09");
  // The note beside each field is the report; a summary would only repeat it.
  assert.equal(page.status.textContent, "");
  assert.equal(page.status.hidden, true);
  assert.equal(page.retry.hidden, true);
  assert.equal(page.field.attributes.has("aria-busy"), false);
});

test("a partial reading stays quiet too", async () => {
  const page = createPage();
  page.choose();
  await page.respond(0, { ...read, wasa_valid_until: "" });

  assert.equal(page.status.hidden, true);
  assert.equal(page.notesUnder(page.auditDate).length, 1);
  assert.equal(page.notesUnder(page.validUntil).length, 0);
});

test("never overwrites a value the integrator typed", async () => {
  const page = createPage({ auditDate: "2025-01-02" });
  page.choose();
  await page.respond(0, read);
  assert.equal(page.auditDate.value, "2025-01-02");
  assert.equal(page.agency.value, AGENCY);
});

test("each filled field says so beneath itself", async () => {
  const page = createPage();
  page.choose();
  await page.respond(0, read);
  for (const control of [page.agency, page.auditDate, page.validUntil]) {
    const [note] = page.notesUnder(control);
    assert.equal(note.textContent, "Filled from the document. Please verify.");
    assert.equal(note.dataset.documentReadNote, "filled");
    assert.match(note.className, /ui-hint/);
    // Read out with the field, not left as decoration.
    assert.match(control.getAttribute("aria-describedby"), new RegExp(note.id));
  }
});

test("choosing a document covers the section until the fields are filled", async () => {
  const page = createPage();
  assert.equal(page.overlay.hidden, true);

  page.choose();

  assert.equal(page.overlay.hidden, false);
  // Kept for a screen reader, but the spinner is what the eye follows.
  assert.equal(page.status.textContent, "Reading the certificate…");
  assert.ok(page.status.classList.contains("sr-only"));
  // Absolute positioning needs the section as its containing block.
  assert.ok(page.fieldset.classList.contains("relative"));

  await page.respond(0, read);

  assert.equal(page.overlay.hidden, true);
  assert.ok(!page.status.classList.contains("sr-only"));
  assert.ok(!page.fieldset.classList.contains("relative"));
  assert.equal(page.auditDate.value, TODAY);
});

test("a failed reading uncovers the section", async () => {
  const page = createPage();
  page.choose();
  await page.respond(0, {}, 503);

  assert.equal(page.overlay.hidden, true);
  assert.ok(!page.fieldset.classList.contains("relative"));
  assert.match(page.status.textContent, /could not be read/);
});

test("a document that can never be read uncovers the section too", async () => {
  const page = createPage();
  page.choose();
  await page.respond(0, { error: "This certificate is a scan." }, 422);

  assert.equal(page.overlay.hidden, true);
  assert.ok(!page.fieldset.classList.contains("relative"));
});

test("clearing the file uncovers the section", () => {
  const page = createPage();
  page.choose();
  page.input.files = [];
  page.input.dispatchEvent(new Event("change"));

  assert.equal(page.overlay.hidden, true);
  assert.ok(!page.fieldset.classList.contains("relative"));
});

test("a section already positioned keeps its own class", async () => {
  const page = createPage();
  page.fieldset.classList.add("relative");

  page.choose();
  await page.respond(0, read);

  assert.ok(page.fieldset.classList.contains("relative"));
});

test("help text that describes a guess waits while the document supplies it", async () => {
  const page = createPage();
  assert.equal(page.expiryHelp.hidden, false);

  page.choose();
  await page.respond(0, read);

  // "Filled in to cover one year from the audit date" is no longer true.
  assert.equal(page.expiryHelp.hidden, true);
  assert.equal(
    page.validUntil.getAttribute("aria-describedby"),
    "id_wasa_valid_until_document_read_note",
  );
});

test("the help text returns when the integrator takes the field back", async () => {
  const page = createPage();
  page.choose();
  await page.respond(0, read);

  page.validUntil.value = "2027-01-01";
  page.validUntil.dispatchEvent(new Event("input"));

  assert.equal(page.expiryHelp.hidden, false);
  assert.equal(
    page.validUntil.getAttribute("aria-describedby"),
    "id_wasa_valid_until_helptext",
  );
});

test("help text on a field the document did not fill is left alone", async () => {
  const page = createPage();
  page.choose();
  await page.respond(0, { ...read, wasa_valid_until: "" });

  assert.equal(page.expiryHelp.hidden, false);
});

test("a field left alone carries no note", async () => {
  const page = createPage({ auditDate: "2025-01-02" });
  page.choose();
  await page.respond(0, read);
  assert.equal(page.notesUnder(page.auditDate).length, 0);
  assert.equal(page.notesUnder(page.agency).length, 1);
});

test("the note goes once the integrator verifies the field", async () => {
  const page = createPage();
  page.choose();
  await page.respond(0, read);
  assert.equal(page.notesUnder(page.auditDate).length, 1);

  page.auditDate.value = "2026-01-01";
  page.auditDate.dispatchEvent(new Event("input"));

  assert.equal(page.notesUnder(page.auditDate).length, 0);
  assert.equal(page.auditDate.getAttribute("aria-describedby"), null);
  assert.equal(page.notesUnder(page.agency).length, 1);
});

test("a verified value is not overwritten by a later reading", async () => {
  const page = createPage();
  page.choose();
  await page.respond(0, read);
  page.auditDate.value = "2026-01-01";
  page.auditDate.dispatchEvent(new Event("input"));

  page.choose("second.pdf");
  await page.respond(1, read, 200, "second.pdf");

  assert.equal(page.auditDate.value, "2026-01-01");
  assert.equal(page.notesUnder(page.auditDate).length, 0);
});

test("re-reading leaves one note per field, not a pile", async () => {
  const page = createPage();
  page.choose();
  await page.respond(0, read);
  page.choose("second.pdf");
  await page.respond(1, read, 200, "second.pdf");

  assert.equal(page.notesUnder(page.agency).length, 1);
  assert.equal(page.notesUnder(page.validUntil).length, 1);
});

test("a blank reading clears the notes from the reading before it", async () => {
  const page = createPage();
  page.choose();
  await page.respond(0, read);
  page.choose("second.pdf");
  await page.respond(
    1,
    { wasa_agency: "", wasa_date: "", wasa_valid_until: "" },
    200,
    "second.pdf",
  );

  assert.equal(page.notesUnder(page.agency).length, 0);
  assert.equal(page.notesUnder(page.auditDate).length, 0);
});

test("the document value replaces one another script derived", async () => {
  const page = createPage({ validUntil: "2027-03-09", autofilled: true });
  page.choose();
  await page.respond(0, { ...read, wasa_valid_until: "2026-09-30" });
  assert.equal(page.validUntil.value, "2026-09-30");
});

test("a choice the dropdown does not offer is ignored", async () => {
  const page = createPage();
  page.choose();
  await page.respond(0, { ...read, wasa_agency: "Unlisted agency" });
  assert.equal(page.agency.value, "");
  assert.equal(page.auditDate.value, TODAY);
});

test("a blank reading leaves the fields alone and says so", async () => {
  const page = createPage();
  page.choose();
  await page.respond(0, {
    wasa_agency: "",
    wasa_date: "",
    wasa_valid_until: "",
  });
  assert.equal(page.auditDate.value, "");
  assert.match(page.status.textContent, /Nothing could be read/);
});

test("a failed reading offers a retry that asks again", async () => {
  const page = createPage();
  page.choose();
  await page.respond(0, {}, 503);
  assert.match(page.status.textContent, /could not be read/);
  assert.equal(page.retry.hidden, false);
  page.retry.dispatchEvent(new Event("click"));
  assert.equal(page.requests.length, 2);
  await page.respond(1, read);
  assert.equal(page.auditDate.value, TODAY);
  assert.equal(page.retry.hidden, true);
});

test("a rate-limited reading asks for the details instead of retrying", async () => {
  const page = createPage();
  page.choose();
  await page.respond(0, {}, 429);
  assert.match(page.status.textContent, /Enter the details below/);
  assert.equal(page.retry.hidden, true);
});

test("a document that can never be read says so without offering a retry", async () => {
  const page = createPage();
  page.choose();
  await page.respond(
    0,
    { error: "This certificate is a scan with no text to read." },
    422,
  );
  assert.equal(
    page.status.textContent,
    "This certificate is a scan with no text to read.",
  );
  assert.equal(page.retry.hidden, true);
  assert.equal(page.auditDate.value, "");
});

test("a reply for a file that was replaced is discarded", async () => {
  const page = createPage();
  page.choose("first.pdf");
  page.choose("second.pdf");
  assert.equal(page.requests.length, 2);
  await page.respond(0, read, 200, "first.pdf");
  assert.equal(page.auditDate.value, "");
  await page.respond(
    1,
    { ...read, wasa_date: "2026-02-02" },
    200,
    "second.pdf",
  );
  assert.equal(page.auditDate.value, "2026-02-02");
});

test("a reply describing another file is refused", async () => {
  const page = createPage();
  page.choose("first.pdf");
  await page.respond(0, read, 200, "somewhere-else.pdf");
  assert.equal(page.auditDate.value, "");
  assert.match(page.status.textContent, /could not be read/);
});

test("a reading that outlasts the timeout reports a failure once", async () => {
  const page = createPage();
  page.choose();
  page.tick();
  await page.flush();
  assert.match(page.status.textContent, /could not be read/);
});

test("clearing the file cancels the reading and the message", async () => {
  const page = createPage();
  page.choose();
  page.input.files = [];
  page.input.dispatchEvent(new Event("change"));
  assert.equal(page.status.hidden, true);
  await page.respond(0, read);
  assert.equal(page.auditDate.value, "");
});
