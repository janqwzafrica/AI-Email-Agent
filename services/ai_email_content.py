import json

from openai import OpenAI
from flask import current_app

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=current_app.config["OPENAI_API_KEY"])
    return _client


SYSTEM_PROMPT = """You are an email copywriter for a law firm's marketing team. \
Given the extracted text of a source document (e.g. a legal update, article, or \
newsletter draft) and a list of CTA links, generate a polished marketing email.

Requirements:
- Write the body as clean HTML (no <html>/<head>/<body> tags — just the inner
  content, using <p>, <strong>, <ul>/<li> tags as appropriate). Do NOT include
  any <a> tags in the body — CTA buttons are rendered separately by the app.
- Start with a greeting using the placeholder {{ contact.FIRSTNAME }}
- Summarize the source content in a warm, professional tone suitable for clients
- If CTA links are provided, naturally reference that the reader can take
  action (e.g. "you can book a consultation below") without writing a raw URL
- For every CTA link provided, pick a short button label (2-5 words) drawn
  from the source content's own keywords/context — e.g. "Book a Consultation",
  "Read the Full Update". Do not invent a label unrelated to the source content.
- Keep the body concise — this is an email, not the full source document
- Do not invent facts not present in the source document

Respond with ONLY a JSON object of this exact shape:
{"body_html": "<p>...</p>...", "cta_buttons": [{"url": "<the exact CTA url>", "label": "<button label>"}]}
If no CTA links were provided, "cta_buttons" must be an empty list. Every
"url" in "cta_buttons" must be copied exactly from the CTA links given to you.
"""


class AIGenerationError(Exception):
    pass


def generate_email_content(extracted_text, cta_links, sender_name):
    """cta_links: list of normalized URLs (may be empty).

    Returns {"body_html": str, "cta_buttons": [{"url": str, "label": str}, ...]}.
    Every url in cta_links is guaranteed to appear in cta_buttons — falls back
    to a generic label if the model omits or mislabels one.
    """
    user_prompt = f"""Sender / firm name: {sender_name}

CTA links to incorporate:
{chr(10).join(cta_links) if cta_links else "(none provided)"}

Source document text:
\"\"\"
{extracted_text}
\"\"\"
"""

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content if response.choices else None
    except Exception as e:
        raise AIGenerationError(f"AI generation failed: {e}")

    if not raw or not raw.strip():
        raise AIGenerationError("AI returned empty content.")

    try:
        parsed = json.loads(raw)
        body_html = (parsed["body_html"] or "").strip()
    except (ValueError, KeyError, AttributeError, TypeError) as e:
        raise AIGenerationError(f"AI returned malformed content: {e}")

    if not body_html:
        raise AIGenerationError("AI returned empty content.")

    cta_url_set = set(cta_links)
    cta_buttons = [
        {"url": button["url"], "label": (button.get("label") or "Learn More").strip()}
        for button in parsed.get("cta_buttons", []) or []
        if isinstance(button, dict) and button.get("url") in cta_url_set
    ]

    # Guarantee every provided link gets a button even if the model skipped
    # or mislabeled it — this is what actually shows up in the email.
    covered = {b["url"] for b in cta_buttons}
    for url in cta_links:
        if url not in covered:
            cta_buttons.append({"url": url, "label": "Learn More"})

    return {"body_html": body_html, "cta_buttons": cta_buttons}
