# Telemetry

## Short version

**NeverHuman collects no telemetry. Zero. None.**

Your voice, conversations, memories, and interactions never leave your machine.

---

## Detailed statement

NeverHuman contains **no:**
- Usage analytics
- Crash reporters
- Model quality feedback loops to external servers
- Anonymous statistics collection
- Advertising SDKs
- Third-party tracking of any kind

All data paths:
- Your microphone → your Mac's RAM → your local LLM → your local TTS → your browser
- Your conversations → your local Postgres database (Docker on your machine)
- Your memories → `~/.neverhuman/` and the local Postgres volume

The only external network calls NeverHuman makes are:
1. **Model downloads** during setup: calling HuggingFace Hub to download ASR/TTS/LLM weights. This is explicit user-initiated, shown in the setup wizard, and subject to HuggingFace's own privacy policy.
2. **Font loading** from Google Fonts (optional: fonts can be hosted locally by serving them from `gary/static/`).

---

## If this changes

Any future telemetry — even fully anonymous — would require:
1. A public RFC with 14-day comment period (see [GOVERNANCE.md](GOVERNANCE.md))
2. Unanimous maintainer agreement
3. Explicit opt-in from users (never opt-out)
4. Transparent, auditable code for all data collection

We consider telemetry changes a major governance decision.
