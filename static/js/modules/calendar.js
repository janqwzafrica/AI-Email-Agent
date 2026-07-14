const MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

function sameDay(a, b) {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

export function initCalendars() {
  document.querySelectorAll("[data-calendar]").forEach((root) => {
    const label = root.querySelector("[data-calendar-label]");
    const grid = root.querySelector("[data-calendar-grid]");
    if (!label || !grid) return;

    const today = new Date();
    let viewYear = today.getFullYear();
    let viewMonth = today.getMonth();
    let selected = null;

    function render() {
      label.textContent = `${MONTH_NAMES[viewMonth]} ${viewYear}`;
      grid.innerHTML = "";

      const firstOfMonth = new Date(viewYear, viewMonth, 1);
      const cursor = new Date(firstOfMonth);
      cursor.setDate(cursor.getDate() - cursor.getDay()); // back to Sunday

      const lastOfMonth = new Date(viewYear, viewMonth + 1, 0);

      while (cursor <= lastOfMonth || cursor.getDay() !== 0) {
        const row = document.createElement("tr");
        for (let i = 0; i < 7; i += 1) {
          const cell = document.createElement("td");
          const day = document.createElement("span");
          const inMonth = cursor.getMonth() === viewMonth;

          day.className = "calendar__day";
          day.textContent = cursor.getDate();

          // Past days can be browsed (prev-month nav stays enabled) but
          // never picked — you can't schedule a campaign in the past.
          const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
          const isPast = cursor < startOfToday;

          if (!inMonth) {
            day.classList.add("calendar__day--muted");
          } else if (isPast) {
            day.classList.add("calendar__day--muted", "calendar__day--disabled");
          } else {
            if (sameDay(cursor, today)) day.classList.add("calendar__day--today");
            if (selected && sameDay(cursor, selected)) {
              day.classList.add("calendar__day--selected");
            }
            const date = new Date(cursor);
            day.addEventListener("click", () => {
              selected = date;
              render();
            });
          }

          cell.appendChild(day);
          row.appendChild(cell);
          cursor.setDate(cursor.getDate() + 1);
        }
        grid.appendChild(row);
      }
    }

    root.querySelector("[data-calendar-prev]")?.addEventListener("click", () => {
      viewMonth -= 1;
      if (viewMonth < 0) { viewMonth = 11; viewYear -= 1; }
      render();
    });

    root.querySelector("[data-calendar-next]")?.addEventListener("click", () => {
      viewMonth += 1;
      if (viewMonth > 11) { viewMonth = 0; viewYear += 1; }
      render();
    });

    root.querySelectorAll("[data-meridiem]").forEach((btn) => {
      btn.addEventListener("click", () => {
        root
          .querySelectorAll("[data-meridiem]")
          .forEach((b) => b.classList.remove("calendar__meridiem--active"));
        btn.classList.add("calendar__meridiem--active");
      });
    });

    // Time input: accept "9", "930", "9:30", "09.30" etc. and normalize to
    // a valid 12-hour "hh:mm" on blur/Enter; revert to last valid on garbage.
    const timeInput = root.querySelector(".calendar__time-input");
    if (timeInput) {
      let lastValid = timeInput.value || "09:00";

      const normalize = (raw) => {
        const digits = raw.replace(/\D/g, "");
        if (!digits) return null;
        let hours;
        let minutes;
        if (digits.length <= 2) {
          hours = Number(digits);
          minutes = 0;
        } else {
          hours = Number(digits.slice(0, digits.length - 2));
          minutes = Number(digits.slice(-2));
        }
        if (hours < 1 || hours > 12 || minutes > 59) return null;
        return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
      };

      const commit = () => {
        const formatted = normalize(timeInput.value);
        if (formatted) lastValid = formatted;
        timeInput.value = lastValid;
      };

      timeInput.addEventListener("blur", commit);
      timeInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          commit();
          timeInput.blur();
        }
      });
    }

    render();
  });
}
