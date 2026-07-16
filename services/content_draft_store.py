import uuid
from threading import Lock

_drafts = {}
_lock = Lock()


def create_draft():
    draft_id = uuid.uuid4().hex
    with _lock:
        _drafts[draft_id] = {
            "content_file_bytes": None,
            "content_file_name": None,
            "extracted_text": None,
            "cta_links": "",
            "logo_url": None,
            "email_content": None,
            "is_generating": False,
            "sender_email": None,
            "sender_name": None,
            "email_subject": None,
            "email_list": None,
        }
    return draft_id


def get_draft(draft_id):
    if not draft_id:
        return None
    return _drafts.get(draft_id)


def update_draft(draft_id, **fields):
    with _lock:
        draft = _drafts.get(draft_id)
        if draft is None:
            return None
        draft.update(fields)
        return draft


def delete_draft(draft_id):
    with _lock:
        _drafts.pop(draft_id, None)
