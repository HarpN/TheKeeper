# 🗝️ TheKeeper: Semantic World-State Aggregator

**TheKeeper** is a specialized background daemon and read-only database manager designed to serve as the exclusive "world state" memory for the Charon conversational UI. 

By physically isolating read-heavy semantic search queries from the primary transaction cluster, TheKeeper ensures high-performance natural language interactions without degrading the execution speeds of the primary ingestion agents.

## ⚙️ Core Responsibilities

*   **Secure Aggregation:** Reaches across the network via strict Mutual TLS (mTLS) gRPC channels to fetch real-time PSN telemetry from **Sly** and scraped strategy chunks from **Milo**.
*   **Blended State Generation:** Merges relational user metrics with unstructured guide data into a highly optimized, unified local SQLite file (`keeper_blended.db`).
*   **Local Vectorization (RAG):** Automatically processes incoming text chunks through a lightweight local **Ollama** embedding model (e.g., `all-minilm`), storing the 384-dimension results into virtual shadow tables.
*   **Charon's Oracle:** Serves as the single, read-only query endpoint for Charon. Charon searches TheKeeper using K-Nearest Neighbor vector math to extract highly relevant context snippets before formulating governance proposals.

## 🏗️ Internal Architecture

TheKeeper intentionally operates with a "Zero-Write" constraint regarding the broader cluster. It believes it is the ultimate database for the UI, forcing Charon to pass all mutation intents upward to the **Judy Council** rather than attempting direct local writes.

```mermaid
graph TD
    subgraph "TheKeeper Read-Only VM"
        K_Service[Keeper Sync Daemon]
        O[Local Ollama Embeddings]
        DB[(blended_read_only.db)]
        
        K_Service -->|Generate Vectors| O
        O -->|Store 384d Float| DB
    end

    subgraph "Primary Cluster (mTLS)"
        M[Milo Agent]
        S[Sly Agent]
    end

    C[Charon UI] -->|Semantic Search| DB
    K_Service -->|gRPC Fetch| M
    K_Service -->|gRPC Fetch| S
