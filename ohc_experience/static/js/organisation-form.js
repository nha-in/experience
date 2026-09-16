// A sole proprietorship gives a business name and verifies with PAN or GSTIN, since it has no CIN.
// The server rejects CIN for it too; this keeps the form in step as the type of entity changes.
(() => {
  const typeSelector = 'select[name="entity_type"]';

  function update(form) {
    const soleProprietorship = form.elements.namedItem('entity_type').value === 'sole_proprietor';

    const nameLabel = form.elements.namedItem('name').labels[0];
    nameLabel.dataset.defaultLabel ??= nameLabel.textContent.trim();
    nameLabel.textContent = soleProprietorship ? 'Business name' : nameLabel.dataset.defaultLabel;

    const documentType = form.elements.namedItem('verification_document_type');
    const cin = documentType.querySelector('option[value="CIN"]');
    if (soleProprietorship && cin.selected) documentType.value = 'PAN';
    cin.hidden = soleProprietorship;
    cin.disabled = soleProprietorship;
  }

  const updateAll = () => document.querySelectorAll(typeSelector).forEach(select => update(select.form));

  // Form media runs again whenever HTMX swaps a form in, so listen only once.
  if (!window.organisationFormListening) {
    window.organisationFormListening = true;
    document.addEventListener('change', event => {
      if (event.target.matches(typeSelector)) update(event.target.form);
    });
    document.addEventListener('DOMContentLoaded', updateAll);
  }
  updateAll();
})();
