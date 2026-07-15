from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path

TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")
EMBEDDING_DIMENSIONS = 256


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def embed_text(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    vector = [0.0] * dimensions
    for token in tokenize(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    dot = sum(l * r for l, r in zip(left, right))
    return dot / (left_norm * right_norm)


def initialize_keeper_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS keeper_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_agent TEXT NOT NULL,
            guide_url TEXT NOT NULL,
            game_title TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            heading TEXT NOT NULL,
            text TEXT NOT NULL,
            token_count INTEGER NOT NULL,
            trust_status TEXT NOT NULL DEFAULT 'approved',
            trust_confidence REAL NOT NULL DEFAULT 1.0,
            source_domain TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '',
            sanitizer_version TEXT NOT NULL DEFAULT '',
            safety_notes TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS keeper_chunk_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_agent TEXT NOT NULL,
            correlation_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            embedding_json TEXT NOT NULL,
            UNIQUE(correlation_id, chunk_index)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS keeper_games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_agent TEXT NOT NULL,
            game_title TEXT NOT NULL,
            platform TEXT NOT NULL,
            completion_rate REAL NOT NULL DEFAULT 0,
            trophy_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            UNIQUE(game_title, platform)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS keeper_guides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_agent TEXT NOT NULL,
            guide_url TEXT NOT NULL,
            game_title TEXT NOT NULL,
            platform TEXT NOT NULL,
            quality_views INTEGER NOT NULL DEFAULT 0,
            quality_age_days INTEGER NOT NULL DEFAULT 0,
            quality_score REAL NOT NULL DEFAULT 0,
            trust_status TEXT NOT NULL DEFAULT 'approved',
            trust_confidence REAL NOT NULL DEFAULT 1.0,
            source_domain TEXT NOT NULL DEFAULT '',
            sanitizer_version TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            UNIQUE(guide_url)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS keeper_game_guide_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_title TEXT NOT NULL,
            platform TEXT NOT NULL,
            guide_url TEXT NOT NULL,
            match_confidence REAL NOT NULL,
            score_views REAL NOT NULL DEFAULT 0,
            score_recency REAL NOT NULL DEFAULT 0,
            score_total REAL NOT NULL DEFAULT 0,
            match_mode TEXT NOT NULL DEFAULT 'probabilistic',
            linked_at TEXT NOT NULL,
            UNIQUE(game_title, platform, guide_url)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS keeper_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_key TEXT NOT NULL,
            version_label TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK (version_label IN ('LATEST', 'PREVIOUS', 'STABLE')),
            UNIQUE(entity_type, entity_key, version_label)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS keeper_discrepancies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_title TEXT NOT NULL,
            platform TEXT NOT NULL,
            discrepancy_type TEXT NOT NULL,
            expected_value TEXT NOT NULL,
            observed_value TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING_USER',
            suggested_resolution TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            CHECK (status IN ('PENDING_USER', 'CONFIRMED', 'DISMISSED'))
        )
        """
    )


def seed_demo_data(connection: sqlite3.Connection) -> int:
    existing = connection.execute("SELECT COUNT(*) AS total FROM keeper_chunks").fetchone()[0]
    if existing:
        return int(existing)

    samples = [
        {
            "source_agent": "milo",
            "guide_url": "https://example.org/guides/elden-ring-routes",
            "game_title": "Elden Ring",
            "correlation_id": "demo-elden-001",
            "fetched_at": "2026-07-11T00:00:00+00:00",
            "chunk_index": 0,
            "heading": "Boss Route",
            "text": "Start with Limgrave field bosses, then clear Stormveil and route into Liurnia to maximize early rune efficiency.",
        },
        {
            "source_agent": "milo",
            "guide_url": "https://example.org/guides/demon-souls-platinum",
            "game_title": "Demon's Souls",
            "correlation_id": "demo-ds-001",
            "fetched_at": "2026-07-11T00:00:00+00:00",
            "chunk_index": 0,
            "heading": "Platinum Prep",
            "text": "Plan world tendency in advance and track missable NPC questlines before starting New Game Plus.",
        },
        {
            "source_agent": "milo",
            "guide_url": "https://example.org/guides/cleanup-trophies",
            "game_title": "General Trophy Cleanup",
            "correlation_id": "demo-cleanup-001",
            "fetched_at": "2026-07-11T00:00:00+00:00",
            "chunk_index": 0,
            "heading": "Cleanup Strategy",
            "text": "Batch collectibles by zone, then run combat and challenge trophies together to reduce duplicate travel time.",
        },
    ]

    for sample in samples:
        token_count = len(sample["text"].split())
        connection.execute(
            """
            INSERT INTO keeper_chunks (
                source_agent, guide_url, game_title, correlation_id, fetched_at,
                chunk_index, heading, text, token_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sample["source_agent"],
                sample["guide_url"],
                sample["game_title"],
                sample["correlation_id"],
                sample["fetched_at"],
                sample["chunk_index"],
                sample["heading"],
                sample["text"],
                token_count,
            ),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO keeper_chunk_embeddings (
                source_agent, correlation_id, chunk_index, embedding_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                sample["source_agent"],
                sample["correlation_id"],
                sample["chunk_index"],
                json.dumps(embed_text(sample["text"]), separators=(",", ":")),
            ),
        )

    connection.execute(
        """
        INSERT OR REPLACE INTO keeper_games (
            source_agent, game_title, platform, completion_rate, trophy_count, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("sly", "Elden Ring", "PS5", 71.5, 42, "2026-07-11T00:00:00+00:00"),
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO keeper_guides (
            source_agent, guide_url, game_title, platform,
            quality_views, quality_age_days, quality_score, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "milo",
            "https://example.org/guides/elden-ring-routes",
            "Elden Ring",
            "PS5",
            240000,
            120,
            0.92,
            "2026-07-11T00:00:00+00:00",
        ),
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO keeper_game_guide_links (
            game_title, platform, guide_url, match_confidence,
            score_views, score_recency, score_total, match_mode, linked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Elden Ring",
            "PS5",
            "https://example.org/guides/elden-ring-routes",
            0.94,
            0.95,
            0.80,
            0.92,
            "probabilistic",
            "2026-07-11T00:00:00+00:00",
        ),
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO keeper_snapshots (
            entity_type, entity_key, version_label, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "game",
            "Elden Ring::PS5",
            "LATEST",
            json.dumps({"completion_rate": 71.5, "trophy_count": 42}, separators=(",", ":")),
            "2026-07-11T00:00:00+00:00",
        ),
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO keeper_snapshots (
            entity_type, entity_key, version_label, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "game",
            "Elden Ring::PS5",
            "PREVIOUS",
            json.dumps({"completion_rate": 69.0, "trophy_count": 40}, separators=(",", ":")),
            "2026-07-10T00:00:00+00:00",
        ),
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO keeper_snapshots (
            entity_type, entity_key, version_label, payload_json, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "game",
            "Elden Ring::PS5",
            "STABLE",
            json.dumps({"completion_rate": 65.0, "trophy_count": 36}, separators=(",", ":")),
            "2026-07-09T00:00:00+00:00",
        ),
    )
    connection.execute(
        """
        INSERT INTO keeper_discrepancies (
            game_title, platform, discrepancy_type, expected_value,
            observed_value, status, suggested_resolution, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Elden Ring",
            "PS5",
            "completion_rate",
            "71.5",
            "70.1",
            "PENDING_USER",
            "Review sync timing and confirm whether to refresh linked plan metrics.",
            "2026-07-11T00:00:00+00:00",
        ),
    )

    return len(samples)


