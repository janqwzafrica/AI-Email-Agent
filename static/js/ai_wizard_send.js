document.addEventListener('DOMContentLoaded', () => {
  const sendBtn = document.getElementById('sendTestEmailBtn');
  const sentPill = document.getElementById('testSentPill');
  const emailListSelect = document.getElementById('testEmailList');
  const completeBtn = document.getElementById('completeSetupBtn');

  if (sendBtn) {
    sendBtn.addEventListener('click', async () => {
      const listId = emailListSelect ? emailListSelect.value : null;

      sendBtn.disabled = true;
      sendBtn.textContent = 'Sending...';

      try {
        const response = await fetch('/campaigns/ai-wizard/test-send/send', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ test_email_list: listId }),
        });

        if (!response.ok) {
          throw new Error('Failed to send test email');
        }

        if (sentPill) {
          sentPill.hidden = false;
        }
      } catch (err) {
        console.error(err);
        alert('Something went wrong sending the test email. Please try again.');
      } finally {
        sendBtn.disabled = false;
        sendBtn.textContent = 'Send Test Email';
      }
    });
  }

  if (completeBtn) {
    completeBtn.addEventListener('click', () => {
      const nextUrl = completeBtn.dataset.nextUrl;
      if (nextUrl) {
        window.location.href = nextUrl;
      }
    });
  }
});