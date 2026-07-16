document.addEventListener("DOMContentLoaded", function () {
  var contentBody = document.getElementById("contentPanelBody");
  var topEditBtn = document.getElementById("editContentTopBtn");
  var panelEditBtn = document.querySelector("[data-panel-edit-toggle]");
  var pill = document.getElementById("generatingPill");

  var isEditable = false;
  var pollTimer = null;

  // --- Inline content editing (panel "Edit" button) ---

  function applyEditState() {
    if (!contentBody) return;
    contentBody.setAttribute("data-editable", isEditable ? "true" : "false");
    contentBody.setAttribute("contenteditable", isEditable ? "true" : "false");
    if (panelEditBtn) panelEditBtn.textContent = isEditable ? "Done" : "Edit";
  }

  function saveEditedContent() {
    if (!contentBody) return;
    fetch("/campaigns/ai-wizard/save-content", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email_content: contentBody.innerHTML }),
    }).catch(function () {
      // Non-fatal — content stays visible in the DOM either way
    });
  }

  function togglePanelEdit() {
    if (isEditable) {
      saveEditedContent();
    }
    isEditable = !isEditable;
    applyEditState();
  }

  if (panelEditBtn) panelEditBtn.addEventListener("click", togglePanelEdit);

  // --- Top "Edit Content" button → back to upload page ---

  if (topEditBtn) {
    topEditBtn.addEventListener("click", function () {
      window.location.href = "/campaigns/ai-wizard/upload";
    });
  }

  // --- Generation status polling (with 60s timeout) ---

  function startPolling() {
    if (pollTimer) return;
    var elapsed = 0;
    var intervalMs = 2000;
    var timeoutMs = 60000;

    pollTimer = setInterval(function () {
      elapsed += intervalMs;

      if (elapsed >= timeoutMs) {
        clearInterval(pollTimer);
        pollTimer = null;
        if (pill) {
          pill.textContent = "Generation timed out — please try again";
          pill.classList.add("status-pill--error");
        }
        return;
      }

      fetch("/campaigns/ai-wizard/status")
        .then(function (res) {
          return res.json();
        })
        .then(function (data) {
          if (!data.is_generating) {
            clearInterval(pollTimer);
            pollTimer = null;
            if (pill) {
              pill.textContent = "Content has been generated successfully";
              pill.classList.remove("status-pill--error");
              pill.classList.add("status-pill--success");
              setTimeout(function () {
                pill.hidden = true;
              }, 3000);
            }
            if (contentBody && data.email_content) {
              contentBody.innerHTML = data.email_content;
            }
          }
        })
        .catch(function () {
          clearInterval(pollTimer);
          pollTimer = null;
        });
    }, intervalMs);
  }

  // Kick off polling immediately if the page loaded mid-generation
  if (pill && !pill.hidden) {
    startPolling();
  }

  // --- Next Step form submit ---

  var form = document.getElementById("templateForm");
  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();

      var payload = {
        sender_email: document.getElementById("senderEmail").value,
        sender_name: document.getElementById("senderName").value,
        email_subject: document.getElementById("emailSubject").value,
        email_list: document.getElementById("emailList").value,
      };

      fetch("/campaigns/ai-wizard/save-template-fields", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
        .then(function () {
          window.location.href = form.dataset.nextUrl || "#";
        })
        .catch(function () {
          // Even if saving fails, don't block navigation — the draft keeps
          // whatever was last successfully saved.
          window.location.href = form.dataset.nextUrl || "#";
        });
    });
  }

  applyEditState();
});
