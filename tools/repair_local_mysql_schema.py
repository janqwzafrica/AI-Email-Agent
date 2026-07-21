from datetime import datetime, timezone
from pathlib import Path
import sys

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app, db


ADMIN_ROLE_ID = "00000000-0000-4000-8000-000000000001"
STAFF_ROLE_ID = "00000000-0000-4000-8000-000000000002"
HEAD_REVISION = "4b5980412770"


def scalar(sql, **params):
    return db.session.execute(text(sql), params).scalar()


def table_exists(name):
    return (
        scalar(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = :name
            """,
            name=name,
        )
        > 0
    )


def column_exists(table, column):
    return (
        scalar(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = :table
              AND column_name = :column
            """,
            table=table,
            column=column,
        )
        > 0
    )


def column_data_type(table, column):
    return scalar(
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = :table
          AND column_name = :column
        """,
        table=table,
        column=column,
    )


def index_exists(table, index_name):
    return (
        scalar(
            """
            SELECT COUNT(*)
            FROM information_schema.statistics
            WHERE table_schema = DATABASE()
              AND table_name = :table
              AND index_name = :index_name
            """,
            table=table,
            index_name=index_name,
        )
        > 0
    )


def row_count(table):
    return db.session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()


def drop_table_if_exists(table):
    if table_exists(table):
        db.session.execute(text(f"DROP TABLE {table}"))


def rebuild_empty_legacy_user_tables():
    if not table_exists("users"):
        return

    id_type = column_data_type("users", "id")
    if id_type in ("varchar", "char"):
        return

    related_tables = [
        "password_reset_codes",
        "contacts",
        "email_campaigns",
        "test_emails",
    ]
    nonempty_tables = [
        table
        for table in ["users", *related_tables]
        if table_exists(table) and row_count(table) > 0
    ]
    if nonempty_tables:
        raise RuntimeError(
            "Refusing to rebuild legacy user tables because these tables contain "
            f"rows: {', '.join(nonempty_tables)}"
        )

    db.session.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
    for table in related_tables:
        drop_table_if_exists(table)
    drop_table_if_exists("users")
    db.session.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    db.session.commit()


def fk_exists_for_column(table, column):
    return (
        scalar(
            """
            SELECT COUNT(*)
            FROM information_schema.key_column_usage
            WHERE table_schema = DATABASE()
              AND table_name = :table
              AND column_name = :column
              AND referenced_table_name IS NOT NULL
            """,
            table=table,
            column=column,
        )
        > 0
    )


def repair_users_roles():
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if not table_exists("roles"):
        db.session.execute(
            text(
                """
                CREATE TABLE roles (
                    id VARCHAR(36) NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    name VARCHAR(50) NOT NULL,
                    PRIMARY KEY (id),
                    UNIQUE KEY ix_roles_name (name)
                )
                """
            )
        )

    db.session.execute(
        text(
            """
            INSERT INTO roles (id, created_at, updated_at, name)
            VALUES (:admin_id, :now, :now, 'Admin'), (:staff_id, :now, :now, 'Staff')
            ON DUPLICATE KEY UPDATE name = VALUES(name), updated_at = VALUES(updated_at)
            """
        ),
        {"admin_id": ADMIN_ROLE_ID, "staff_id": STAFF_ROLE_ID, "now": now},
    )

    if table_exists("users") and not column_exists("users", "role_id"):
        db.session.execute(text("ALTER TABLE users ADD COLUMN role_id VARCHAR(36) NULL"))
        if column_exists("users", "access_level"):
            db.session.execute(
                text(
                    """
                    UPDATE users
                    SET role_id = CASE
                        WHEN LOWER(access_level) = 'staff' THEN :staff_id
                        ELSE :admin_id
                    END
                    WHERE role_id IS NULL
                    """
                ),
                {"admin_id": ADMIN_ROLE_ID, "staff_id": STAFF_ROLE_ID},
            )
        else:
            db.session.execute(
                text("UPDATE users SET role_id = :admin_id WHERE role_id IS NULL"),
                {"admin_id": ADMIN_ROLE_ID},
            )
        db.session.execute(text("ALTER TABLE users MODIFY role_id VARCHAR(36) NOT NULL"))

    if table_exists("users") and not index_exists("users", "ix_users_role_id"):
        db.session.execute(text("CREATE INDEX ix_users_role_id ON users (role_id)"))

    if table_exists("users") and not fk_exists_for_column("users", "role_id"):
        db.session.execute(
            text(
                """
                ALTER TABLE users
                ADD CONSTRAINT fk_users_role_id_roles
                FOREIGN KEY (role_id) REFERENCES roles(id)
                """
            )
        )

    db.session.commit()


def stamp_head():
    if not table_exists("alembic_version"):
        return

    db.session.execute(text("DELETE FROM alembic_version"))
    db.session.execute(
        text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
        {"head": HEAD_REVISION},
    )
    db.session.commit()


with app.app_context():
    rebuild_empty_legacy_user_tables()
    repair_users_roles()
    db.create_all()
    stamp_head()
    print("schema repair complete")
