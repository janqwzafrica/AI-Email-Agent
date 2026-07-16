document.addEventListener("DOMContentLoaded", function () {
  var rail = document.getElementById("scheduleRail");
  if (!rail) return;

  var actionUrl = rail.dataset.actionUrl;
  var dateTimeInput = document.getElementById("scheduleDateTime");
  var scheduleBtn = document.getElementById("scheduleBtn");
  var runNowBtn = document.getElementById("runNowBtn");
  var finishBtn = document.getElementById("finishBtn");

  function postAction(action, extra) {
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
        window.location.href = data.next_url || "#";
      })
      .catch(function (err) {
        alert(err.message);
      });
  }

  if (scheduleBtn) {
    scheduleBtn.addEventListener("click", function () {
      if (!dateTimeInput.value) {
        alert("Choose a date and time to schedule for.");
        return;
      }
      postAction("schedule", { scheduled_at: dateTimeInput.value });
    });
  }

  if (runNowBtn) {
    runNowBtn.addEventListener("click", function () {
      if (!confirm("Send this campaign now?")) return;
      postAction("run_now");
    });
  }

  if (finishBtn) {
    finishBtn.addEventListener("click", function () {
      postAction("finish");
    });
  }
});
