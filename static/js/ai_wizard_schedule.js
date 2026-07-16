document.addEventListener('DOMContentLoaded', function () {
  var openCalendarBtn = document.getElementById('openCalendarBtn');
  var calendarModal = document.getElementById('calendarModal');
  var calendarBackdrop = document.getElementById('calendarBackdrop');
  var monthYearEl = document.getElementById('calendarMonthYear');
  var gridEl = document.getElementById('calendarGrid');
  var prevBtn = document.getElementById('calendarPrevMonth');
  var nextBtn = document.getElementById('calendarNextMonth');
  var timeInput = document.getElementById('calendarTimeInput');
  var ampmBtns = document.querySelectorAll('.ampm-btn');
  var confirmBtn = document.getElementById('calendarConfirmBtn');
  var runNowBtn = document.getElementById('runNowBtn');
  var finishBtn = document.getElementById('finishBtn');
  var statusPill = document.getElementById('scheduleStatusPill');

  var today = new Date();
  var viewYear = today.getFullYear();
  var viewMonth = today.getMonth(); // 0-indexed
  var selectedDate = null;
  var selectedAmPm = 'AM';

  var MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  function renderCalendar() {
    monthYearEl.textContent = MONTH_NAMES[viewMonth] + ' ' + viewYear;

    // Remove old date cells (keep the 7 day-name headers)
    var dateCells = gridEl.querySelectorAll('.calendar-date');
    dateCells.forEach(function (cell) { cell.remove(); });

    var firstOfMonth = new Date(viewYear, viewMonth, 1);
    var startWeekday = firstOfMonth.getDay(); // 0 = Sunday
    var daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    var daysInPrevMonth = new Date(viewYear, viewMonth, 0).getDate();

    var cells = [];

    // Leading muted days from previous month
    for (var i = startWeekday - 1; i >= 0; i--) {
      cells.push({ day: daysInPrevMonth - i, muted: true });
    }
    // Current month days
    for (var d = 1; d <= daysInMonth; d++) {
      cells.push({ day: d, muted: false });
    }
    // Trailing muted days to fill the final week row
    while (cells.length % 7 !== 0) {
      cells.push({ day: cells.length - (startWeekday + daysInMonth) + 1, muted: true });
    }

    cells.forEach(function (cellData) {
      var cell = document.createElement('div');
      cell.className = 'calendar-date' + (cellData.muted ? ' muted' : '');
      cell.textContent = cellData.day;

      if (!cellData.muted) {
        var cellDate = new Date(viewYear, viewMonth, cellData.day);
        if (
          selectedDate &&
          selectedDate.getFullYear() === cellDate.getFullYear() &&
          selectedDate.getMonth() === cellDate.getMonth() &&
          selectedDate.getDate() === cellDate.getDate()
        ) {
          cell.classList.add('selected');
        }

        cell.addEventListener('click', function () {
          selectedDate = cellDate;
          renderCalendar();
        });
      }

      gridEl.appendChild(cell);
    });
  }

  if (prevBtn) {
    prevBtn.addEventListener('click', function () {
      viewMonth -= 1;
      if (viewMonth < 0) { viewMonth = 11; viewYear -= 1; }
      renderCalendar();
    });
  }

  if (nextBtn) {
    nextBtn.addEventListener('click', function () {
      viewMonth += 1;
      if (viewMonth > 11) { viewMonth = 0; viewYear += 1; }
      renderCalendar();
    });
  }

  ampmBtns.forEach(function (btn) {
    btn.addEventListener('click', function () {
      ampmBtns.forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      selectedAmPm = btn.dataset.ampm;
    });
  });

  if (openCalendarBtn && calendarModal) {
    openCalendarBtn.addEventListener('click', function () {
      renderCalendar();
      calendarModal.hidden = false;
    });
  }

  if (calendarBackdrop && calendarModal) {
    calendarBackdrop.addEventListener('click', function () {
      calendarModal.hidden = true;
    });
  }

  function buildScheduledIso() {
    if (!selectedDate) return null;
    var timeParts = (timeInput.value || '09:00').split(':');
    var hours = parseInt(timeParts[0], 10) || 9;
    var minutes = parseInt(timeParts[1], 10) || 0;

    if (selectedAmPm === 'PM' && hours < 12) hours += 12;
    if (selectedAmPm === 'AM' && hours === 12) hours = 0;

    var combined = new Date(
      selectedDate.getFullYear(),
      selectedDate.getMonth(),
      selectedDate.getDate(),
      hours,
      minutes
    );
    return combined.toISOString();
  }

  function postAction(action, extra) {
    var payload = Object.assign({ action: action }, extra || {});
    return fetch('/campaigns/ai-wizard/schedule/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(function (res) {
      if (!res.ok) throw new Error('Action failed');
      return res.json();
    });
  }

  if (confirmBtn) {
    confirmBtn.addEventListener('click', function () {
      var scheduledIso = buildScheduledIso();
      if (!scheduledIso) {
        alert('Please select a date first.');
        return;
      }
      postAction('schedule', { scheduled_at: scheduledIso })
        .then(function (data) {
          if (statusPill) {
            statusPill.textContent = 'Scheduled for ' + new Date(data.scheduled_at).toLocaleString();
          }
          calendarModal.hidden = true;
        })
        .catch(function () {
          alert('Something went wrong scheduling this campaign. Please try again.');
        });
    });
  }

  if (runNowBtn) {
    runNowBtn.addEventListener('click', function () {
      runNowBtn.disabled = true;
      postAction('run_now')
        .then(function () {
          if (statusPill) statusPill.textContent = 'Sent';
        })
        .catch(function () {
          alert('Something went wrong sending this campaign. Please try again.');
        })
        .finally(function () {
          runNowBtn.disabled = false;
        });
    });
  }

  if (finishBtn) {
    finishBtn.addEventListener('click', function () {
      finishBtn.disabled = true;
      postAction('finish')
        .then(function (data) {
          window.location.href = data.next_url || '/dashboard';
        })
        .catch(function () {
          alert('Something went wrong finishing this campaign. Please try again.');
          finishBtn.disabled = false;
        });
    });
  }
});