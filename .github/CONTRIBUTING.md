# Contributing to NeverHuman (GARY)

First off, thank you for considering contributing to GARY! This is a community-driven project dedicated to building a private, local, voice-first cognitive assistant for Apple Silicon. We’d love your help.

## Contribution Lanes

Contributors are more likely to succeed if they find the right lane. We have explicitly defined the following areas of focus:

* **🎙️ Voice pipeline** (`label: voice`) — VAD tuning, ASR models (Whisper), TTS improvements (Kokoro), audio latency reduction.
* **🧠 Local inference & runtime** (`label: performance`, `label: runtime`) — Metal/SSD streaming optimizations, Qwen model loading, context handling.
* **💾 Memory and retrieval** (`label: memory`) — Postgres `pgvector` optimizations, graph relationships, vector spacing.
* **🖥️ UI / Frontend** (`label: frontend`) — The setup UI, console web interface, and socket stability.
* **📖 Documentation** (`label: docs`) — Improving tutorials, adding examples, writing clearer copy.
* **📊 Benchmarks / Testing** (`label: infra`) — Automating performance comparisons, hardware testing matrices.

## Finding an Issue

* **New to the project?** Look for issues labeled `good first issue` or `help wanted`. These will usually be isolated UI tweaks, documentation improvements, or simple edge cases.
* **Researchers?** Look for the `research` label. We frequently post open-ended architectural questions there.

## Issue Templates

Please use the provided YAML issue templates when opening an issue:
* Bug Report
* Feature Request
* Research Proposition

## Development Setup

1. **Clone the repository**
2. **Initialize Python**: `python3 -m venv .venv && source .venv/bin/activate`
3. **Ensure Docker is running** (for `pgvector` memory backend).
4. **Read `ARCHITECTURE.md`** to understand the high-level OS data flow before digging into `gary/server.py`.
