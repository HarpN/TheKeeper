from __future__ import annotations

import json
import sqlite3

from scripts.bootstrap_keeper import initialize_keeper_tables
from scripts.discrepancy_workflow import resolve_discrepancy, scan_discrepancies


def _seed_game_and_snapshot(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO keeper_games (
            source_agent, game_title, platform, completion_rate, trophy_count, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("sly", "Astro Bot", "PS5", 80.0, 120, "2026-07-11T00:00:00+00:00"),
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO keeper_snapshots (
            entity_type, entity_key, version_label, payload_json, created_at
        ) VALUES ('game', ?, 'LATEST', ?, ?)
        """,
        (
            "Astro Bot::PS5",
            json.dumps({"completion_rate": 70.0, "trophy_count": 111}, separators=(",", ":")),
            "2026-07-11T00:00:00+00:00",
        ),
    )


def test_scan_creates_pending_discrepancies(tmp_path) -> None:
    db_path = tmp_path / "keeper.db"
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        initialize_keeper_tables(connection)
        _seed_game_and_snapshot(connection)

        created_ids = scan_discrepancies(connection)
        assert len(created_ids) == 2

        rows = connection.execute(
            "SELECT discrepancy_type, status FROM keeper_discrepancies ORDER BY discrepancy_type"
        ).fetchall()

    assert [row["discrepancy_type"] for row in rows] == [
        "completion_rate_mismatch",
        "trophy_count_mismatch",
    ]
    assert all(row["status"] == "PENDING_USER" for row in rows)


def test_confirmed_resolution_updates_latest_snapshot(tmp_path) -> None:
    db_path = tmp_path / "keeper.db"
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        initialize_keeper_tables(connection)
        _seed_game_and_snapshot(connection)

        created_ids = scan_discrepancies(connection)
        completion_id = connection.execute(
            """
            SELECT id
            FROM keeper_discrepancies
            WHERE discrepancy_type = 'completion_rate_mismatch'
            """
        ).fetchone()["id"]

        result = resolve_discrepancy(connection, int(completion_id), "CONFIRMED")
        assert result["applied"] is True

        snapshot = connection.execute(
            """
            SELECT payload_json
            FROM keeper_snapshots
            WHERE entity_type = 'game' AND entity_key = 'Astro Bot::PS5' AND version_label = 'LATEST'
            """
        ).fetchone()
        payload = json.loads(snapshot["payload_json"])

        status = connection.execute(
            "SELECT status FROM keeper_discrepancies WHERE id = ?",
            (int(completion_id),),
        ).fetchone()["status"]

    assert len(created_ids) == 2
    assert payload["completion_rate"] == 80.0
    assert status == "CONFIRMED"


def test_dismissed_resolution_does_not_modify_snapshot(tmp_path) -> None:
    db_path = tmp_path / "keeper.db"
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        initialize_keeper_tables(connection)
        _seed_game_and_snapshot(connection)

        scan_discrepancies(connection)
        trophy_id = connection.execute(
            """
            SELECT id
            FROM keeper_discrepancies
            WHERE discrepancy_type = 'trophy_count_mismatch'
            """
        ).fetchone()["id"]

        result = resolve_discrepancy(connection, int(trophy_id), "DISMISSED")
        assert result["applied"] is False

        snapshot = connection.execute(
            """
            SELECT payload_json
            FROM keeper_snapshots
            WHERE entity_type = 'game' AND entity_key = 'Astro Bot::PS5' AND version_label = 'LATEST'
            """
        ).fetchone()
        payload = json.loads(snapshot["payload_json"])

        status = connection.execute(
            "SELECT status FROM keeper_discrepancies WHERE id = ?",
            (int(trophy_id),),
        ).fetchone()["status"]

    assert payload["trophy_count"] == 111
    assert status == "DISMISSED"
