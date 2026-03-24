# Frequently Asked Questions

**Does it work offline?**
Yes. 100% of GARY’s inference, memory retrieval, and speech processing happens locally on your Apple Silicon chip.

**Which Macs are supported?**
GARY requires an Apple Silicon Mac (M1, M2, M3, or M4). An Intel Mac is not supported as we rely heavily on Metal framework optimizations.

**Can I choose my own model?**
Yes. While it defaults to `Qwen3.5-35B-A3B-4bit` for optimal performance, you can supply your own local model formats.

**How much disk space does it need?**
We recommend at least 50GB of free SSD space. The model weights take the bulk of this, while your local memory (Postgres) grows slowly over time.

**What data is stored?**
All conversational data, generated embeddings, and internal reflections are stored locally in a Postgres database running via Docker on your machine. Nothing is transmitted to external servers.

**Can I disable learning or memory?**
Yes. You can disable background reflections and memory consolidation entirely in the settings screen.

**Is telemetry enabled?**
By default, we collect zero telemetry. There is no hidden pinging. Review `TELEMETRY.md` for explicit details.

**Is this production-ready or experimental?**
GARY is currently in **Alpha**. The core interactions work flawlessly, but APIs and internal structures may change.

**Does it require Docker?**
Yes, Docker is required to run the Postgres database with the `pgvector` extension for memory storage.

**Can contributors extend it?**
Absolutely. We have heavily modularized the `pipeline/` and `core/` folders to invite new tools, plugins, and modalities. See `CONTRIBUTING.md`.
