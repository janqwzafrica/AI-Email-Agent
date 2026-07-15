document.addEventListener("DOMContentLoaded", function () {
  var form = document.getElementById("aiWizardUploadForm");
  if (!form) return;

  var nextStepBtn = document.getElementById("nextStepBtn");
  var contentInput = document.getElementById("contentUpload");

  var dropzones = form.querySelectorAll("[data-dropzone]");

  dropzones.forEach(function (zone) {
    var targetId = zone.getAttribute("data-target");
    var input = document.getElementById(targetId);
    var filenameEl = zone.querySelector("[data-filename]");
    var browseBtn = zone.querySelector("[data-browse-trigger]");

    function showFile(file) {
      if (!file) return;
      zone.classList.add("has-file");
      filenameEl.hidden = false;
      filenameEl.textContent = file.name;
    }

    // Click anywhere on the dropzone (not just the "upload" link) opens the picker
    zone.addEventListener("click", function (e) {
      input.click();
    });

    // Prevent the inner link click from double-firing the zone's click handler
    if (browseBtn) {
      browseBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        input.click();
      });
    }

    input.addEventListener("change", function () {
      if (input.files && input.files[0]) {
        showFile(input.files[0]);
        updateNextStepState();
      }
    });

    ["dragenter", "dragover"].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        zone.classList.add("is-dragover");
      });
    });

    ["dragleave", "drop"].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        zone.classList.remove("is-dragover");
      });
    });

    zone.addEventListener("drop", function (e) {
      var files = e.dataTransfer.files;
      if (files && files[0]) {
        input.files = files;
        showFile(files[0]);
        updateNextStepState();
      }
    });
  });

  function updateNextStepState() {
    // Required: content file must be attached. Logo is optional.
    var hasContent = contentInput.files && contentInput.files.length > 0;
    nextStepBtn.disabled = !hasContent;
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if (nextStepBtn.disabled) return;

    var formData = new FormData(form);
    nextStepBtn.disabled = true;
    nextStepBtn.textContent = "Uploading...";

    fetch("/campaigns/ai-wizard/upload", {
      method: "POST",
      body: formData,
    })
      .then(function (res) {
        if (!res.ok)
          return res.json().then(function (data) {
            throw new Error(data.error || "Upload failed");
          });
        return res.json();
      })
      .then(function (data) {
        window.location.href = data.next_url || form.dataset.nextUrl || "#";
      })
      .catch(function (err) {
        alert(err.message);
        nextStepBtn.disabled = false;
        nextStepBtn.textContent = "Next Step";
      });
  });

  updateNextStepState();
});
