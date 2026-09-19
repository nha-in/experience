// The support sub-menu only offers the issue types of the category above it.
//
// The category select stays the source of truth for what the sub-menu shows,
// and the sub-menu select stays the source of truth for what is posted. Options
// are hidden rather than removed, which is what searchable-select.js reads when
// it draws the combobox, and every value this script writes is announced with a
// change event so that combobox redraws. A category with no sub-menu hides the
// field: there is nothing to choose, and the server stores no issue type for it.
//
// Without JavaScript the sub-menu is a flat select listing every issue type,
// and the server still rejects one that does not belong to the chosen category.
(() => {
  const selector = '[data-issue-type-field]';

  function narrow(category, issueType, field) {
    const chosen = category.value;
    let available = 0;
    for (const option of issueType.options) {
      if (option.value === '') continue;
      option.hidden = option.dataset.category !== chosen;
      if (!option.hidden) available += 1;
    }
    field.hidden = !available;
    const current = issueType.options[issueType.selectedIndex];
    if ((!available || current?.hidden) && issueType.value !== '') {
      issueType.value = '';
      issueType.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  function fieldsWithin(root) {
    return [
      ...(root.matches?.(selector) ? [root] : []),
      ...(root.querySelectorAll?.(selector) || []),
    ];
  }

  function attach(field) {
    const issueType = field.querySelector('select[name="issue_type"]');
    const category = field.closest('form')?.querySelector('select[name="category"]');
    if (!issueType || !category) return;
    const update = () => narrow(category, issueType, field);
    if (!field.dataset.issueTypeReady) {
      field.dataset.issueTypeReady = 'true';
      category.addEventListener('change', update);
    }
    update();
  }

  function initialize(root = document) {
    fieldsWithin(root).forEach(attach);
  }

  document.addEventListener('DOMContentLoaded', () => initialize());
  document.addEventListener('htmx:load', event => initialize(event.detail?.elt || event.target));
  document.addEventListener('htmx:afterSwap', event => initialize(event.detail?.target || event.target));
  window.addEventListener('pageshow', () => initialize());
  initialize();
})();
