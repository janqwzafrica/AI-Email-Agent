document.addEventListener('DOMContentLoaded', function () {
  var contentBody = document.getElementById('contentPanelBody');
  var topEditBtn = document.getElementById('editContentTopBtn');
  var panelEditBtn = document.querySelector('[data-panel-edit-toggle]');

  var isEditable = false;

  function applyEditState() {
    if (!contentBody) return;
    contentBody.setAttribute('data-editable', isEditable ? 'true' : 'false');
    contentBody.setAttribute('contenteditable', isEditable ? 'true' : 'false');
    if (topEditBtn) topEditBtn.textContent = isEditable ? 'Done Editing' : 'Edit Content';
    if (panelEditBtn) panelEditBtn.textContent = isEditable ? 'Done' : 'Edit';
  }

  function toggleEdit() {
    isEditable = !isEditable;
    applyEditState();
  }

  if (topEditBtn) topEditBtn.addEventListener('click', toggleEdit);
  if (panelEditBtn) panelEditBtn.addEventListener('click', toggleEdit);

  var form = document.getElementById('templateForm');
  if (form) {
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      // TODO: replace with real submit once backend save endpoint exists.
      window.location.href = form.dataset.nextUrl || '#';
    });
  }

  applyEditState();
});