const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function createDecision({ decisionBlocked = false, approvalBlocked = false } = {}) {
  const listeners = new Map();
  const button = { disabled: false, textContent: '' };
  const label = { textContent: '' };
  const note = { required: false, dataset: { noteMinlength: '10' } };
  const marker = { hidden: true };
  const actions = ['approve', 'reject', 'query'].map(value => ({ value, checked: false }));
  const dataset = {};
  if (decisionBlocked) dataset.decisionBlocked = 'true';
  if (approvalBlocked) dataset.approvalBlocked = 'true';
  const form = {
    dataset,
    querySelector(selector) {
      if (selector === '[name="action"]:checked') return actions.find(action => action.checked) || null;
      return {
        '[data-decision-label]': label,
        '[data-decision-submit]': button,
        '[name="note"]': note,
        '[data-note-marker]': marker,
      }[selector] || null;
    },
    querySelectorAll() { return []; },
  };
  const document = {
    addEventListener(name, callback) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(callback);
    },
    querySelectorAll(selector) { return selector === '[data-decision-form]' ? [form] : []; },
    querySelector() { return null; },
  };
  document.body = document;
  const context = vm.createContext({
    document,
    window: { addEventListener() {}, location: { hash: '' } },
    navigator: {},
    Element: class {},
    queueMicrotask(callback) { callback(); },
    setTimeout() {},
  });
  vm.runInContext(readFileSync(join(__dirname, '../ohc_experience/static/js/project.js'), 'utf8'), context);
  const target = { closest: selector => (selector === '[data-decision-form]' || selector === 'form' ? form : null) };
  function fire(name, event = {}) {
    for (const callback of listeners.get(name) || []) callback(event);
  }
  return {
    button, label, marker,
    initialize: () => fire('DOMContentLoaded'),
    choose(value) {
      for (const action of actions) action.checked = action.value === value;
      fire('change', { target });
    },
  };
}

test('pending prerequisites hold approval and rejection but not a query', () => {
  const page = createDecision({ decisionBlocked: true });
  page.initialize();
  assert.equal(page.button.disabled, true);

  page.choose('reject');
  assert.equal(page.button.disabled, true);
  assert.equal(page.button.textContent, 'Reject request');

  // Nothing written yet, so a query has nothing to send; the prerequisites do
  // not hold it (see the query boxes below).
  page.choose('query');
  assert.equal(page.button.disabled, true);
  assert.equal(page.button.textContent, 'Send queries');
});

test('unresolved queries hold only approval', () => {
  const page = createDecision({ approvalBlocked: true });
  page.choose('approve');
  assert.equal(page.button.disabled, true);

  page.choose('reject');
  assert.equal(page.button.disabled, false);
});

test('a decision with nothing pending can be recorded', () => {
  const page = createDecision();
  page.choose('approve');
  assert.equal(page.button.disabled, false);
  assert.equal(page.button.textContent, 'Record approval');
});


// A form that lists reasons offers them in one select; the note is the reviewer's own words.
function createReasons() {
  const listeners = new Map();
  const button = { disabled: false, textContent: '' };
  const label = { textContent: '' };
  const note = { required: false, dataset: { noteMinlength: '10' } };
  const marker = { hidden: true };
  const hint = { textContent: '' };
  const actions = ['approve', 'reject', 'query'].map(value => ({ value, checked: false }));
  const options = ['', 'Website unreachable or not working', 'Wrong website address', 'Other'].map(value => ({
    value,
    dataset: value === 'Other' ? { noteRequired: '' } : {},
  }));
  const select = {
    options,
    selectedIndex: 0,
    get value() { return options[this.selectedIndex].value; },
  };
  const form = {
    dataset: {},
    querySelector(selector) {
      if (selector === '[name="action"]:checked') return actions.find(action => action.checked) || null;
      return ({
        '[data-decision-label]': label,
        '[data-decision-submit]': button,
        '[name="note"]': note,
        '[data-reason-hint]': hint,
        '[data-reason-select]': select,
        '[data-note-marker]': marker,
      })[selector] || null;
    },
    querySelectorAll() { return []; },
  };
  const document = {
    addEventListener(name, callback) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(callback);
    },
    querySelectorAll(selector) { return selector === '[data-decision-form]' ? [form] : []; },
    querySelector() { return null; },
  };
  document.body = document;
  const context = vm.createContext({
    document,
    window: { addEventListener() {}, location: { hash: '' } },
    navigator: {},
    Element: class {},
    queueMicrotask(callback) { callback(); },
    setTimeout() {},
  });
  vm.runInContext(readFileSync(join(__dirname, '../ohc_experience/static/js/project.js'), 'utf8'), context);
  const target = { closest: selector => (selector === '[data-decision-form]' || selector === 'form' ? form : null) };
  function fire(name) {
    for (const callback of listeners.get(name) || []) callback({ target });
  }
  return {
    button, note, hint, marker,
    choose(value) {
      for (const action of actions) action.checked = action.value === value;
      fire('change');
    },
    pick(value) {
      select.selectedIndex = options.findIndex(option => option.value === value);
      fire('change');
    },
  };
}

