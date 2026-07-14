import { initDropdowns } from "./modules/dropdown.js";
import { initCalendars } from "./modules/calendar.js";
import { initContentEditor, initSettingsSave } from "./modules/editor.js";

document.addEventListener("DOMContentLoaded", () => {
  initDropdowns();
  initCalendars();
  initContentEditor();
  initSettingsSave();
});
