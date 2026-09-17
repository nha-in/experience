/* Small progressive enhancements for the public website. */
(() => {
  const header = document.querySelector(".marketing-landing .marketing-header");
  if (header && "ResizeObserver" in window) {
    // Keep anchors and keyboard focus below the header when text is enlarged.
    const headerObserver = new ResizeObserver(() => {
      header.closest(".marketing-shell").style.setProperty(
        "--marketing-header-height",
        `${header.getBoundingClientRect().height}px`,
      );
    });
    headerObserver.observe(header);
  }
  const menu = document.querySelector(".marketing-mobile-menu");
  if (menu) {
    menu.addEventListener("click", (event) => {
      const link = event.target.closest("a");
      if (!link || event.defaultPrevented) return;
      menu.open = false;

      // Keep native navigation, then place keyboard focus at an in-page
      // destination instead of leaving it inside the now-closed menu.
      const destination = new URL(link.href, window.location.href);
      if (
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey ||
        destination.origin !== window.location.origin ||
        destination.pathname !== window.location.pathname ||
        destination.search !== window.location.search ||
        !destination.hash
      ) {
        return;
      }
      const target = document.getElementById(
        decodeURIComponent(destination.hash.slice(1)),
      );
      if (target) requestAnimationFrame(() => target.focus({ preventScroll: true }));
    });
    document.addEventListener("click", (event) => {
      if (!menu.contains(event.target)) menu.open = false;
    });
    menu.addEventListener("focusout", (event) => {
      if (!menu.contains(event.relatedTarget)) menu.open = false;
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
