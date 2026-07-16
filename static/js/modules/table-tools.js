// Client-side search, filtering, and date sorting for data tables.
//
// Hooks:
//   <input data-table-search="#table" data-search-cols="0,1">  — live text search
//   <button data-sort-date="#table" data-col="3">              — toggle newest/oldest
//   <button data-cycle-blacklist="#table" data-col="4">        — All / Blacklisted / Not
//
// Buttons need a <span data-label> so the label can change without
// clobbering the icon.

function dataRows(table) {
  // Skip empty-state rows (single full-width cell).
  return [...table.querySelectorAll("tbody tr")].filter(
    (tr) => tr.querySelectorAll("td").length > 1
  );
}

function applyHidden(tr) {
  tr.hidden = tr.dataset.hideSearch === "1" || tr.dataset.hideFilter === "1";
}

function parseDate(text) {
  const [m, d, y] = (text || "").trim().split("/").map(Number);
  const time = new Date(y, (m || 1) - 1, d || 1).getTime();
  return Number.isFinite(time) ? time : 0;
}

export function initTableTools() {
  document.querySelectorAll("[data-table-search]").forEach((input) => {
    const table = document.querySelector(input.dataset.tableSearch);
    if (!table) return;
    const cols = (input.dataset.searchCols || "")
      .split(",")
      .filter(Boolean)
      .map(Number);

    input.addEventListener("input", () => {
      const query = input.value.trim().toLowerCase();
      dataRows(table).forEach((tr) => {
        const cells = tr.querySelectorAll("td");
        const haystack = (
          cols.length
            ? cols.map((i) => cells[i]?.textContent || "").join(" ")
            : tr.textContent
        ).toLowerCase();
        tr.dataset.hideSearch = query && !haystack.includes(query) ? "1" : "";
        applyHidden(tr);
      });
    });
  });

  document.querySelectorAll("[data-sort-date]").forEach((button) => {
    const table = document.querySelector(button.dataset.sortDate);
    if (!table) return;
    const col = Number(button.dataset.col || 0);
    const label = button.querySelector("[data-label]");
    let newestFirst = null;

    button.addEventListener("click", () => {
      newestFirst = newestFirst === null ? true : !newestFirst;
      const tbody = table.querySelector("tbody");
      dataRows(table)
        .sort((a, b) => {
          const diff =
            parseDate(a.querySelectorAll("td")[col]?.textContent) -
            parseDate(b.querySelectorAll("td")[col]?.textContent);
          return newestFirst ? -diff : diff;
        })
        .forEach((tr) => tbody.appendChild(tr));
      if (label) label.textContent = newestFirst ? "Date: Newest" : "Date: Oldest";
    });
  });

  document.querySelectorAll("[data-cycle-blacklist]").forEach((button) => {
    const table = document.querySelector(button.dataset.cycleBlacklist);
    if (!table) return;
    const col = Number(button.dataset.col || 0);
    const label = button.querySelector("[data-label]");
    const states = ["All", "Blacklisted", "Not Blacklisted"];
    let state = 0;

    button.addEventListener("click", () => {
      state = (state + 1) % states.length;
      dataRows(table).forEach((tr) => {
        const isBlacklisted = (
          tr.querySelectorAll("td")[col]?.textContent || ""
        ).includes("Blacklisted");
        const hide =
          (state === 1 && !isBlacklisted) || (state === 2 && isBlacklisted);
        tr.dataset.hideFilter = hide ? "1" : "";
        applyHidden(tr);
      });
      if (label) {
        label.textContent =
          state === 0 ? "Filter By Blacklisted" : `Blacklisted: ${states[state]}`;
      }
    });
  });
}
