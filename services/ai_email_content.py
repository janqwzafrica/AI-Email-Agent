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
newsletter draft) and a list of CTA links, generate a polished marketing email body \
as clean HTML (no <html>/<head>/<body> tags — just the inner content, using <p>, \
<strong>, <ul>/<li>, and <a> tags as appropriate).

Requirements:
- Start with a greeting using the placeholder {{ contact.FIRSTNAME }}
- Summarize the source content in a warm, professional tone suitable for clients
- Include at least one clear call-to-action button/link using the provided CTA links
- Keep it concise — this is an email, not the full source document
- Do not invent facts not present in the source document
- Output ONLY the HTML fragment, no markdown code fences, no commentary
"""


class AIGenerationError(Exception):
    pass


def generate_email_content(extracted_text, cta_links, sender_name):
    user_prompt = f"""Sender / firm name: {sender_name}

CTA links to incorporate:
{cta_links or "(none provided)"}

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
        )
        content = response.choices[0].message.content if response.choices else None
    except AIGenerationError:
        raise
    except Exception as e:
        raise AIGenerationError(f"AI generation failed: {e}")

    if not content or not content.strip():
        raise AIGenerationError("AI returned empty content.")

    return content.strip()