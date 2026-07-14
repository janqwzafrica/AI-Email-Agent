const CONTENT_KEY = "aiEmailAgent.contentHtml";
const SETTINGS_KEY = "aiEmailAgent.settings";

/**
 * Email content editing: the Edit button on the Content card switches the
 * preview body to contenteditable; it becomes Save, and saving persists the
 * HTML to localStorage (no backend yet). Every screen that includes the
 * shared preview-card partial restores the saved content on load, so an edit
 * made on one step carries through the whole flow.
 */
export function initContentEditor() {
  document.querySelectorAll("[data-edit-toggle]").forEach((button) => {
    const card = button.closest(".preview-card");
    const content = card && card.querySelector("[data-email-content]");
    if (!content) return;

    const saved = localStorage.getItem(CONTENT_KEY);
    if (saved) content.innerHTML = saved;

    let editing = false;

    button.addEventListener("click", () => {
      editing = !editing;

      if (editing) {
        content.setAttribute("contenteditable", "true");
        content.focus();
        button.textContent = "Save";
        button.classList.add("preview-card__edit--saving");
      } else {
        content.removeAttribute("contenteditable");
        localStorage.setItem(CONTENT_KEY, content.innerHTML);
        button.classList.remove("preview-card__edit--saving");
        button.textContent = "Saved ✓";
        setTimeout(() => {
          button.textContent = "Edit";
        }, 1500);
      }
    });
  });
}

/**
 * Campaign settings: restore saved field values wherever the shared settings
 * fields exist, and wire any [data-settings-save] button to persist them.
 */
const SETTINGS_FIELD_IDS = ["sender-email", "sender-name", "email-subject", "email-list"];

export function initSettingsSave() {
  // Restore on every page that renders the fields
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {};
  } catch {
    saved = {};
  }
  SETTINGS_FIELD_IDS.forEach((id) => {
    const field = document.getElementById(id);
    if (field && typeof saved[id] === "string") field.value = saved[id];
  });

  document.querySelectorAll("[data-settings-save]").forEach((button) => {
    button.addEventListener("click", () => {
      const values = {};
      SETTINGS_FIELD_IDS.forEach((id) => {
        const field = document.getElementById(id);
        if (field) values[id] = field.value;
      });
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(values));

      const original = button.textContent;
      button.textContent = "Saved ✓";
      button.disabled = true;
      setTimeout(() => {
        button.textContent = original;
        button.disabled = false;
      }, 1500);
    });
  });
}
