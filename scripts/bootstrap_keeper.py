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
            token_count INTEGER NOT NULL
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
        print(f"Keeper rows available: {total}")

        results = retrieve(connection, args.query, args.top_k)
        print(f"\nTop {len(results)} result(s) for query: {args.query!r}\n")
        for index, (score, row) in enumerate(results, start=1):
            print(f"{index}. score={score:.4f} | {row['game_title']} | {row['heading']}")
            print(f"   {row['text'][:220]}")


if __name__ == "__main__":
    main()
