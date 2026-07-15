export function initPasswordResetCodeSender() {
  const button = document.querySelector("[data-password-reset-code]");
  if (!button) return;

  const form = button.closest("form");
  const emailInput = form?.querySelector("input[name='email']");
  const message = form?.querySelector("[data-password-reset-message]");
  if (!form || !emailInput || !message) return;

  const originalText = button.textContent.trim();

  function showMessage(text, type) {
    message.textContent = text;
    message.hidden = false;
    message.className = `auth-inline-message auth-inline-message--${type}`;
  }

  button.addEventListener("click", async () => {
    if (!emailInput.reportValidity()) return;

    button.disabled = true;
    button.textContent = "Sending...";
    message.hidden = true;

    const body = new URLSearchParams();
    body.set("email", emailInput.value);

    try {
      const response = await fetch("/forgot-password/send-code", {
        method: "POST",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body,
      });
      const data = await response.json();
      showMessage(data.message || "Request complete.", response.ok && data.success ? "success" : "error");
    } catch {
      showMessage("We could not send a reset code right now. Please try again.", "error");
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
}
