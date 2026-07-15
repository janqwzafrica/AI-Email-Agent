from datetime import datetime, timezone
from uuid import uuid4

from extensions import db


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def generate_uuid():
    return str(uuid4())


class BaseEntity(db.Model):
    __abstract__ = True

    id = db.Column(db.String(36), primary_key=True, default=generate_uuid)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
