/* Small progressive enhancements for the public website. */
(() => {
  const menu = document.querySelector(".marketing-mobile-menu");
  if (menu) {
    menu.addEventListener("click", (event) => {
      if (event.target.closest("a")) menu.open = false;
    });
    document.addEventListener("click", (event) => {
      if (!menu.contains(event.target)) menu.open = false;
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && menu.open) {
        menu.open = false;
        menu.querySelector("summary").focus();
      }
    });
  }

  // The prompt remains selectable when Clipboard API access is unavailable.
  if (!navigator.clipboard?.writeText) return;
  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    const target = document.getElementById(button.dataset.copyTarget);
    if (!target) return;
    button.hidden = false;
    const original = button.innerHTML;
    let resetTimer;
    button.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(target.textContent.trim());
        button.textContent = button.dataset.copiedLabel;
        const status = document.getElementById("interaction-status");
        if (status) status.textContent = button.dataset.copiedLabel;
        clearTimeout(resetTimer);
        resetTimer = setTimeout(() => {
          button.innerHTML = original;
        }, 2400);
      } catch {
        // Select the complete prompt so it can still be copied manually.
        target.focus();
        const range = document.createRange();
        range.selectNodeContents(target);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        const status = document.getElementById("interaction-status");
        if (status) status.textContent = button.dataset.copyFallbackLabel;
      }
    });
  });
})();
