# AI / Manual Email Wizard — how it works

For anyone (hi Ganiyu) working on the Create Campaign flow. This covers where everything lives and the one place AI and Manual mode actually differ.

## The backbone: `EmailCampaign`

There is no more "draft" concept living in memory. Every campaign — from the moment someone uploads a file at Step 1, through to being sent — is a single row in the `email_campaigns` table (`models/email_campaign.py`). The row currently being edited is tracked with `session["campaign_id"]`.

Two helpers in `app.py` manage this:
- `_get_or_create_campaign(mode)` — called at Step 1. Reuses the campaign already in the session if it's still in an unsent state (`draft`/`generating`/`ready`), otherwise creates a new row.
- `get_current_campaign()` — called by every later step to load `session["campaign_id"]` back out. If there's no campaign in the session, the route redirects back to an earlier step instead of crashing.

`EmailCampaign.mode` is `"ai"` or `"manual"` — set once at Step 1 and never changes for that campaign.

## The four steps

All routes live in `app.py`, all templates in `templates/wizard_*.html`, all JS in `static/js/wizard_*.js`.

| Step | Route(s) | Template | JS |
|---|---|---|---|
| 1. Setup | `campaign_wizard(mode)` — GET+POST, line ~671 | `wizard_setup.html` | `wizard_setup.js` |
| 2. Template | `wizard_template(mode)` (line ~725), `wizard_template_status(mode)` (line ~758), `wizard_template_save(mode)` (line ~770) | `wizard_template.html` | `wizard_template.js` |
| 3. Test Send | `wizard_test_send(mode)` (line ~823), `wizard_test_send_action(mode)` (line ~845) | `wizard_test_send.html` | `wizard_test_send.js` |
| 4. Schedule | `wizard_schedule(mode)` (line ~937), `wizard_schedule_action(mode)` (line ~960) | `wizard_schedule.html` | `wizard_schedule.js` |

**Step 1 (Setup)** — POST handler extracts text from the uploaded doc (`services/document_extractor.py`), saves the logo file to disk, and stores `extracted_text`/`cta_links`/`logo_url` on the campaign row. Same code path for both modes — mode only decides what happens next, at Step 2.

**Step 2 (Template)** — this is the *only* place AI and Manual diverge. See below.

**Step 3 (Test Send)** — same code for both modes. Requires `campaign.brevo_campaign_id` to already be set (i.e. Step 2 must have been completed — a real Brevo campaign gets created there). Pulls contacts from a Brevo list (defaults to one named "Test Emails" if it exists) and lets the user pick a subset to send to via `send_test_email()`.

**Step 4 (Schedule)** — same code for both modes. Three actions posted from the UI (`run_now` / `schedule` / `finish`), handled in `wizard_schedule_action`. `schedule` converts the browser's naive `datetime-local` value to the server's local timezone before sending it to Brevo (Brevo requires a tz-aware ISO timestamp) — see the comment right above that conversion in `wizard_schedule_action` if you're touching it.

## Where AI vs Manual actually diverge

Everything happens in `wizard_template(mode)` (`app.py` ~line 725):

```python
if mode == EmailCampaign.MODE_AI and not campaign.email_content and not campaign.is_generating:
    _start_generation(campaign)
elif mode == EmailCampaign.MODE_MANUAL and campaign.email_content is None:
    campaign.email_content = f"<p>{campaign.extracted_text}</p>" + _feedback_footer_html()
```

- **AI**: kicks off `_start_generation()` → spawns a background thread running `_run_generation()`, which calls `generate_email_content()` (`services/ai_email_content.py`, the actual OpenAI call) and writes the result into `campaign.email_content`. The page polls `wizard_template_status(mode)` every 2s (see `wizard_template.js`) until `is_generating` flips false, then swaps the generated HTML into the content panel.
- **Manual**: no AI call at all. The raw extracted text from the uploaded document gets wrapped in a `<p>` and dropped straight into `campaign.email_content`, editable immediately.

Both paths append `_feedback_footer_html()` (app.py ~line 90) — the "Yes I'm interested / Not right now" links used for auto-classification (see below). This only happens once per campaign; both branches are guarded by "only if `email_content` is still unset," so it can't double-append.

After content exists (either way), Step 2 also has a settings form (sender/subject/list) that POSTs to `wizard_template_save`. **The first time** that saves, it calls `create_email_campaign()` on Brevo and stores the returned id as `campaign.brevo_campaign_id` — that id is what Steps 3 and 4 act on. If you go back and change settings later, it calls `update_email_campaign()` instead of creating a duplicate.

If you need to change what the AI writes, edit the prompt in `services/ai_email_content.py` — nothing in `app.py` needs to change for that.

## Auto-classification (separate feature, worth knowing about)

Every campaign's content ends with two links (interested / not interested) pointing at `/feedback/interested` and `/feedback/not-interested` (public pages, no login). When a recipient clicks one, Brevo's webhook (`brevo_webhook` in `app.py`) fires an `event=click` payload, and the handler matches the clicked URL against `FEEDBACK_LINK_CLASSIFICATIONS` to set `Contact.classification` automatically — no staff member touches the Recipients page dropdown for this. See `PUBLIC_BASE_URL` in `.env` if the links are pointing at the wrong domain.

## Common things you might want to change

- **Add a field to the wizard** (e.g. a new Step 1 input): add the form field to `wizard_setup.html`, read it in `campaign_wizard`'s POST branch, add a column to `EmailCampaign` if it needs to persist (new model field → `flask db migrate` → `flask db upgrade`).
- **Change what happens after generation** (AI mode): edit `_run_generation()`.
- **Change the Manual mode starting content**: edit the one line in `wizard_template()`'s `elif mode == EmailCampaign.MODE_MANUAL` branch.
- **Debug a stuck "generating" state**: `_run_generation` always sets `campaign.is_generating = False` in a `finally` block, even on unexpected errors — it should never hang. If it does, something's swallowing an exception before that point.
