const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = readFileSync(join(__dirname, '../ohc_experience/static/js/portal-ux.js'), 'utf8');

// Just enough DOM for the picker: elements whose dataset is their data-*
// attributes, and selectors of one compound each, with :checked, :not() and :has().
class Element {
  constructor(tagName, attributes = {}, children = []) {
    this.tagName = tagName.toUpperCase();
    this.attributes = new Map();
    this.children = [];
    this.parentElement = null;
    this.checked = false;
    this.textContent = '';
    const attribute = name => `data-${name.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`)}`;
    this.dataset = new Proxy({}, {
      get: (_, name) => this.getAttribute(attribute(name)) ?? undefined,
      set: (_, name, value) => {
        this.setAttribute(attribute(name), value);
        return true;
      },
      has: (_, name) => this.hasAttribute(attribute(name)),
    });
    for (const [name, value] of Object.entries(attributes)) this.setAttribute(name, value);
    children.forEach(child => this.append(child));
  }
  get id() { return this.getAttribute('id') || ''; }
  get hidden() { return this.hasAttribute('hidden'); }
  set hidden(value) { this.toggleAttribute('hidden', value); }
  get disabled() { return this.hasAttribute('disabled'); }
  set disabled(value) { this.toggleAttribute('disabled', value); }
  get form() { return this.closest('form'); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  hasAttribute(name) { return this.attributes.has(name); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  toggleAttribute(name, force = !this.hasAttribute(name)) {
    if (force) this.setAttribute(name, '');
    else this.removeAttribute(name);
    return force;
  }
  append(child) {
    child.parentElement = this;
    this.children.push(child);
  }
  * descendants() {
    for (const child of this.children) {
      yield child;
      yield* child.descendants();
    }
  }
  matches(selector) {
    return selector.split(',').some(compound => this.matchesCompound(compound.trim()));
  }
  matchesCompound(selector) {
    const pseudo = [];
    const compound = selector.replace(/:(not|has)\(([^()]*)\)/g, (_, kind, inner) => {
      pseudo.push([kind, inner]);
      return '';
    });
    const tag = compound.match(/^[a-z]*/)[0];
    const classes = [...compound.matchAll(/\.([\w-]+)/g)].map(match => match[1]);
    const attributes = [...compound.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)];
    const names = (this.getAttribute('class') || '').split(' ');
    return (!tag || this.tagName === tag.toUpperCase())
      && classes.every(name => names.includes(name))
      && attributes.every(([, name, value]) => (value === undefined ? this.hasAttribute(name) : this.getAttribute(name) === value))
      && (!compound.includes(':checked') || this.checked)
      && pseudo.every(([kind, inner]) => (kind === 'not' ? !this.matches(inner) : this.querySelectorAll(inner).length > 0));
  }
  querySelectorAll(selector) { return [...this.descendants()].filter(node => node.matches(selector)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) if (node.matches(selector)) return node;
    return null;
  }
}

// The ABDM program's tracks, each milestone with the milestones that open it,
// any one of which will do unless it needs them all. ABDM and PHR rule each
// other out.
const TRACKS = [
  { code: 'ABDM', excludes: 'PHR', milestones: { m1: '', m2: 'm1', m3: 'm1', m4: 'm1' } },
  { code: 'PHR', excludes: 'ABDM', milestones: { p1: '', p2: 'p1', p3: 'p1', p4: 'p1 p2 p3' }, requiresAll: ['p4'] },
  { code: 'UHI', milestones: { uhi1: 'm1 p1' }, standsAlone: true },
  { code: 'NHCX', milestones: { nhcx1: 'm1 p1' } },
];
// A few solution types, with the milestones each one requires. Other requires none.
const SOLUTION_TYPES = {
  other: ['Other', []],
  hmis: ['HMIS', ['m1', 'm2', 'm3', 'm4']],
  phr: ['PHR', ['p1', 'p2', 'p3']],
  health_locker: ['Health Locker', ['p1', 'p2', 'p3', 'p4']],
  insurance: ['Insurance', ['m1', 'm3']],
};

// The registration form as product_solution_type_picker.html and
// product_milestone_picker.html draw it, with `selected` already saved.
function createPage({ selected = [] } = {}) {
  const form = new Element('form');
  const types = {};
  for (const [value, [label]] of Object.entries(SOLUTION_TYPES)) {
    types[value] = new Element('input', { type: 'radio', name: 'solution_type', 'data-solution-type': '' });
    Object.assign(types[value], { value, labels: [{ textContent: `\n  ${label}\n` }] });
    form.append(types[value]);
  }
  const status = new Element('p', { role: 'status', 'data-milestone-status': '' });
  form.append(status);
  const boxes = {};
  const notes = {};
  const fixedNotes = {};
  // The server greys out the track the saved selection rules out.
  const chosen = TRACKS.find(track => Object.keys(track.milestones).some(key => selected.includes(key)));
  for (const track of TRACKS) {
    const row = new Element('div', { 'data-track': track.code });
    if (track.excludes) row.setAttribute('data-track-excludes', track.excludes);
    notes[track.code] = new Element('span', { 'data-track-blocked': '' });
    notes[track.code].hidden = !(chosen?.excludes === track.code);
    row.append(notes[track.code]);
    if (track.excludes) {
      fixedNotes[track.code] = new Element('span', { 'data-track-fixed': '', hidden: '' });
      row.append(fixedNotes[track.code]);
    }
    for (const [key, requires] of Object.entries(track.milestones)) {
      const id = `milestone-${track.code.toLowerCase()}${key}`;
      const box = new Element('input', {
        type: 'checkbox', id, name: 'applied_milestones', 'data-milestone-key': key, 'data-milestone-requires': requires,
      });
      if (track.standsAlone) box.setAttribute('data-milestone-stands-alone', '');
      if (track.requiresAll?.includes(key)) box.setAttribute('data-milestone-requires-all', '');
      box.disabled = chosen?.excludes === track.code;
      box.checked = selected.includes(key);
      const requiredFor = Object.keys(SOLUTION_TYPES).filter(value => SOLUTION_TYPES[value][1].includes(key));
      row.append(box);
      if (requiredFor.length) box.setAttribute('data-required-for', requiredFor.join(' '));
      boxes[key] = box;
    }
    form.append(row);
  }
  const body = new Element('body', {}, [form]);
  const listeners = new Map();
  const document = {
    body,
    activeElement: body,
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(listener);
    },
    querySelectorAll: selector => body.querySelectorAll(selector),
    querySelector: selector => body.querySelector(selector),
    getElementById: id => body.querySelector(`[id="${id}"]`),
  };
  vm.runInNewContext(script, {
    document,
    window: { addEventListener() {}, location: { hash: '' } },
    navigator: {},
    CSS: { escape: value => value },
  });
  const fire = (type, target) => {
    for (const listener of listeners.get(type) || []) listener({ type, target });
  };
  // A click on a box: the browser flips it, then says it changed. A click on a
  // radio checks it and clears the rest of its group, but says only it changed.
  const click = input => {
    assert.equal(input.disabled, false, `${input.id} cannot be clicked while disabled`);
    if (input.getAttribute('type') === 'radio') {
      if (input.checked) return;
      form.querySelectorAll(`[name="${input.getAttribute('name')}"]`).forEach(radio => { radio.checked = false; });
      input.checked = true;
    } else {
      input.checked = !input.checked;
    }
    fire('change', input);
  };
  return { boxes, types, notes, fixedNotes, status, click, initialize: () => fire('DOMContentLoaded', document) };
}

const enabled = (page, ...keys) => keys.filter(key => !page.boxes[key].disabled);
const checked = (page, ...keys) => keys.filter(key => page.boxes[key].checked);
const ALL = ['m1', 'm2', 'm3', 'm4', 'uhi1', 'nhcx1', 'p1', 'p2', 'p3', 'p4'];

test('a milestone opens once one it builds on is ticked, and closes with it', () => {
  const page = createPage();
  page.initialize();
  // UHI is applied for on its own; NHCX always rides on a track.
  assert.deepEqual(enabled(page, ...ALL), ['m1', 'uhi1', 'p1']);

  page.click(page.boxes.m1);
  assert.deepEqual(enabled(page, ...ALL), ['m1', 'm2', 'm3', 'm4', 'uhi1', 'nhcx1']);
  page.click(page.boxes.m2);
  page.click(page.boxes.nhcx1);
  page.click(page.boxes.uhi1);

  page.click(page.boxes.m1);
  assert.deepEqual(checked(page, ...ALL), ['uhi1']);
  assert.deepEqual(enabled(page, ...ALL), ['m1', 'uhi1', 'p1']);
});

test('NHCX opens on either identity milestone', () => {
  const page = createPage();
  page.initialize();

  page.click(page.boxes.p1);
  assert.deepEqual(enabled(page, 'nhcx1', 'p2', 'p3'), ['nhcx1', 'p2', 'p3']);
  page.click(page.boxes.nhcx1);
  page.click(page.boxes.p1);
  assert.deepEqual(checked(page, 'nhcx1'), []);
  assert.deepEqual(enabled(page, 'nhcx1'), []);
});

test('ticking ABDM or PHR rules the other track out until it is cleared again', () => {
  for (const [ticked, ruledOut] of [['m1', 'PHR'], ['p1', 'ABDM']]) {
    const page = createPage();
    const own = ruledOut === 'PHR' ? 'ABDM' : 'PHR';
    const other = ruledOut === 'PHR' ? ['p1', 'p2', 'p3', 'p4'] : ['m1', 'm2', 'm3', 'm4'];
    page.initialize();
    assert.equal(page.notes[ruledOut].hidden, true);

    page.click(page.boxes[ticked]);
    assert.deepEqual(enabled(page, ...other), []);
    assert.equal(page.notes[ruledOut].hidden, false);
    assert.equal(page.notes[own].hidden, true);

    page.click(page.boxes[ticked]);
    assert.deepEqual(enabled(page, ...other), [other[0]]);
    assert.equal(page.notes[ruledOut].hidden, true);
  }
});

test('a saved track keeps the other one out as the page loads', () => {
  const page = createPage({ selected: ['p1', 'p2'] });
  page.initialize();
  assert.deepEqual(enabled(page, 'm1', 'm2'), []);
  assert.equal(page.notes.ABDM.hidden, false);

  page.click(page.boxes.p1);
  assert.deepEqual(checked(page, 'p1', 'p2'), []);
  assert.deepEqual(enabled(page, 'm1', 'p1'), ['m1', 'p1']);
  assert.equal(page.notes.ABDM.hidden, true);
});

test('a solution type fixes the ABDM or PHR milestones it requires', () => {
  const page = createPage();
  page.initialize();

  page.click(page.types.hmis);
  assert.deepEqual(checked(page, ...ALL), ['m1', 'm2', 'm3', 'm4']);
  assert.deepEqual(enabled(page, ...ALL), ['uhi1', 'nhcx1']);
  assert.equal(page.fixedNotes.ABDM.hidden, false);
  assert.equal(page.fixedNotes.ABDM.textContent, '· Set by the HMIS solution type.');
  assert.equal(page.fixedNotes.PHR.hidden, true);
  assert.equal(page.status.textContent, 'M1, M2, M3 and M4 set by the HMIS solution type.');

  // Insurance requires only M1 and M3; M2 and M4 cannot be added back.
  page.click(page.types.insurance);
  assert.deepEqual(checked(page, ...ALL), ['m1', 'm3']);
  assert.deepEqual(enabled(page, 'm2', 'm4'), []);

  // PHR is on the other track: ABDM clears, so PHR can open.
  page.click(page.types.phr);
  assert.deepEqual(checked(page, ...ALL), ['p1', 'p2', 'p3']);
  assert.deepEqual(enabled(page, ...ALL), ['uhi1', 'nhcx1']);
  assert.equal(page.notes.ABDM.hidden, false);
  assert.equal(page.fixedNotes.PHR.textContent, '· Set by the PHR solution type.');
  assert.equal(page.fixedNotes.ABDM.hidden, true);
});

test('Other hands the milestones back, ticked as they were', () => {
  const page = createPage();
  page.initialize();
  page.click(page.types.health_locker);
  page.click(page.boxes.uhi1);

  page.click(page.types.other);
  assert.deepEqual(checked(page, ...ALL), ['uhi1', 'p1', 'p2', 'p3', 'p4']);
  assert.deepEqual(enabled(page, 'p1', 'p2', 'p3', 'p4', 'm1'), ['p1', 'p2', 'p3', 'p4']);
  assert.equal(page.fixedNotes.PHR.hidden, true);
  assert.equal(page.status.textContent, '');

  page.click(page.boxes.p4);
  page.click(page.boxes.p1);
  assert.deepEqual(checked(page, ...ALL), ['uhi1']);
  assert.deepEqual(enabled(page, 'm1', 'p1'), ['m1', 'p1']);
});

test('a box the server locked stays locked as the page loads', () => {
  const page = createPage({ selected: ['m1', 'm3'] });
  for (const key of ['m1', 'm2', 'm3', 'm4', 'p1', 'p2', 'p3', 'p4']) {
    page.boxes[key].setAttribute('data-milestone-locked', '');
  }
  page.initialize();

  assert.deepEqual(checked(page, ...ALL), ['m1', 'm3']);
  assert.deepEqual(enabled(page, ...ALL), ['uhi1', 'nhcx1']);
});

test('P4 opens only once P1, P2 and P3 are all ticked, and closes with any of them', () => {
  const page = createPage();
  page.initialize();

  page.click(page.boxes.p1);
  assert.deepEqual(enabled(page, 'p4'), []);
  page.click(page.boxes.p2);
  assert.deepEqual(enabled(page, 'p4'), []);
  page.click(page.boxes.p3);
  assert.deepEqual(enabled(page, 'p4'), ['p4']);
  page.click(page.boxes.p4);

  page.click(page.boxes.p2);
  assert.deepEqual(checked(page, 'p1', 'p2', 'p3', 'p4'), ['p1', 'p3']);
  assert.deepEqual(enabled(page, 'p4'), []);
});
