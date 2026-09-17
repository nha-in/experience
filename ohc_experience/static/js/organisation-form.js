// A sole proprietorship gives a business name and verifies with PAN or GSTIN, since it has no CIN.
// The server rejects CIN for it too; this keeps the form in step as the type of entity changes.
(() => {
  const typeSelector = 'select[name="entity_type"]';
  const documentTypeSelector = 'select[name="verification_document_type"]';
  const documentLimits = {
    PAN: { maxLength: 10, pattern: '[A-Za-z]{5}[0-9]{4}[A-Za-z]' },
    GSTIN: { maxLength: 15, pattern: '[0-9]{2}[A-Za-z]{5}[0-9]{4}[A-Za-z][1-9A-Za-z]Z[0-9A-Za-z]' },
    CIN: { maxLength: 21, pattern: '[LU][0-9]{5}[A-Za-z]{2}[0-9]{4}[A-Za-z]{3}[0-9]{6}' },
  };

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

    const documentNumber = form.elements.namedItem('verification_document_number');
    const limit = documentLimits[documentType.value];
    if (limit) {
      documentNumber.maxLength = limit.maxLength;
      documentNumber.pattern = limit.pattern;
      documentNumber.title = `Enter a ${limit.maxLength}-character ${documentType.value} number.`;
    }
  }

  const updateAll = () => document.querySelectorAll(typeSelector).forEach(select => update(select.form));

  // Form media runs again whenever HTMX swaps a form in, so listen only once.
  if (!window.organisationFormListening) {
    window.organisationFormListening = true;
    document.addEventListener('change', event => {
      if (event.target.matches(typeSelector) || event.target.matches(documentTypeSelector)) {
        update(event.target.form);
      }
    });
    document.addEventListener('input', event => {
      if (event.target.matches('input[name="verification_document_number"]')) {
        const start = event.target.selectionStart;
        event.target.value = event.target.value.toUpperCase();
        event.target.setSelectionRange(start, start);
      }
    });
    document.addEventListener('DOMContentLoaded', updateAll);
  }
  updateAll();
})();
