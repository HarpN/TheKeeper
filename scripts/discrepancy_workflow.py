from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.bootstrap_keeper import initialize_keeper_tables
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from bootstrap_keeper import initialize_keeper_tables


VALID_STATUSES = {"PENDING_USER", "CONFIRMED", "DISMISSED"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _upsert_pending_discrepancy(
    connection: sqlite3.Connection,
    *,
    game_title: str,
    platform: str,
    discrepancy_type: str,
    expected_value: str,
    observed_value: str,
    suggested_resolution: str,
) -> int:
    existing = connection.execute(
        """
        SELECT id
        FROM keeper_discrepancies
        WHERE game_title = ?
          AND platform = ?
          AND discrepancy_type = ?
          AND status = 'PENDING_USER'
        ORDER BY id DESC
        LIMIT 1
        """,
        (game_title, platform, discrepancy_type),
    ).fetchone()

    if existing is not None:
        connection.execute(
            """
            UPDATE keeper_discrepancies
            SET expected_value = ?, observed_value = ?, suggested_resolution = ?, created_at = ?
            WHERE id = ?
            """,
            (expected_value, observed_value, suggested_resolution, now_iso(), int(existing["id"])),
        )
        return int(existing["id"])

    cursor = connection.execute(
        """
        INSERT INTO keeper_discrepancies (
            game_title, platform, discrepancy_type, expected_value,
            observed_value, status, suggested_resolution, created_at
        ) VALUES (?, ?, ?, ?, ?, 'PENDING_USER', ?, ?)
        """,
        (game_title, platform, discrepancy_type, expected_value, observed_value, suggested_resolution, now_iso()),
    )
    return int(cursor.lastrowid)


def _latest_game_snapshot(connection: sqlite3.Connection, game_title: str, platform: str) -> dict[str, Any] | None:
    entity_key = f"{game_title}::{platform}"
    row = connection.execute(
        """
        SELECT payload_json
        FROM keeper_snapshots
        WHERE entity_type = 'game' AND entity_key = ? AND version_label = 'LATEST'
        """,
        (entity_key,),
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(str(row["payload_json"]))
    except json.JSONDecodeError:
        return None


def scan_discrepancies(connection: sqlite3.Connection, completion_tolerance: float = 0.01) -> list[int]:
    initialize_keeper_tables(connection)

    created_ids: list[int] = []
    games = connection.execute(
        """
        SELECT game_title, platform, completion_rate, trophy_count
        FROM keeper_games
        """
    ).fetchall()

    for game in games:
        game_title = str(game["game_title"])
        platform = str(game["platform"])
        snapshot = _latest_game_snapshot(connection, game_title, platform)

        if snapshot is None:
            created_ids.append(
                _upsert_pending_discrepancy(
                    connection,
                    game_title=game_title,
                    platform=platform,
                    discrepancy_type="missing_latest_snapshot",
                    expected_value="present",
                    observed_value="missing",
                    suggested_resolution="Confirm creating a fresh game snapshot from current game state.",
                )
            )
            continue

        expected_completion = float(game["completion_rate"])
        observed_completion = float(snapshot.get("completion_rate", 0.0))
        if abs(expected_completion - observed_completion) > completion_tolerance:
            created_ids.append(
                _upsert_pending_discrepancy(
                    connection,
                    game_title=game_title,
                    platform=platform,
                    discrepancy_type="completion_rate_mismatch",
                    expected_value=f"{expected_completion}",
                    observed_value=f"{observed_completion}",
                    suggested_resolution="Confirm and update snapshot completion_rate from keeper_games baseline.",
                )
            )

        expected_trophies = int(game["trophy_count"])
        observed_trophies = int(snapshot.get("trophy_count", 0))
        if expected_trophies != observed_trophies:
            created_ids.append(
                _upsert_pending_discrepancy(
                    connection,
                    game_title=game_title,
                    platform=platform,
                    discrepancy_type="trophy_count_mismatch",
                    expected_value=f"{expected_trophies}",
                    observed_value=f"{observed_trophies}",
                    suggested_resolution="Confirm and update snapshot trophy_count from keeper_games baseline.",
                )
            )

    return created_ids


def _apply_confirmed_resolution(connection: sqlite3.Connection, row: sqlite3.Row) -> bool:
    discrepancy_type = str(row["discrepancy_type"])
    game_title = str(row["game_title"])
    platform = str(row["platform"])

    if discrepancy_type == "missing_latest_snapshot":
        game = connection.execute(
            """
            SELECT completion_rate, trophy_count
            FROM keeper_games
            WHERE game_title = ? AND platform = ?
            """,
            (game_title, platform),
        ).fetchone()
        if game is None:
            return False
        payload = {
            "completion_rate": float(game["completion_rate"]),
            "trophy_count": int(game["trophy_count"]),
            "source": "discrepancy_resolution",
        }
        connection.execute(
            """
            INSERT OR REPLACE INTO keeper_snapshots (
                entity_type, entity_key, version_label, payload_json, created_at
            ) VALUES ('game', ?, 'LATEST', ?, ?)
            """,
            (f"{game_title}::{platform}", json.dumps(payload, separators=(",", ":")), now_iso()),
        )
        return True

    entity_key = f"{game_title}::{platform}"
    snapshot_row = connection.execute(
        """
        SELECT payload_json
        FROM keeper_snapshots
        WHERE entity_type = 'game' AND entity_key = ? AND version_label = 'LATEST'
        """,
        (entity_key,),
    ).fetchone()
    if snapshot_row is None:
        return False

    try:
        payload = json.loads(str(snapshot_row["payload_json"]))
    except json.JSONDecodeError:
        return False

    if discrepancy_type == "completion_rate_mismatch":
        payload["completion_rate"] = float(row["expected_value"])
    elif discrepancy_type == "trophy_count_mismatch":
        payload["trophy_count"] = int(float(row["expected_value"]))
    else:
        return False

    payload["source"] = "discrepancy_resolution"
    connection.execute(
        """
        INSERT OR REPLACE INTO keeper_snapshots (
            entity_type, entity_key, version_label, payload_json, created_at
        ) VALUES ('game', ?, 'LATEST', ?, ?)
        """,
        (entity_key, json.dumps(payload, separators=(",", ":")), now_iso()),
    )
    return True


def resolve_discrepancy(connection: sqlite3.Connection, discrepancy_id: int, decision: str) -> dict[str, Any]:
    initialize_keeper_tables(connection)
    if decision not in {"CONFIRMED", "DISMISSED"}:
        raise ValueError("decision must be CONFIRMED or DISMISSED")

    row = connection.execute(
        """
        SELECT *
        FROM keeper_discrepancies
        WHERE id = ?
        """,
        (discrepancy_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"discrepancy id {discrepancy_id} was not found")

    applied = False
    if decision == "CONFIRMED":
        applied = _apply_confirmed_resolution(connection, row)

    connection.execute(
        """
        UPDATE keeper_discrepancies
        SET status = ?
        WHERE id = ?
        """,
        (decision, discrepancy_id),
    )

    return {
        "id": discrepancy_id,
        "status": decision,
        "applied": applied,
    }


def list_discrepancies(connection: sqlite3.Connection, status: str | None = None) -> list[dict[str, Any]]:
    initialize_keeper_tables(connection)
    if status is not None and status not in VALID_STATUSES:
        raise ValueError("status filter must be PENDING_USER, CONFIRMED, or DISMISSED")

    if status is None:
        rows = connection.execute("SELECT * FROM keeper_discrepancies ORDER BY id").fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM keeper_discrepancies WHERE status = ? ORDER BY id",
            (status,),
        ).fetchall()
    return [dict(row) for row in rows]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Keeper discrepancy scan and resolution workflow.")
    parser.add_argument("--db-path", default="keeper_blended.db", help="Path to Keeper SQLite DB")
    parser.add_argument("--action", choices=["scan", "resolve", "list"], required=True)
    parser.add_argument("--id", type=int, help="Discrepancy id for resolve")
    parser.add_argument("--decision", choices=["CONFIRMED", "DISMISSED"], help="Resolution decision")
    parser.add_argument("--status", choices=["PENDING_USER", "CONFIRMED", "DISMISSED"], help="Status filter for list")
    parser.add_argument("--output", choices=["text", "json"], default="text")
    return parser.parse_args()


def _emit(payload: dict[str, Any], output_mode: str) -> None:
    if output_mode == "json":
        print(json.dumps(payload, separators=(",", ":")))
        return

    for key, value in payload.items():
        print(f"{key}: {value}")


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    if db_path.parent != Path(""):
        db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row

        if args.action == "scan":
            created = scan_discrepancies(connection)
            payload = {
                "created_count": len(created),
                "created_ids": created,
                "pending_count": len(list_discrepancies(connection, status="PENDING_USER")),
            }
            _emit(payload, args.output)
            return

        if args.action == "resolve":
            if args.id is None or args.decision is None:
                raise ValueError("--id and --decision are required for --action resolve")
            payload = resolve_discrepancy(connection, args.id, args.decision)
            _emit(payload, args.output)
            return

        payload = {
            "items": list_discrepancies(connection, status=args.status),
        }
        _emit(payload, args.output)


if __name__ == "__main__":
    main()
