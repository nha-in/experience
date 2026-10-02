/* Small progressive enhancements for the public website. */
(() => {
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
      if (target)
        requestAnimationFrame(() => target.focus({ preventScroll: true }));
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
  const search = document.querySelector(".marketing-search");
  if (search) {
    const input = search.querySelector("input");
    const results = search.querySelector(".marketing-search-results");
    const links = [...results.querySelectorAll("[data-search-resource]")];
    const empty = results.querySelector(".marketing-search-empty");
    const showResults = () => {
      const query = input.value.trim().toLocaleLowerCase();
      let matches = 0;
      links.forEach((link) => {
        link.hidden = !link.textContent.toLocaleLowerCase().includes(query);
        if (!link.hidden) matches += 1;
      });
      empty.hidden = matches > 0;
      results.hidden = false;
    };
    input.addEventListener("input", showResults);
    input.addEventListener("focus", showResults);
    search
      .querySelector("[data-search-toggle]")
      .addEventListener("click", () => {
        input.focus();
        showResults();
      });
    input.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "Enter") {
        event.preventDefault();
        showResults();
        const first = links.find((link) => !link.hidden);
        (first || results.querySelector(".marketing-search-all")).focus();
      }
    });
    search.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        input.focus();
        results.hidden = true;
      }
    });
    search.addEventListener("focusout", (event) => {
      if (!search.contains(event.relatedTarget)) results.hidden = true;
    });
    document.addEventListener("click", (event) => {
      if (!search.contains(event.target)) results.hidden = true;
    });
  }

  let textSize = 100;
  document.querySelectorAll("[data-text-size]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.textSize;
      textSize =
        action === "reset"
          ? 100
          : Math.max(
              90,
              Math.min(130, textSize + (action === "increase" ? 10 : -10)),
            );
      document.documentElement.style.fontSize = `${textSize}%`;
    });
  });
  const contrast = document.querySelector("[data-contrast-toggle]");
  if (contrast) {
    contrast.addEventListener("click", () => {
      const enabled = document.body.classList.toggle("marketing-high-contrast");
      contrast.setAttribute("aria-pressed", String(enabled));
    });
  }
})();
