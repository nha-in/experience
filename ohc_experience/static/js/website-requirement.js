// A website is asked of every kind of entity except an individual or sole
// proprietorship, which may well trade under a business name without one.
// The server applies the same rule; this keeps the box and the markers on its
// label in step as the type of entity changes. Where the type is already settled
// the select is disabled, and reading it here simply confirms what was rendered.
(() => {
  const typeSelector = 'select[name="organisation_type"], select[name="entity_type"]';
  const soleProprietorship = 'sole_proprietor';

  function update(select) {
    const website = select.form?.elements.namedItem('website');
    if (!website) return;
    const optional = select.value === soleProprietorship;
    website.required = !optional;
    const label = website.labels[0];
    const optionalMarker = label?.querySelector('[data-optional-marker]');
    if (optionalMarker) optionalMarker.hidden = !optional;
    const requiredMarker = label?.querySelector('[data-required-marker]');
    if (requiredMarker) requiredMarker.hidden = optional;
  }

  const updateAll = () => document.querySelectorAll(typeSelector).forEach(update);

  // Form media runs again whenever HTMX swaps a form in, so listen only once.
  if (!window.websiteRequirementListening) {
    window.websiteRequirementListening = true;
    document.addEventListener('change', event => {
      if (event.target.matches(typeSelector)) update(event.target);
    });
    document.addEventListener('DOMContentLoaded', updateAll);
  }
  updateAll();
})();
