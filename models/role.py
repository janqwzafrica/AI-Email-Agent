from extensions import db
from models.base import BaseEntity


class Role(BaseEntity):
    __tablename__ = "roles"

    NAME_ADMIN = "Admin"
    NAME_STAFF = "Staff"
    DEFAULT_NAMES = (NAME_ADMIN, NAME_STAFF)

    name = db.Column(db.String(50), nullable=False, unique=True, index=True)
