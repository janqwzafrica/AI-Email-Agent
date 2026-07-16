document.addEventListener("DOMContentLoaded", function () {
  var form = document.getElementById("testEmailsSaveForm");
  var saveBtn = document.getElementById("saveTestEmailsBtn");
  if (!form || !saveBtn) return;

  form.addEventListener("submit", function () {
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving...";
  });
});