test('rejecting waits for a reason, which then needs no note', () => {
  const page = createReasons();
  page.choose('reject');
  assert.equal(page.button.disabled, true);
  assert.match(page.hint.textContent, /Choose a reason/);

  page.pick('Wrong website address');
  assert.equal(page.button.disabled, false);
  assert.equal(page.note.required, false);
  assert.match(page.hint.textContent, /sees this reason above your note/);
});

test('Other asks the reviewer for their own words', () => {
  const page = createReasons();
  page.choose('reject');
  page.pick('Other');
  assert.equal(page.button.disabled, false);
  assert.equal(page.note.required, true);
});

test('a form with no list to choose from takes the note alone', () => {
  const page = createDecision();
  page.choose('reject');
  assert.equal(page.button.disabled, false);
  assert.equal(page.marker.hidden, false);
});

test('the note is marked required for as long as it is', () => {
  const page = createReasons();
  page.choose('approve');
  assert.equal(page.marker.hidden, false);

  page.choose('reject');
  assert.equal(page.note.required, false);
  assert.equal(page.marker.hidden, true);
  page.pick('Wrong website address');
  assert.equal(page.marker.hidden, true);
  page.pick('Other');
  assert.equal(page.marker.hidden, false);
  page.pick('Wrong website address');
  assert.equal(page.marker.hidden, true);

  // A query's words are in its boxes, so the note it hides is not required.
  page.choose('query');
  assert.equal(page.note.required, false);
  assert.equal(page.marker.hidden, true);
});

test('the note is named for the action and stays marked as required', () => {
  const page = createDecision();
  page.choose('query');
  assert.equal(page.marker.hidden, true);
  page.choose('reject');
  assert.equal(page.label.textContent, 'Note for the integrator');
  assert.equal(page.marker.hidden, false);
});


