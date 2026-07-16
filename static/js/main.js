import { initDropdowns } from "./modules/dropdown.js";
import { initCalendars } from "./modules/calendar.js";
import { initContentEditor, initSettingsSave } from "./modules/editor.js";
import { initPasswordToggles } from "./modules/password-toggle.js";
import { initAuthFormLoading } from "./modules/auth-form.js";
import { initPasswordResetCodeSender } from "./modules/password-reset.js";
import { initTableTools } from "./modules/table-tools.js";

document.addEventListener("DOMContentLoaded", () => {
  initDropdowns();
  initCalendars();
  initContentEditor();
  initSettingsSave();
  initPasswordToggles();
  initAuthFormLoading();
  initPasswordResetCodeSender();
  initTableTools();
});
