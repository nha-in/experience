const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Query beside a submitted field opens a query box under it; the query form
// lists the open boxes and sends every query at once.
function createQuestions() {
  const listeners = new Map();
  const button = { disabled: false, textContent: '' };
  const count = { hidden: true, textContent: '' };
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
  const form = {
    id: 'query-form',
    dataset: {},
    matches: selector => selector === '[data-query-send-form]',
    querySelector(selector) {
      if (selector === '[data-query-list] [data-query-remove]') return list.entries[0]?.remove || null;
      return {
        '[data-query-send]': button,
        '[data-query-count]': count,
        '[data-query-list]': list,
        'template[data-query-list-item]': template,
      }[selector] || null;
    },
    querySelectorAll() { return []; },
  };
  // A question box under each field, its Ask link beside the field, and the
  // row that is tinted while the box is open. The whole form's box is its own row.
  const fields = [['form', 'Whole form'], ['report_reference', 'Report Reference'], ['score', 'Score']];
  const boxes = {};
  const links = {};
  for (const [key, fieldLabel] of fields) {
    const row = { selected: false, toggleAttribute(name, on) { if (name === 'data-query-selected') this.selected = on; } };
    const cell = { hidden: false };
    const box = { id: `draft-query-form-${key}`, hidden: true, dataset: { queryDraft: key, queryLabel: fieldLabel }, row };
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
      if (selector === '[data-query-send-form]') return [form];
      if (selector === '[data-query-for="query-form"] [data-query-draft]') return Object.values(boxes);
      return [];
    },
    querySelector(selector) {
      const key = selector.match(/^\[data-query-form="query-form"\]\[data-query-field="([^"]+)"\]$/)?.[1];
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
  function fire(name, event = {}) {
    for (const callback of listeners.get(name) || []) callback(event);
  }
  function click(target) {
    const event = { target, prevented: false, preventDefault() { this.prevented = true; } };
    fire('click', event);
    return event;
  }
  return {
    button, count, list, boxes, links,
    initialize: () => fire('DOMContentLoaded'),
    ask: key => click({ closest: selector => (selector === '[data-query-field]' ? links[key] || { dataset: { queryField: key, queryForm: form.id } } : null) }),
    remove: key => click(boxes[key].removeButton),
    removeFromList: index => click(list.entries[index].remove),
    type(key, text) {
      boxes[key].question.value = text;
      fire('input', { target: boxes[key].question });
    },
  };
}

test('with nothing written, a query has nothing to send', () => {
  const page = createQuestions();
  page.initialize();
  assert.equal(page.button.disabled, true);
  assert.equal(page.button.textContent, 'Send queries');
});

test('Query opens its field\'s query box, without leaving the page', () => {
  const page = createQuestions();
  page.initialize();
  const click = page.ask('score');

  assert.equal(click.prevented, true);
  const { score } = page.boxes;
  assert.equal(score.hidden, false);
  assert.equal(score.question.disabled, false);
  assert.equal(score.question.focused, true);
  assert.equal(score.row.selected, true);
  // The open box replaces its Query button.
  assert.equal(page.links.score.cell.hidden, true);
  assert.equal(page.count.textContent, '1 query to send');
  assert.equal(page.list.entries.length, 1);
  assert.equal(page.list.entries[0].link.textContent, 'Score');
  assert.equal(page.list.entries[0].link.href, '#draft-query-form-score');
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

test('removing from the query form\'s list keeps focus in the list', () => {
  const page = createQuestions();
  page.ask('score');
  page.ask('report_reference');
  page.removeFromList(0);

  assert.equal(page.list.entries.length, 1);
  assert.equal(page.list.entries[0].remove.focused, true);
});

test('removing the last query from the list hands focus back to its Query button', () => {
  const page = createQuestions();
  page.ask('score');
  page.removeFromList(0);

  assert.equal(page.list.entries.length, 0);
  assert.equal(page.links.score.focused, true);
});

test('Query for a field the page has no box for falls back to its link', () => {
  const page = createQuestions();
  const click = page.ask('retired_field');
  assert.equal(click.prevented, false);
});
