document.addEventListener("DOMContentLoaded", function () {
  var contentBody = document.getElementById("contentPanelBody");
  var panelEditBtn = document.querySelector("[data-panel-edit-toggle]");
  var pill = document.getElementById("generatingPill");
  var form = document.getElementById("templateForm");
  var saveUrl = form ? form.dataset.saveUrl : null;
  var statusUrl = form ? form.dataset.statusUrl : null;

  var nextStepBtn = document.getElementById("nextStepBtn");
  var isEditable = false;
  var pollTimer = null;

  function setNextStepEnabled(enabled) {
    if (!nextStepBtn) return;
    nextStepBtn.disabled = !enabled;
    nextStepBtn.title = enabled ? "" : "Please wait for content generation to finish";
  }

  function applyEditState() {
    if (!contentBody) return;
    contentBody.setAttribute("data-editable", isEditable ? "true" : "false");
    contentBody.setAttribute("contenteditable", isEditable ? "true" : "false");
    if (panelEditBtn) panelEditBtn.textContent = isEditable ? "Done" : "Edit";
  }

  function saveEditedContent() {
    if (!contentBody || !saveUrl) return;
    fetch(saveUrl, {
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

  function startPolling() {
    if (pollTimer || !statusUrl) return;
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
        setNextStepEnabled(true);
        return;
      }

      fetch(statusUrl)
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
            setNextStepEnabled(true);
          }
        })
        .catch(function () {
          clearInterval(pollTimer);
          pollTimer = null;
          setNextStepEnabled(true);
        });
    }, intervalMs);
  }

  if (pill && !pill.hidden) {
    setNextStepEnabled(false);
    startPolling();
  }

  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var nextStepBtn = document.getElementById("nextStepBtn");
      var formData = new FormData(form);
      var payload = {};
      formData.forEach(function (value, key) {
        payload[key] = value;
      });

      if (nextStepBtn) {
        nextStepBtn.disabled = true;
        nextStepBtn.textContent = "Saving...";
      }

      fetch(saveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
        .then(function (res) {
          if (!res.ok)
            return res.json().then(function (data) {
              throw new Error(data.error || "Could not save this step.");
            });
          return res.json();
        })
        .then(function (data) {
          window.location.href = data.next_url || "#";
        })
        .catch(function (err) {
          alert(err.message);
          if (nextStepBtn) {
            nextStepBtn.disabled = false;
            nextStepBtn.textContent = "Next Step";
          }
        });
    });
  }

  applyEditState();
});
