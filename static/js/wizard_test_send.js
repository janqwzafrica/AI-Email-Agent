document.addEventListener("DOMContentLoaded", function () {
  var rail = document.getElementById("testRail");
  var sendBtn = document.getElementById("sendTestEmailBtn");
  var sentBadge = document.getElementById("testSentBadge");
  if (!rail || !sendBtn) return;

  var sendUrl = rail.dataset.sendUrl;

  sendBtn.addEventListener("click", function () {
    var checked = Array.prototype.slice
      .call(document.querySelectorAll(".test-email-checkbox:checked"))
      .map(function (el) {
        return el.value;
      });

    if (!checked.length) {
      alert("Select at least one test email.");
      return;
    }

    sendBtn.disabled = true;
    sendBtn.textContent = "Sending...";

    fetch(sendUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ emails: checked }),
    })
      .then(function (res) {
        if (!res.ok)
          return res.json().then(function (data) {
            throw new Error(data.error || "Could not send test email.");
          });
        return res.json();
      })
      .then(function () {
        sentBadge.hidden = false;
      })
      .catch(function (err) {
        alert(err.message);
      })
      .finally(function () {
        sendBtn.disabled = false;
        sendBtn.textContent = "Send Test Email";
      });
  });
});
