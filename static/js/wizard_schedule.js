document.addEventListener("DOMContentLoaded", function () {
  var rail = document.getElementById("scheduleRail");
  if (!rail) return;

  var actionUrl = rail.dataset.actionUrl;
  var dateTimeInput = document.getElementById("scheduleDateTime");
  var scheduleBtn = document.getElementById("scheduleBtn");
  var runNowBtn = document.getElementById("runNowBtn");
  var finishBtn = document.getElementById("finishBtn");

  function setBusy(button, busyText) {
    if (!button) return function () {};
    var originalText = button.textContent;
    button.disabled = true;
    button.textContent = busyText;
    return function reset() {
      button.disabled = false;
      button.textContent = originalText;
    };
  }

  function postAction(action, extra, button, busyText) {
    var reset = setBusy(button, busyText);
    var body = Object.assign({ action: action }, extra || {});
    return fetch(actionUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (res) {
        if (!res.ok)
          return res.json().then(function (data) {
            throw new Error(data.error || "Could not complete this action.");
          });
        return res.json();
      })
      .then(function (data) {
        // Leave the button disabled/busy — we're navigating away anyway.
        window.location.href = data.next_url || "#";
      })
      .catch(function (err) {
        reset();
        alert(err.message);
      });
  }

  if (scheduleBtn) {
    scheduleBtn.addEventListener("click", function () {
      if (!dateTimeInput.value) {
        alert("Choose a date and time to schedule for.");
        return;
      }
      // datetime-local gives a naive string with no timezone. `new Date(...)`
      // parses that as the browser's local time, so toISOString() below turns
      // it into an absolute UTC instant the server can use without having to
      // guess (and possibly mismatch) any timezone.
      var localDate = new Date(dateTimeInput.value);
      if (isNaN(localDate.getTime())) {
        alert("Choose a valid date and time to schedule for.");
        return;
      }
      postAction(
        "schedule",
        { scheduled_at: localDate.toISOString() },
        scheduleBtn,
        "Scheduling…"
      );
    });
  }

  if (runNowBtn) {
    runNowBtn.addEventListener("click", function () {
      if (!confirm("Send this campaign now?")) return;
      postAction("run_now", null, runNowBtn, "Running…");
    });
  }

  if (finishBtn) {
    finishBtn.addEventListener("click", function () {
      postAction("finish", null, finishBtn, "Finishing…");
    });
  }
});
