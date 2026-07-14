document.addEventListener('DOMContentLoaded', () => {
  const openCalendarBtn = document.getElementById('openCalendarBtn');
  const calendarModal = document.getElementById('calendarModal');
  const calendarBackdrop = document.getElementById('calendarBackdrop');

  // Open the modal when the "Schedule" button is clicked
  if (openCalendarBtn && calendarModal) {
    openCalendarBtn.addEventListener('click', () => {
      calendarModal.hidden = false;
    });
  }

  // Close the modal when clicking the dark backdrop area
  if (calendarBackdrop && calendarModal) {
    calendarBackdrop.addEventListener('click', () => {
      calendarModal.hidden = true;
    });
  }
});