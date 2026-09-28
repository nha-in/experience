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
// any one of which will do. ABDM and PHR rule each other out.
const TRACKS = [
  { code: 'ABDM', excludes: 'PHR', milestones: { m1: '', m2: 'm1', m3: 'm1', m4: 'm1' } },
  { code: 'PHR', excludes: 'ABDM', milestones: { p1: '', p2: 'p1', p3: 'p1', p4: 'p1' } },
  { code: 'UHI', milestones: { uhi1: 'm1 p1' }, standsAlone: true },
  { code: 'NHCX', milestones: { nhcx1: 'm1 p1' } },
];
// A few solution types, with the milestones each one requires.
const SOLUTION_TYPES = {
  hmis: ['HMIS', ['m1', 'm2', 'm3', 'm4']],
  phr: ['PHR', ['p1', 'p2', 'p3']],
  insurance: ['Insurance', ['m1', 'm3']],
};

// The registration form as product_solution_type_picker.html and
// product_milestone_picker.html draw it, with `selected` already saved.
function createPage({ selected = [] } = {}) {
  const form = new Element('form');
  const types = {};
  for (const [value, [label]] of Object.entries(SOLUTION_TYPES)) {
    types[value] = new Element('input', { type: 'checkbox', name: 'solution_type', 'data-solution-type': '' });
    Object.assign(types[value], { value, labels: [{ textContent: `\n  ${label}\n` }] });
    form.append(types[value]);
  }
  const status = new Element('p', { role: 'status', 'data-milestone-status': '' });
  form.append(status);
  const boxes = {};
  const notes = {};
  const warnings = {};
  // The server greys out the track the saved selection rules out.
  const chosen = TRACKS.find(track => Object.keys(track.milestones).some(key => selected.includes(key)));
  for (const track of TRACKS) {
    const row = new Element('div', { 'data-track': track.code });
    if (track.excludes) row.setAttribute('data-track-excludes', track.excludes);
    notes[track.code] = new Element('span', { 'data-track-blocked': '' });
    notes[track.code].hidden = !(chosen?.excludes === track.code);
    row.append(notes[track.code]);
    for (const [key, requires] of Object.entries(track.milestones)) {
      const id = `milestone-${track.code.toLowerCase()}${key}`;
      const box = new Element('input', {
        type: 'checkbox', id, name: 'applied_milestones', 'data-milestone-key': key, 'data-milestone-requires': requires,
      });
      if (track.standsAlone) box.setAttribute('data-milestone-stands-alone', '');
      box.disabled = chosen?.excludes === track.code;
      box.checked = selected.includes(key);
      const requiredFor = Object.keys(SOLUTION_TYPES).filter(value => SOLUTION_TYPES[value][1].includes(key));
      row.append(box);
      if (requiredFor.length) {
        box.setAttribute('data-required-for', requiredFor.join(' '));
        warnings[key] = new Element('p', { id: `${id}-required`, 'data-milestone-code': key.toUpperCase(), hidden: '' });
        row.append(warnings[key]);
      }
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
  // A click on a box: the browser flips it, then says it changed.
  const click = input => {
    assert.equal(input.disabled, false, `${input.id} cannot be clicked while disabled`);
    input.checked = !input.checked;
    fire('change', input);
  };
  return { boxes, types, notes, warnings, status, click, initialize: () => fire('DOMContentLoaded', document) };
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
  assert.deepEqual(enabled(page, 'nhcx1', 'p2', 'p3', 'p4'), ['nhcx1', 'p2', 'p3', 'p4']);
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

test('a solution type ticks what it requires, and takes back only what nothing else keeps', () => {
  const page = createPage();
  page.initialize();

  page.click(page.types.hmis);
  assert.deepEqual(checked(page, ...ALL), ['m1', 'm2', 'm3', 'm4']);
  page.click(page.types.insurance);
  // The integrator unticks M4 and ticks it again: it is theirs now.
  page.click(page.boxes.m4);
  page.click(page.boxes.m4);

  page.click(page.types.hmis);
  // Insurance still requires M1 and M3, and M4 builds on M1.
  assert.deepEqual(checked(page, ...ALL), ['m1', 'm3', 'm4']);

  // M4 is the integrator's, and keeps the M1 it builds on.
  page.click(page.types.insurance);
  assert.deepEqual(checked(page, ...ALL), ['m1', 'm4']);
});

test('a required milestone left unticked names the solution types that need it', () => {
  const page = createPage();
  page.initialize();
  page.click(page.types.hmis);
  page.click(page.types.insurance);
  assert.deepEqual(Object.values(page.warnings).filter(warning => !warning.hidden), []);

  page.click(page.boxes.m3);
  assert.equal(page.warnings.m3.hidden, false);
  assert.equal(page.warnings.m3.textContent, 'Required for the HMIS and Insurance solution types.');
  assert.equal(page.status.textContent, 'M3: Required for the HMIS and Insurance solution types.');

  // Closing M2 with M1 says so for each, once.
  page.click(page.boxes.m1);
  assert.equal(page.warnings.m2.textContent, 'Required for the HMIS solution type.');
  assert.equal(
    page.status.textContent,
    'M1: Required for the HMIS and Insurance solution types. M2: Required for the HMIS solution type. '
      + 'M4: Required for the HMIS solution type.',
  );

  page.click(page.types.hmis);
  page.click(page.types.insurance);
  assert.deepEqual(Object.values(page.warnings).filter(warning => !warning.hidden), []);
});
