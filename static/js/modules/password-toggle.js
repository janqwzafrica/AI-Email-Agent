export function initPasswordToggles() {
  const toggles = document.querySelectorAll("[data-password-toggle]");
  if (!toggles.length) return;

  toggles.forEach((toggle) => {
    const inputId = toggle.getAttribute("aria-controls");
    const input = inputId ? document.getElementById(inputId) : null;
    if (!input) return;

    toggle.addEventListener("click", () => {
      const shouldShow = input.type === "password";
      input.type = shouldShow ? "text" : "password";
      toggle.setAttribute("aria-label", shouldShow ? "Hide password" : "Show password");
    });
  });
}
