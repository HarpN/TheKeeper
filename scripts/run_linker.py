from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path


def normalize_name(name: str) -> str:
    return " ".join(name.lower().replace(":", " ").replace("-", " ").split())


def title_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_name(left), normalize_name(right)).ratio()


def link_score(similarity: float, quality_score: float) -> float:
    return round((0.65 * similarity) + (0.35 * quality_score), 4)


def run_linker(connection: sqlite3.Connection, threshold: float) -> tuple[int, int]:
    games = connection.execute(
        "SELECT game_title, platform FROM keeper_games"
    ).fetchall()
    guides = connection.execute(
        "SELECT guide_url, game_title, platform, quality_views, quality_age_days, quality_score FROM keeper_guides"
    ).fetchall()

    linked = 0
    candidates = 0
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    for game in games:
        for guide in guides:
            if str(game["platform"]).lower() != str(guide["platform"]).lower():
                continue

            similarity = title_similarity(str(game["game_title"]), str(guide["game_title"]))
            total = link_score(similarity, float(guide["quality_score"]))
            candidates += 1
            if total < threshold:
                continue

            connection.execute(
                """
                INSERT OR REPLACE INTO keeper_game_guide_links (
                    game_title, platform, guide_url, match_confidence,
                    score_views, score_recency, score_total, match_mode, linked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    game["game_title"],
                    game["platform"],
                    guide["guide_url"],
                    total,
                    min(float(guide["quality_views"]) / 250000.0, 1.0),
                    max(0.0, 1.0 - min(float(guide["quality_age_days"]), 365.0) / 365.0),
                    total,
                    "probabilistic",
                    now_iso,
                ),
            )
            linked += 1

    return linked, candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run platform-aware probabilistic game-guide linking in TheKeeper.")
    parser.add_argument("--db-path", default="keeper_blended.db", help="Path to Keeper SQLite DB")
    parser.add_argument("--threshold", type=float, default=0.55, help="Minimum score to persist a link")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        linked, candidates = run_linker(connection, args.threshold)
        print(f"Link candidates evaluated: {candidates}")
        print(f"Links persisted: {linked}")


if __name__ == "__main__":
    main()