// Query beside a submitted field opens a query box under it; the decision
// form lists the open boxes and sends every query at once.
function createQuestions({ decisionBlocked = false } = {}) {
  const listeners = new Map();
  const button = { disabled: false, textContent: '' };
  const label = { textContent: '' };
  const note = { required: true, dataset: { noteMinlength: '10' } };
  // Radios in one group: checking one unchecks the rest.
  let checkedAction = 'approve';
  const actions = ['approve', 'reject', 'query'].map(value => ({
    value,
    disabled: false,
    focus() {},
    get checked() { return checkedAction === value; },
    set checked(on) { if (on) checkedAction = value; else if (checkedAction === value) checkedAction = null; },
  }));
  const queryControls = { hidden: true };
  const noteControls = { hidden: false };
  const count = { hidden: true, textContent: '' };
  const aside = { hidden: true, textContent: '' };
  const list = {
    hidden: true,
    entries: [],
    replaceChildren(...entries) { this.entries = entries; },
    querySelector(selector) { return selector === '[data-query-remove]' ? this.entries[0]?.remove || null : null; },
  };
  // One cloned list entry: a link back to the box, its excerpt, and Remove.
  const template = {
    content: {
      firstElementChild: {
        cloneNode() {
          const link = { textContent: '', href: '' };
          const excerpt = { textContent: '' };
          const removeLabel = { textContent: '' };
          const remove = {
            dataset: {},
            focused: false,
            focus() { this.focused = true; },
            querySelector: selector => (selector === '[data-query-remove-label]' ? removeLabel : null),
            closest: selector => (selector === '[data-query-remove]' ? remove : null),
          };
          return {
            link, excerpt, remove, removeLabel,
            querySelector: selector => ({
              '[data-query-link]': link,
              '[data-query-excerpt]': excerpt,
              '[data-query-remove]': remove,
            })[selector] || null,
          };
        },
      },
    },
  };
  const dataset = {};
  if (decisionBlocked) dataset.decisionBlocked = 'true';
  const form = {
    id: 'decision-form',
    dataset,
    matches: selector => selector === '[data-decision-form]',
    querySelector(selector) {
      if (selector === '[name="action"]:checked') return actions.find(action => action.checked) || null;
      if (selector === '[name="action"][value="query"]') return actions[2];
      if (selector === '[data-query-list] [data-query-remove]') return list.entries[0]?.remove || null;
      return {
        '[data-decision-label]': label,
        '[data-decision-submit]': button,
        '[name="note"]': note,
        '[data-query-count]': count,
        '[data-query-list]': list,
        'template[data-query-list-item]': template,
        '[data-query-aside]': aside,
      }[selector] || null;
    },
    querySelectorAll(selector) {
      if (selector === '[data-query-controls]') return [queryControls];
      if (selector === '[data-note-controls]') return [noteControls];
      return [];
    },
  };
  // A question box under each field, its Ask link beside the field, and the
  // row that is tinted while the box is open. The whole form's box is its own row.
  const fields = [['form', 'Whole form'], ['report_reference', 'Report Reference'], ['score', 'Score']];
  const boxes = {};
  const links = {};
  for (const [key, fieldLabel] of fields) {
    const row = { selected: false, toggleAttribute(name, on) { if (name === 'data-query-selected') this.selected = on; } };
    const cell = { hidden: false };
    const box = { id: `draft-decision-form-${key}`, hidden: true, dataset: { queryDraft: key, queryLabel: fieldLabel }, row };
    const question = {
      value: '', disabled: true, focused: false, form, box,
      focus() { this.focused = true; },
      closest: selector => (selector === '[data-query-draft]' ? box : null),
    };
    const removeButton = { dataset: {}, closest: selector => ({ '[data-query-remove]': removeButton, '[data-query-draft]': box })[selector] || null };
    Object.assign(box, {
      question, removeButton,
      querySelector: selector => (selector === 'textarea' ? question : null),
      closest: selector => (selector === '[data-query-row]' ? (key === 'form' ? box : row) : null),
      toggleAttribute: (name, on) => row.toggleAttribute(name, on),
    });
    const link = {
      dataset: { queryField: key, queryForm: form.id },
      cell,
      focused: false,
      focus() { this.focused = true; },
      closest: selector => ({ '[data-query-button]': cell, '[data-query-field]': link })[selector] || null,
    };
    boxes[key] = box;
    links[key] = link;
  }
  const document = {
    addEventListener(name, callback) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(callback);
    },
    getElementById: id => (id === form.id ? form : Object.values(boxes).find(box => box.id === id) || null),
    querySelectorAll(selector) {
      if (selector === '[data-decision-form]') return [form];
      if (selector === '[data-query-for="decision-form"] [data-query-draft]') return Object.values(boxes);
      return [];
    },
    querySelector(selector) {
      const key = selector.match(/^\[data-query-form="decision-form"\]\[data-query-field="([^"]+)"\]$/)?.[1];
      return key ? links[key] || null : null;
    },
  };
  document.body = document;
  const context = vm.createContext({
    document,
    window: { addEventListener() {}, location: { hash: '' } },
    navigator: {},
    Element: class {},
    queueMicrotask(callback) { callback(); },
    setTimeout() {},
  });
  vm.runInContext(readFileSync(join(__dirname, '../ohc_experience/static/js/project.js'), 'utf8'), context);
  const radio = { closest: selector => (selector === '[data-decision-form]' || selector === 'form' ? form : null) };
  function fire(name, event = {}) {
    for (const callback of listeners.get(name) || []) callback(event);
  }
  function click(target) {
    const event = { target, prevented: false, preventDefault() { this.prevented = true; } };
    fire('click', event);
    return event;
  }
  return {
    actions, button, note, count, list, aside, queryControls, noteControls, boxes, links,
    initialize: () => fire('DOMContentLoaded'),
    ask: key => click({ closest: selector => (selector === '[data-query-field]' ? links[key] || { dataset: { queryField: key, queryForm: form.id } } : null) }),
    remove: key => click(boxes[key].removeButton),
    removeFromList: index => click(list.entries[index].remove),
    type(key, text) {
      boxes[key].question.value = text;
      fire('input', { target: boxes[key].question });
    },
    choose(value) {
      for (const action of actions) action.checked = action.value === value;
      fire('change', { target: radio });
    },
  };
}

