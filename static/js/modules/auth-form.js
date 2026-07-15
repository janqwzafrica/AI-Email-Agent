export function initAuthFormLoading() {
  const forms = document.querySelectorAll(".auth-form");
  if (!forms.length) return;

  forms.forEach((form) => {
    form.addEventListener("submit", () => {
      const submitButton = form.querySelector(".auth-submit");
      if (!submitButton) return;

      submitButton.dataset.originalText = submitButton.textContent.trim();
      submitButton.disabled = true;
      submitButton.setAttribute("aria-busy", "true");
      const loadingText = submitButton.dataset.loadingText || "Submitting...";
      submitButton.innerHTML = `<span class="auth-submit__spinner" aria-hidden="true"></span><span>${loadingText}</span>`;
    });
  });

  window.addEventListener("pageshow", () => {
    forms.forEach((form) => {
      const submitButton = form.querySelector(".auth-submit[aria-busy='true']");
      if (!submitButton) return;

      submitButton.disabled = false;
      submitButton.removeAttribute("aria-busy");
      submitButton.textContent = submitButton.dataset.originalText || "Submit";
    });
  });
}
