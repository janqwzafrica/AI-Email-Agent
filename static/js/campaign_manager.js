document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".table__delete--btn").forEach(function (button) {
    button.closest("form").addEventListener("submit", function () {
      // Native form submit (full page nav), so no need to reset this —
      // the page is about to unload either way.
      button.disabled = true;
      button.textContent = "Deleting…";
    });
  });

  document.querySelectorAll(".quarterly-run").forEach(function (button) {
    button.closest("form").addEventListener("submit", function () {
      button.disabled = true;
      button.textContent = button.classList.contains("quarterly-run--running")
        ? "Deactivating…"
        : "Activating…";
    });
  });
});
