const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = readFileSync(join(__dirname, '../ohc_experience/static/js/row-link.js'), 'utf8');

// Just enough DOM for row links: a tree of elements, selector lists of tags and
// attributes, and the document's listeners, where the script listens.
class Element {
  constructor(tagName, attributes = {}, children = []) {
    this.tagName = tagName.toUpperCase();
    this.attributes = new Map(Object.entries(attributes));
    this.parentElement = null;
    this.children = [];
    this.clicks = 0;
    children.forEach(child => {
      child.parentElement = this;
      this.children.push(child);
    });
  }
  get href() { return this.attributes.get('href') ?? ''; }
  click() { this.clicks += 1; }
  * descendants() {
    for (const child of this.children) {
      yield child;
      yield* child.descendants();
    }
  }
  matches(selector) {
    return selector.split(',').some(part => {
      const [, tag, rest] = part.trim().match(/^(\w*)(.*)$/);
      return (!tag || this.tagName === tag.toUpperCase()) &&
        [...rest.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)].every(([, name, value]) =>
          (value === undefined ? this.attributes.has(name) : this.attributes.get(name) === value));
    });
  }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) if (node.matches(selector)) return node;
    return null;
  }
  querySelectorAll(selector) { return [...this.descendants()].filter(node => node.matches(selector)); }
}

const element = (tagName, attributes, children) => new Element(tagName, attributes, children);

function createPage() {
  const listeners = new Map();
  const page = { selection: '', opened: [] };
  vm.runInContext(script, vm.createContext({
    Element,
    document: {
      addEventListener(type, listener) { listeners.set(type, [...(listeners.get(type) || []), listener]); },
    },
    window: {
      getSelection: () => ({ toString: () => page.selection }),
      open: (...args) => { page.opened.push(args); },
    },
  }));
  page.dispatch = (type, target, init = {}) => {
    const event = {
      type, target, button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false,
      defaultPrevented: false,
      ...init,
      preventDefault() { this.defaultPrevented = true; },
    };
    (listeners.get(type) || []).forEach(listener => listener(event));
    return event;
  };
  return page;
}

// A queue row: the product's name is the row link, and the row also has its own
// Open link and an Assign button.
function queueRow() {
  const parts = {
    name: element('a', { href: '/assess/reviews/41/', 'data-row-link': '' }),
    reference: element('span'),
    status: element('span'),
    open: element('a', { href: '/assess/reviews/41/' }),
    assign: element('button', { type: 'submit' }),
    unlinked: element('span'),
  };
  element('table', {}, [element('tbody', {}, [
    element('tr', {}, [
      element('td', {}, [parts.name, parts.reference]),
      element('td', {}, [parts.status]),
      element('td', {}, [parts.open, parts.assign]),
    ]),
    element('tr', {}, [element('td', {}, [parts.unlinked])]),
  ])]);
  return parts;
}

test('a click anywhere in a row follows its link', () => {
  const page = createPage();
  const row = queueRow();
  page.dispatch('click', row.status);
  page.dispatch('click', row.reference);
  assert.equal(row.name.clicks, 2);
  assert.equal(row.open.clicks, 0);
  assert.deepEqual(page.opened, []);
});

test('a click on a control in the row is left to that control', () => {
  const page = createPage();
  const row = queueRow();
  for (const control of [row.name, row.open, row.assign]) page.dispatch('click', control);
  assert.equal(row.name.clicks, 0);
});

test('a click that ends a text selection only selects', () => {
  const page = createPage();
  const row = queueRow();
  page.selection = 'PRD-0041';
  page.dispatch('click', row.reference);
  page.dispatch('click', row.reference, { ctrlKey: true });
  assert.equal(row.name.clicks, 0);
  assert.deepEqual(page.opened, []);
});

test('Ctrl, Cmd and the middle button open the row in a new tab', () => {
  const page = createPage();
  const row = queueRow();
  page.dispatch('click', row.status, { ctrlKey: true });
  page.dispatch('click', row.status, { metaKey: true });
  page.dispatch('auxclick', row.status, { button: 1 });
  assert.equal(row.name.clicks, 0);
  assert.deepEqual(page.opened, Array(3).fill(['/assess/reviews/41/', '_blank', 'noopener']));
  // The middle press does not start autoscrolling over the row, as on a link.
  assert.ok(page.dispatch('mousedown', row.status, { button: 1 }).defaultPrevented);
  assert.ok(!page.dispatch('mousedown', row.status).defaultPrevented);
  assert.ok(!page.dispatch('mousedown', row.assign, { button: 1 }).defaultPrevented);
  // A right click opens the menu, not the row.
  page.dispatch('auxclick', row.status, { button: 2 });
  assert.equal(page.opened.length, 3);
});

test('Shift and Alt clicks leave the row alone', () => {
  const page = createPage();
  const row = queueRow();
  page.dispatch('click', row.status, { shiftKey: true });
  page.dispatch('click', row.status, { altKey: true });
  assert.equal(row.name.clicks, 0);
  assert.deepEqual(page.opened, []);
});

test('a row without a marked link, and a handled click, do nothing', () => {
  const page = createPage();
  const row = queueRow();
  page.dispatch('click', row.unlinked);
  page.dispatch('click', row.status, { defaultPrevented: true });
  page.dispatch('click', element('p'));
  assert.equal(row.name.clicks, 0);
});

test('a list inside a row belongs to the row, and a table inside a list item keeps its links', () => {
  const page = createPage();
  // A staff member's card: the access list is part of the card.
  const name = element('a', { href: '/staff/7/', 'data-row-link': '' });
  const area = element('span');
  element('ul', {}, [element('li', {}, [
    element('div', {}, [name]),
    element('ul', {}, [element('li', {}, [area])]),
  ])]);
  page.dispatch('click', area);
  assert.equal(name.clicks, 1);
  // A list item holding a table: only the table's row opens its link.
  const ticket = element('a', { href: '/support/T-9/', 'data-row-link': '' });
  const cell = element('td');
  const intro = element('p');
  element('ul', {}, [element('li', {}, [
    intro,
    element('table', {}, [element('tr', {}, [element('td', {}, [ticket]), cell])]),
  ])]);
  page.dispatch('click', intro);
  assert.equal(ticket.clicks, 0);
  page.dispatch('click', cell);
  assert.equal(ticket.clicks, 1);
});