def retrieve(connection: sqlite3.Connection, query: str, top_k: int) -> list[tuple[float, sqlite3.Row]]:
    rows = connection.execute(
        """
        SELECT
            kc.game_title,
            kc.heading,
            kc.text,
            kce.embedding_json
        FROM keeper_chunk_embeddings kce
        JOIN keeper_chunks kc
            ON kc.correlation_id = kce.correlation_id
           AND kc.chunk_index = kce.chunk_index
        """
    ).fetchall()

    query_vector = embed_text(query)
    ranked: list[tuple[float, sqlite3.Row]] = []
    for row in rows:
        embedding = json.loads(row["embedding_json"])
        score = cosine_similarity(query_vector, embedding)
        ranked.append((score, row))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[:top_k]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize TheKeeper DB and run a retrieval smoke test.")
    parser.add_argument(
        "--db-path",
        default="keeper_blended.db",
        help="Path to Keeper SQLite file. Default: keeper_blended.db",
    )
    parser.add_argument(
        "--query",
        default="best way to clean up remaining trophies quickly",
        help="Query string for smoke retrieval test.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Number of retrieval snippets to show.",
    )
    parser.add_argument(
        "--seed-demo",
        action="store_true",
        help="Seed demo chunks if keeper_chunks is empty.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path)
    if db_path.parent != Path(""):
        db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        initialize_keeper_tables(connection)

        if args.seed_demo:
            inserted = seed_demo_data(connection)
            if inserted:
                print(f"Seeded {inserted} demo chunk(s).")

        total = connection.execute("SELECT COUNT(*) AS total FROM keeper_chunks").fetchone()[0]
        game_total = connection.execute("SELECT COUNT(*) AS total FROM keeper_games").fetchone()[0]
        guide_total = connection.execute("SELECT COUNT(*) AS total FROM keeper_guides").fetchone()[0]
        link_total = connection.execute("SELECT COUNT(*) AS total FROM keeper_game_guide_links").fetchone()[0]
        discrepancy_total = connection.execute("SELECT COUNT(*) AS total FROM keeper_discrepancies").fetchone()[0]
        print(f"Keeper rows available: {total}")
        print(f"Games: {game_total} | Guides: {guide_total} | Links: {link_total} | Discrepancies: {discrepancy_total}")

        results = retrieve(connection, args.query, args.top_k)
        print(f"\nTop {len(results)} result(s) for query: {args.query!r}\n")
        for index, (score, row) in enumerate(results, start=1):
            print(f"{index}. score={score:.4f} | {row['game_title']} | {row['heading']}")
            print(f"   {row['text'][:220]}")


if __name__ == "__main__":
    main()
