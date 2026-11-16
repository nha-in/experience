// The staff search box (components/global_search.html), after Odoo's: what
// people type is offered as a row per field, "Search Organisation for: care",
// the first one highlighted, and the row chosen opens the list it belongs to,
// searched in that field.
//
// The rows are a listbox under a combobox: up and down move, Enter opens,
// Escape closes. Each row is a boosted link, so opening one is a click on it.
// "/" anywhere but in a field goes to the top bar's box.
(() => {
  const selector = 'form[data-global-search]';
  const ready = new WeakSet();

  function setUp(form) {
    if (ready.has(form)) return;
    const input = form.querySelector('[role="combobox"]');
    const popup = form.querySelector('[data-global-search-popup]');
    const listbox = form.querySelector('[role="listbox"]');
    const template = form.querySelector('template[data-global-search-rows]');
    const status = form.querySelector('[data-global-search-status]');
    if (!input || !popup || !listbox || !template) return;
    ready.add(form);

    let active = null;
    let shownFor = null; // the text the rows carry

    const text = () => input.value.trim();
    const isOpen = () => !popup.hidden;
    const rows = () => [...listbox.querySelectorAll('[role="option"]')];

    function activate(row) {
      active?.setAttribute('aria-selected', 'false');
      active = row || null;
      if (!active) {
        input.removeAttribute('aria-activedescendant');
        return;
      }
      active.setAttribute('aria-selected', 'true');
      input.setAttribute('aria-activedescendant', active.id);
      active.scrollIntoView?.({ block: 'nearest' });
    }

    function open() {
      if (!isOpen()) {
        popup.hidden = false;
        // iOS does not blur the input when someone taps elsewhere on the page.
        document.addEventListener('pointerdown', leaveFromOutside, true);
      }
      input.setAttribute('aria-expanded', 'true');
    }

    function close() {
      document.removeEventListener('pointerdown', leaveFromOutside, true);
      popup.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      activate(null);
    }

    function leaveFromOutside(event) {
      if (!form.contains(event.target)) close();
    }

    // The rows are built afresh for each change of text, since each row's link
    // carries it, and htmx boosts the new links.
    function render() {
      const typed = text();
      if (!typed) {
        shownFor = null;
        listbox.replaceChildren();
        close();
        return;
      }
      const opening = !isOpen();
      if (typed !== shownFor) {
        listbox.replaceChildren(template.content.cloneNode(true));
        rows().forEach((row, index) => {
          row.id = `${listbox.id}-${index}`;
          row.querySelector('[data-global-search-text]').textContent = typed;
          const target = new URL(row.dataset.url, window.location.origin);
          target.searchParams.set('q', typed);
          row.setAttribute('href', `${target.pathname}${target.search}`);
        });
        window.htmx?.process(listbox);
        shownFor = typed;
        active = null;
      }
      open();
      if (!active) activate(rows()[0]);
      if (opening && status) status.textContent = `${rows().length} search options`;
    }

    // Arrows wrap around the rows.
    function move(step) {
      const list = rows();
      if (!list.length) return;
      const index = list.indexOf(active);
      const next = index === -1 ? (step > 0 ? 0 : list.length - 1) : (index + step + list.length) % list.length;
      activate(list[next]);
    }

    // The drawer stays open over the page a submitted search lands on unless
    // it is closed here; a chosen row is a link, which closes it already.
    function closeDrawer() {
      const drawer = document.getElementById('app-drawer');
      if (!drawer?.checked || !form.closest('#app-sidebar')) return;
      drawer.checked = false;
      drawer.dispatchEvent(new Event('change'));
    }

    input.addEventListener('input', render);
    input.addEventListener('focus', render);

    input.addEventListener('keydown', event => {
      if (event.isComposing) return;
      switch (event.key) {
        case 'ArrowDown':
        case 'ArrowUp':
          if (!text()) return;
          event.preventDefault();
          if (!isOpen()) render();
          else move(event.key === 'ArrowDown' ? 1 : -1);
          break;
        case 'Enter': {
          if (!text()) {
            event.preventDefault();
            return;
          }
          if (!isOpen() || !active) break; // the form submits, and the server opens the first row
          event.preventDefault();
          const row = active;
          close();
          row.click();
          break;
        }
        case 'Escape':
          if (!isOpen()) return;
          event.preventDefault();
          event.stopPropagation();
          close();
          break;
        default:
      }
    });

    form.addEventListener('focusout', event => {
      if (!form.contains(event.relatedTarget)) close();
    });
    form.addEventListener('submit', () => {
      close();
      closeDrawer();
    });
    // Pressing a row must not take focus from the input before the click lands.
    popup.addEventListener('mousedown', event => event.preventDefault());
    popup.addEventListener('mousemove', event => {
      const row = event.target.closest('[role="option"]');
      if (row && row !== active) activate(row);
    });
    popup.addEventListener('click', event => {
      if (event.target.closest('[role="option"]')) close();
    });
  }

  function setUpWithin(root) {
    if (root.matches?.(selector)) setUp(root);
    root.querySelectorAll?.(selector).forEach(setUp);
  }

  // "/" puts the cursor in the search box that shows it, from anywhere on the
  // page but a field, where it is typed as it is.
  document.addEventListener('keydown', event => {
    if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
    const target = event.target;
    if (target?.isContentEditable || target?.closest?.('input, textarea, select')) return;
    const input = [...document.querySelectorAll('[aria-keyshortcuts="/"]')].find(field => field.getClientRects().length);
    if (!input) return;
    event.preventDefault();
    input.focus();
  });

  document.addEventListener('htmx:load', event => setUpWithin(event.detail?.elt || event.target));
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setUpWithin(document));
  else setUpWithin(document);
})();
