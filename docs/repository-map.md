# Repository Map

The NeverHuman repository is structured to separate front-end interactions, backend inference, and long-term memory systems.

```
neverhuman/
├── gary/                  # The core cognitive assistant runtime
│   ├── server.py          # FastAPI application & websocket orchestration
│   ├── pipeline/          # ASR, TTS, LLM interaction wrappers
│   ├── core/              # Consciousness loop, reflections, policy bounds
│   ├── memory/            # Postgres handlers, pgvector storage, retrieval
│   └── static/            # Web application UI assets (HTML, JS, CSS)
├── docs/                  # Project documentation, FAQs, Benchmarks
├── assets/                # Design assets, logos, screenshots
├── examples/              # Sample configurations, external integrations
├── benchmarks/            # Performance testing scripts and references
├── ARCHITECTURE.md        # Technical breakdown of the OS
├── VISION.md              # Project philosophy and long-term goals
├── ROADMAP.md             # Immediate and future feature delivery
├── SECURITY.md            # Vulnerability handling
└── TELEMETRY.md           # Telemetry choices and exact privacy policies
```
