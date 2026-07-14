import { initDropdowns } from "./modules/dropdown.js";
import { initCalendars } from "./modules/calendar.js";
import { initContentEditor, initSettingsSave } from "./modules/editor.js";
import { initPasswordToggles } from "./modules/password-toggle.js";

document.addEventListener("DOMContentLoaded", () => {
  initDropdowns();
  initCalendars();
  initContentEditor();
  initSettingsSave();
  initPasswordToggles();
});
