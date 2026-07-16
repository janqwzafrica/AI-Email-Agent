document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".classification-select").forEach(function (select) {
    select.addEventListener("change", function () {
      var email = select.dataset.email;
      var classification = select.value;
      var previous = select.dataset.previousValue || "";

      select.disabled = true;
      fetch("/campaign-manager/contacts/classification", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email, classification: classification }),
      })
        .then(function (res) {
          if (!res.ok)
            return res.json().then(function (data) {
              throw new Error(data.error || "Could not save classification.");
            });
          return res.json();
        })
        .then(function () {
          select.dataset.previousValue = classification;
        })
        .catch(function (err) {
          alert(err.message);
          select.value = previous;
        })
        .finally(function () {
          select.disabled = false;
        });
    });
    select.dataset.previousValue = select.value;
  });
});
