export function initDropdowns() {
  const dropdowns = document.querySelectorAll("[data-dropdown]");
  if (!dropdowns.length) return;

  dropdowns.forEach((dropdown) => {
    const toggle = dropdown.querySelector("[data-dropdown-toggle]");
    const menu = dropdown.querySelector("[data-dropdown-menu]");
    if (!toggle || !menu) return;

    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      const isOpen = menu.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", String(isOpen));
    });
  });

  // Close any open dropdown when clicking elsewhere or pressing Escape
  const closeAll = () => {
    document.querySelectorAll("[data-dropdown-menu].is-open").forEach((menu) => {
      menu.classList.remove("is-open");
      const toggle = menu
        .closest("[data-dropdown]")
        .querySelector("[data-dropdown-toggle]");
      toggle.setAttribute("aria-expanded", "false");
    });
  };

  document.addEventListener("click", closeAll);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeAll();
  });
}