test('Query opens its field\'s query box and chooses Query, without leaving the page', () => {
  const page = createQuestions();
  page.initialize();
  const click = page.ask('score');

  assert.equal(click.prevented, true);
  assert.equal(page.actions.find(action => action.checked).value, 'query');
  const { score } = page.boxes;
  assert.equal(score.hidden, false);
  assert.equal(score.question.disabled, false);
  assert.equal(score.question.focused, true);
  assert.equal(score.row.selected, true);
  // The open box replaces its Query button.
  assert.equal(page.links.score.cell.hidden, true);
  assert.equal(page.queryControls.hidden, false);
  assert.equal(page.noteControls.hidden, true);
  assert.equal(page.note.required, false);
  assert.equal(page.count.textContent, '1 query to send');
  assert.equal(page.list.entries.length, 1);
  assert.equal(page.list.entries[0].link.textContent, 'Score');
  assert.equal(page.list.entries[0].link.href, '#draft-decision-form-score');
  assert.equal(page.list.entries[0].excerpt.textContent, 'Nothing written yet.');
  assert.equal(page.button.textContent, 'Send 1 query');
  assert.equal(page.button.disabled, false);
});

test('every open query goes in one send, and the list shows what each says', () => {
  const page = createQuestions();
  page.ask('score');
  page.ask('form');
  page.type('score', 'Confirm this score against the report.');

  assert.equal(page.count.textContent, '2 queries to send');
  assert.equal(page.button.textContent, 'Send 2 queries');
  const excerpts = Object.fromEntries(page.list.entries.map(entry => [entry.link.textContent, entry.excerpt.textContent]));
  assert.equal(excerpts.Score, 'Confirm this score against the report.');
  assert.equal(excerpts['Whole form'], 'Nothing written yet.');
  assert.equal(page.list.entries.find(entry => entry.link.textContent === 'Whole form').removeLabel.textContent,
    'Remove the query about the whole form');
});

test('Remove clears a query, closes its box and hands focus back to its Query button', () => {
  const page = createQuestions();
  page.ask('score');
  page.type('score', 'Confirm this score against the report.');
  page.remove('score');

  const { score } = page.boxes;
  assert.equal(score.hidden, true);
  assert.equal(score.question.value, '');
  assert.equal(score.question.disabled, true);
  assert.equal(score.row.selected, false);
  assert.equal(page.links.score.cell.hidden, false);
  assert.equal(page.links.score.focused, true);
  assert.equal(page.count.hidden, true);
  assert.equal(page.button.textContent, 'Send queries');
  assert.equal(page.button.disabled, true);
});

test('removing from the decision form\'s list keeps focus in the list', () => {
  const page = createQuestions();
  page.ask('score');
  page.ask('report_reference');
  page.removeFromList(0);

  assert.equal(page.list.entries.length, 1);
  assert.equal(page.list.entries[0].remove.focused, true);
});

test('another decision sets the queries aside, neither sending nor blocking it', () => {
  const page = createQuestions();
  page.ask('score');
  page.choose('approve');

  const { score } = page.boxes;
  assert.equal(score.hidden, false);
  assert.equal(score.question.disabled, true);
  assert.equal(score.row.selected, false);
  assert.equal(page.aside.hidden, false);
  assert.equal(page.aside.textContent, 'Your query is not sent with this decision. Choose Query to send it.');
  assert.equal(page.note.required, true);
  assert.equal(page.button.textContent, 'Record approval');

  page.choose('query');
  assert.equal(score.question.disabled, false);
  assert.equal(page.aside.hidden, true);
});

test('pending prerequisites hold no query with something to send', () => {
  const page = createQuestions({ decisionBlocked: true });
  page.ask('form');
  assert.equal(page.button.disabled, false);
  assert.equal(page.button.textContent, 'Send 1 query');
});

test('Query for a field the page has no box for falls back to its link', () => {
  const page = createQuestions();
  const click = page.ask('retired_field');
  assert.equal(click.prevented, false);
  assert.equal(page.actions.find(action => action.checked).value, 'approve');
});
