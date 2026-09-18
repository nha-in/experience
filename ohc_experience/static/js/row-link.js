// A row that opens a page opens it from anywhere in the row, not only from its
// name. Mark the link to that page data-row-link, and the table row or list item
// that holds it follows that link on a click anywhere else in the row.
//
// The link stays the row's one real link. Keyboard and screen reader users reach
// the page through it, htmx boosts it, and a click elsewhere in the row is handed
// to it, so both open the page the same way. A click on another control in the
// row stays with that control, and a click that ends a text selection only
// selects, so a reference can still be copied out of the row. Ctrl or Cmd and the
// middle button open the page in a new tab, as they do on the link itself.
// Without JavaScript only the link opens the page.
(() => {
  const rows = 'tr, li';
  const controls = 'a[href], button, input, select, textarea, label, summary, [role="button"]';

  // The nearest row with a marked link of its own. A marked link in a row nested
  // inside it, such as a table in a list item, belongs to that inner row.
  function rowLink(element) {
    for (let row = element.closest(rows); row; row = row.parentElement?.closest(rows)) {
      const link = [...row.querySelectorAll('a[data-row-link]')].find(candidate => candidate.closest(rows) === row);
      if (link) return link;
    }
    return null;
  }

  function linkFor(event) {
    const { target } = event;
    if (event.defaultPrevented || !(target instanceof Element) || target.closest(controls)) return null;
    return rowLink(target);
  }

  const openInNewTab = link => window.open(link.href, '_blank', 'noopener');

  document.addEventListener('click', event => {
    // Shift and Alt extend a selection or download; neither means open the row.
    if (event.button !== 0 || event.shiftKey || event.altKey || window.getSelection()?.toString()) return;
    const link = linkFor(event);
    if (!link) return;
    if (event.ctrlKey || event.metaKey) openInNewTab(link);
    else link.click();
  });
  // A middle press on a link does not start autoscrolling, so neither does one
  // on its row.
  document.addEventListener('mousedown', event => {
    if (event.button === 1 && linkFor(event)) event.preventDefault();
  });
  document.addEventListener('auxclick', event => {
    const link = event.button === 1 && linkFor(event);
    if (link) openInNewTab(link);
  });
})();
