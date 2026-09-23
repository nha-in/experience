/* Small progressive enhancements for the public website. */
(() => {
  const header = document.querySelector(".marketing-light .marketing-header");
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
})();
