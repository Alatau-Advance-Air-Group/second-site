from __future__ import annotations

import csv
import io
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request


DB_PATH = Path(os.environ.get("REGISTRATIONS_DB_PATH", "/data/registrations.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with closing(get_connection()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                company_name TEXT NOT NULL,
                position TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(registrations)").fetchall()
        }
        if "company_name" not in columns:
            connection.execute(
                """
                ALTER TABLE registrations
                ADD COLUMN company_name TEXT NOT NULL DEFAULT ''
                """
            )
        connection.commit()


def validate_payload(payload: dict[str, str]) -> tuple[dict[str, str], str | None]:
    required_fields = {
        "first_name": "first_name",
        "last_name": "last_name",
        "company_name": "company_name",
        "position": "position",
        "phone": "phone",
        "email": "email",
    }

    cleaned: dict[str, str] = {}
    for source_key, target_key in required_fields.items():
        value = str(payload.get(source_key, "")).strip()
        if not value:
            return {}, f"Field '{source_key}' is required."
        cleaned[target_key] = value

    email = cleaned["email"]
    if "@" not in email or "." not in email.split("@")[-1]:
        return {}, "Email address is invalid."

    return cleaned, None


def insert_registration(connection: sqlite3.Connection, cleaned: dict[str, str]) -> int:
    created_at = datetime.now(timezone.utc).isoformat()
    cursor = connection.execute(
        """
        INSERT INTO registrations (
            first_name,
            last_name,
            company_name,
            position,
            phone,
            email,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cleaned["first_name"],
            cleaned["last_name"],
            cleaned["company_name"],
            cleaned["position"],
            cleaned["phone"],
            cleaned["email"],
            created_at,
        ),
    )
    return int(cursor.lastrowid)


@app.post("/api/register")
def register() -> tuple[object, int]:
    payload = request.get_json(silent=True) or {}
    cleaned, error = validate_payload(payload)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    with closing(get_connection()) as connection:
        registration_id = insert_registration(connection, cleaned)
        connection.commit()

    return jsonify({"ok": True, "registration_id": registration_id}), 201


@app.get("/api/registrations")
def list_registrations() -> object:
    with closing(get_connection()) as connection:
        rows = connection.execute(
            """
            SELECT id, first_name, last_name, company_name, position, phone, email, created_at
            FROM registrations
            ORDER BY id DESC
            """
        ).fetchall()

    return jsonify(
        {
            "ok": True,
            "items": [dict(row) for row in rows],
            "count": len(rows),
        }
    )


@app.post("/api/registrations/delete")
def delete_registrations() -> tuple[object, int]:
    payload = request.get_json(silent=True) or {}
    ids = payload.get("ids")

    if not isinstance(ids, list) or not ids:
        return jsonify({"ok": False, "error": "At least one registration id is required."}), 400

    try:
        normalized_ids = [int(item) for item in ids]
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Registration ids must be integers."}), 400

    placeholders = ",".join("?" for _ in normalized_ids)

    with closing(get_connection()) as connection:
        cursor = connection.execute(
            f"DELETE FROM registrations WHERE id IN ({placeholders})",
            normalized_ids,
        )
        connection.commit()

    return jsonify({"ok": True, "deleted": cursor.rowcount}), 200


@app.post("/api/registrations/import")
def import_registrations() -> tuple[object, int]:
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"ok": False, "error": "CSV file is required."}), 400

    try:
        content = file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify({"ok": False, "error": "CSV must be UTF-8 encoded."}), 400

    reader = csv.DictReader(io.StringIO(content))
    required_columns = {
        "first_name",
        "last_name",
        "company_name",
        "position",
        "phone",
        "email",
    }
    fieldnames = set(reader.fieldnames or [])
    if not required_columns.issubset(fieldnames):
        missing = ", ".join(sorted(required_columns - fieldnames))
        return (
            jsonify(
                {
                    "ok": False,
                    "error": f"Missing required CSV columns: {missing}.",
                }
            ),
            400,
        )

    imported = 0
    errors: list[str] = []

    with closing(get_connection()) as connection:
        for line_number, row in enumerate(reader, start=2):
            cleaned, error = validate_payload(row)
            if error:
                errors.append(f"Line {line_number}: {error}")
                continue

            insert_registration(connection, cleaned)
            imported += 1

        connection.commit()

    return (
        jsonify(
            {
                "ok": True,
                "imported": imported,
                "failed": len(errors),
                "errors": errors[:20],
            }
        ),
        200,
    )


@app.get("/api/health")
def health() -> object:
    return jsonify({"ok": True, "database": str(DB_PATH)})


init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
