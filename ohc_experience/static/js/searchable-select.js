// Every careui dropdown is searchable: people type to filter the options of a
// single-choice select.ui-select, and the select itself is never replaced.
//
// The select stays in the form as the source of truth. It posts the value, holds
// the validation state, and is what the other scripts read and change: unsaved
// changes and readiness tracking, the PIN code lookup, the WASA product choice.
// This script draws a combobox over the select and writes each choice back with
// the input and change events a person using the select would fire. Without
// JavaScript it is simply a select.
//
// There is nothing to opt into: render the field with {% ui_field %}, or give a
// hand-written select the ui-select class. data-native-select keeps a plain
// select. The keyboard and screen reader behaviour is the WAI-ARIA editable
// combobox with list autocomplete.
(() => {
  const selector = 'select.ui-select:not([multiple]):not([data-native-select])';
  const partAttribute = 'data-searchable-select-part';
  const pageSize = 10;
  const gap = 4;
  const enhanced = new WeakSet();
  let sequence = 0;

  // Case, accents and spacing are ignored, so "tuv sud" finds "M/s TÜV-SÜD".
  const fold = text => text.normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase().replace(/\s+/g, ' ').trim();
  const startsWord = (text, index) => index === 0 || !/[\p{L}\p{N}]/u.test(text[index - 1]);

  // Every typed word has to appear in the label; -1 means it does not match.
  // Labels where each word starts a word rank ahead of the rest, because the
  // start of the whole label says little when nearly every one begins "M/s".
  function rank(label, words) {
    let result = 0;
    for (const word of words) {
      let index = label.indexOf(word);
      if (index === -1) return -1;
      while (index !== -1 && !startsWord(label, index)) index = label.indexOf(word, index + 1);
      if (index === -1) result = 1;
    }
    return result;
  }

  function setAttribute(element, name, value) {
    if (value === null) element.removeAttribute(name);
    else element.setAttribute(name, value);
  }

  function enhance(select) {
    if (enhanced.has(select) || select.multiple || select.size > 1) return;
    enhanced.add(select);

    let wrapper = select.parentElement;
    if (!wrapper.classList.contains('ui-select-wrapper')) {
      wrapper = document.createElement('div');
      wrapper.className = 'ui-select-wrapper';
      select.before(wrapper);
      wrapper.append(select);
    }
    // An htmx history snapshot keeps the markup added last time, but not its listeners.
    [...wrapper.children].filter(child => child.hasAttribute(partAttribute)).forEach(child => child.remove());
    select.classList.remove('ui-combobox__native');
    if (!select.id) select.id = `searchable-select-${++sequence}`;

    const input = document.createElement('input');
    const popup = document.createElement('div');
    const listbox = document.createElement('div');
    const empty = document.createElement('p');
    const status = document.createElement('div');

    listbox.id = `${select.id}-options`;
    listbox.className = 'ui-combobox__listbox';
    listbox.setAttribute('role', 'listbox');
    empty.className = 'ui-combobox__empty';
    empty.textContent = 'No matches';
    empty.hidden = true;
    popup.className = 'ui-combobox__popup';
    popup.hidden = true;
    // In the top layer the list is not cut off by a card that clips its overflow,
    // or trapped by the transform an entrance animation leaves on the page.
    if (typeof popup.showPopover === 'function') popup.popover = 'manual';
    popup.append(listbox, empty);
    status.className = 'ui-combobox__status';
    status.setAttribute('role', 'status');

    input.type = 'text';
    input.id = `${select.id}-search`;
    input.className = `${select.className} ui-combobox__input`;
    const inputAttributes = {
      role: 'combobox',
      'aria-autocomplete': 'list',
      'aria-expanded': 'false',
      'aria-controls': listbox.id,
      autocomplete: 'off',
      autocapitalize: 'off',
      spellcheck: 'false',
    };
    Object.entries(inputAttributes).forEach(([name, value]) => input.setAttribute(name, value));

    // The label moves to the search box, so clicking it focuses what people type
    // into rather than the hidden select (which on iOS would open the picker).
    const labels = [...new Set([...select.labels, ...document.querySelectorAll(`label[for="${CSS.escape(input.id)}"]`)])];
    labels.forEach((label, index) => {
      if (!label.id) label.id = `${input.id}-label${index || ''}`;
      label.htmlFor = input.id;
    });
    if (labels.length) listbox.setAttribute('aria-labelledby', labels.map(label => label.id).join(' '));
    ['aria-label', 'aria-labelledby', 'aria-describedby'].forEach(name => {
      const value = select.getAttribute(name);
      if (value === null) return;
      input.setAttribute(name, value);
      if (name !== 'aria-describedby' && !labels.length) listbox.setAttribute(name, value);
    });

    [input, popup, status].forEach(part => part.setAttribute(partAttribute, ''));
    select.after(input, popup, status);
    select.classList.add('ui-combobox__native');
    select.tabIndex = -1;
    select.setAttribute('aria-hidden', 'true');

    let sections = [];
    let shown = [];
    let active = null;
    let navigated = false; // whether the arrow keys put the highlight where it is
    let query = null; // null while browsing the list, the typed text while filtering it
    let above = false;
    let announcement;

    const isOpen = () => !popup.hidden;
    const chosen = () => select.options[select.selectedIndex] || null;
    const firstEnabled = () => shown.find(item => !item.disabled) || null;
    const currentItem = () => shown.find(item => item.option === chosen() && !item.disabled) || firstEnabled();

    function sync() {
      // Sized by its longest option, the search box takes the room the select took.
      input.size = Math.max(1, ...[...select.options].map(option => option.label.length));
      input.disabled = select.disabled;
      setAttribute(input, 'aria-required', select.required ? 'true' : null);
      setAttribute(input, 'aria-invalid', select.getAttribute('aria-invalid'));
      if (query !== null) return;
      const option = chosen();
      const blank = [...select.options].find(candidate => candidate.value === '');
      input.value = option && option.value !== '' ? option.label : '';
      input.placeholder = blank ? blank.label : '';
    }

    // Options are read again on every open, so hidden, disabled and replaced
    // options are always current.
    function readOptions() {
      sections = [];
      let count = 0;
      const item = (option, group) => {
        const element = document.createElement('div');
        element.id = `${listbox.id}-${count++}`;
        element.className = 'ui-combobox__option';
        element.setAttribute('role', 'option');
        element.textContent = option.label;
        const disabled = option.disabled || Boolean(group?.disabled);
        if (disabled) element.setAttribute('aria-disabled', 'true');
        return { option, element, disabled, key: fold(option.label) };
      };
      for (const child of select.children) {
        if (child.hidden) continue;
        if (child.tagName === 'OPTGROUP') {
          const element = document.createElement('div');
          const heading = document.createElement('div');
          heading.id = `${listbox.id}-group-${sections.length}`;
          heading.className = 'ui-combobox__group-label';
          heading.setAttribute('role', 'presentation');
          heading.textContent = child.label;
          element.setAttribute('role', 'group');
          element.setAttribute('aria-labelledby', heading.id);
          const items = [...child.children].filter(option => !option.hidden).map(option => item(option, child));
          sections.push({ element, heading, items });
        } else if (child.tagName === 'OPTION') {
          if (!sections.length || sections.at(-1).element) sections.push({ items: [] });
          sections.at(-1).items.push(item(child));
        }
      }
    }

    function render() {
      const words = query ? fold(query).split(' ').filter(Boolean) : [];
      const current = chosen();
      const rows = [];
      shown = [];
      for (const section of sections) {
        const items = words.length
          ? section.items
            .map(item => [item, rank(item.key, words)])
            .filter(([, score]) => score !== -1)
            .sort((left, right) => left[1] - right[1])
            .map(([item]) => item)
          : section.items;
        if (!items.length) continue;
        items.forEach(item => setAttribute(item.element, 'data-chosen', item.option === current ? '' : null));
        shown.push(...items);
        if (section.element) {
          section.element.replaceChildren(section.heading, ...items.map(item => item.element));
          rows.push(section.element);
        } else {
          rows.push(...items.map(item => item.element));
        }
      }
      listbox.replaceChildren(...rows);
      listbox.hidden = !shown.length;
      empty.hidden = Boolean(shown.length);
      if (!shown.includes(active)) activate(null);
    }

    function activate(item, reveal = false) {
      navigated = false;
      active?.element.removeAttribute('aria-selected');
      active = item && !item.disabled ? item : null;
      if (!active) {
        input.removeAttribute('aria-activedescendant');
        return;
      }
      active.element.setAttribute('aria-selected', 'true');
      input.setAttribute('aria-activedescendant', active.element.id);
      if (reveal) active.element.scrollIntoView({ block: 'nearest' });
    }

    // Arrows wrap around the list; a page step stops at either end.
    function move(step) {
      const enabled = shown.filter(item => !item.disabled);
      if (!enabled.length) return;
      const index = enabled.indexOf(active);
      let next;
      if (Math.abs(step) > 1) next = Math.min(enabled.length - 1, Math.max(0, Math.max(index, 0) + step));
      else if (index === -1) next = step > 0 ? 0 : enabled.length - 1;
      else next = (index + step + enabled.length) % enabled.length;
      activate(enabled[next], true);
      navigated = true;
    }

    // The list opens under the field, or over it when there is more room there,
    // and is shortened rather than run off the screen. It is at least as wide as
    // the field and wider when its options need it, but stays on screen.
    function place(flip = false) {
      const field = input.getBoundingClientRect();
      const { clientWidth, clientHeight } = document.documentElement;
      const room = { below: clientHeight - field.bottom - gap * 2, above: field.top - gap * 2 };
      listbox.style.maxHeight = '';
      popup.style.minWidth = `${field.width}px`;
      const natural = popup.getBoundingClientRect();
      if (flip) above = natural.height > room.below && room.above > room.below;
      const space = above ? room.above : room.below;
      const frame = natural.height - listbox.getBoundingClientRect().height;
      if (natural.height > space) listbox.style.maxHeight = `${Math.max(space - frame, 120)}px`;
      popup.style.left = `${Math.max(gap, Math.min(field.left, clientWidth - natural.width - gap))}px`;
      popup.style.top = above ? 'auto' : `${field.bottom + gap}px`;
      popup.style.bottom = above ? `${clientHeight - field.top + gap}px` : 'auto';
    }

    // Scrolling the page or resizing the window moves the field; scrolling the list
    // does not. An htmx swap can remove the field while the list is open.
    function follow(event) {
      if (!input.isConnected) close();
      else if (event.type !== 'scroll' || !popup.contains(event.target)) place();
    }

    function open() {
      readOptions();
      render();
      popup.hidden = false;
      if (popup.popover) popup.showPopover();
      input.setAttribute('aria-expanded', 'true');
      place(true);
      window.addEventListener('scroll', follow, true);
      window.addEventListener('resize', follow);
      // iOS does not blur the input when someone taps elsewhere on the page.
      document.addEventListener('pointerdown', leaveFromOutside, true);
    }

    function browse() {
      query = null;
      open();
      activate(currentItem(), true);
    }

    function close() {
      clearTimeout(announcement);
      document.removeEventListener('pointerdown', leaveFromOutside, true);
      window.removeEventListener('scroll', follow, true);
      window.removeEventListener('resize', follow);
      if (popup.popover && popup.matches(':popover-open')) popup.hidePopover();
      popup.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      activate(null);
      query = null;
      sync();
    }

    function choose(item) {
      if (!item || item.disabled) return;
      const changed = !item.option.selected;
      item.option.selected = true;
      close();
      if (!changed) return;
      select.dispatchEvent(new Event('input', { bubbles: true }));
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }

    // Leaving keeps the current choice unless the typed text leaves no doubt:
    // it names an option exactly, only one option still matches, or it was
    // cleared to go back to the blank option.
    function leave() {
      if (query !== null) {
        const typed = fold(query);
        const enabled = shown.filter(item => !item.disabled);
        const match = typed
          ? enabled.find(item => item.key === typed) || (enabled.length === 1 ? enabled[0] : null)
          : enabled.find(item => item.option.value === '');
        if (match) {
          choose(match);
          return;
        }
      }
      if (isOpen()) close();
    }

    function leaveFromOutside(event) {
      if (!wrapper.contains(event.target)) leave();
    }

    function itemAt(target) {
      for (let node = target; node && node !== listbox; node = node.parentElement) {
        const item = shown.find(candidate => candidate.element === node);
        if (item) return item;
      }
      return null;
    }

    function refresh() {
      if (isOpen()) {
        readOptions();
        render();
        place();
        if (!active) activate(query === null ? currentItem() : firstEnabled());
      }
      sync();
    }

    input.addEventListener('click', () => {
      if (!isOpen()) {
        browse();
        input.select();
      } else if (query === null) {
        close();
      }
    });

    // Typing is a search, not an answer, so its events stay inside the combobox:
    // forms that submit on change (the queue filters, the member role picker)
    // hear only the select.
    input.addEventListener('change', event => event.stopPropagation());
    input.addEventListener('input', event => {
      event.stopPropagation();
      query = input.value;
      if (isOpen()) {
        render();
        place();
      } else {
        open();
      }
      activate(firstEnabled(), true);
      clearTimeout(announcement);
      announcement = setTimeout(() => {
        const count = shown.filter(item => !item.disabled).length;
        status.textContent = count ? `${count} ${count === 1 ? 'match' : 'matches'}` : 'No matches';
      }, 400);
    });

    input.addEventListener('keydown', event => {
      if (event.isComposing) return;
      const opened = isOpen();
      switch (event.key) {
        case 'ArrowDown':
        case 'ArrowUp':
          event.preventDefault();
          if (!opened) browse();
          else if (!event.altKey) move(event.key === 'ArrowDown' ? 1 : -1);
          else if (event.key === 'ArrowUp') close();
          break;
        case 'PageDown':
        case 'PageUp':
          if (!opened) return;
          event.preventDefault();
          move(event.key === 'PageDown' ? pageSize : -pageSize);
          break;
        case 'Enter':
          // A select never submits its form on Enter, and neither does this.
          event.preventDefault();
          if (opened) choose(active);
          break;
        case 'Tab':
          // Moving the highlight with the arrows and tabbing on is how people
          // pick from a select with the keyboard.
          if (opened && navigated) choose(active);
          break;
        case 'Escape':
          if (!opened) return;
          event.preventDefault();
          event.stopPropagation();
          close();
          break;
        default:
      }
    });

    input.addEventListener('blur', leave);
    // Pressing an option must not take focus from the input before the click lands.
    popup.addEventListener('mousedown', event => event.preventDefault());
    listbox.addEventListener('click', event => choose(itemAt(event.target)));
    listbox.addEventListener('mousemove', event => {
      const item = itemAt(event.target);
      if (item && item !== active) activate(item);
    });

    // Other scripts focus the select to point at a missing answer, and change it
    // directly, sometimes without an event (see organisation-form.js).
    select.addEventListener('focus', () => input.focus());
    select.addEventListener('change', refresh);
    select.form?.addEventListener('reset', () => setTimeout(refresh));
    new MutationObserver(refresh).observe(select, { attributes: true, characterData: true, childList: true, subtree: true });
    sync();
  }

  function enhanceWithin(root) {
    if (root.matches?.(selector)) enhance(root);
    root.querySelectorAll?.(selector).forEach(enhance);
  }

  document.addEventListener('htmx:load', event => enhanceWithin(event.detail?.elt || event.target));
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => enhanceWithin(document));
  else enhanceWithin(document);
})();
