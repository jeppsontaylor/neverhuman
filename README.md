# <p align="center"><img src="assets/logo-placeholder.svg" alt="NeverHuman" width="400" /><br>GARY</p>

**GARY is a private, voice-first cognitive assistant that runs entirely on Apple Silicon Macs.**
Persistent memory, local inference, and real-time voice interaction — no cloud, no subscriptions, no data leaving your machine.

<p align="center">
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License" />
  </a>
  <a href="https://support.apple.com/en-us/HT211814">
    <img src="https://img.shields.io/badge/requires-Apple%20Silicon-black?logo=apple" alt="Requires Apple Silicon" />
  </a>
</p>

<p align="center">
  <a href="#quick-start">Get Started</a> · 
  <a href="#demo">Watch Demo</a> · 
  <a href="ARCHITECTURE.md">Architecture</a> · 
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

---

## What makes it different

* **Private by default** — runs locally, data never leaves your machine.
* **Fast on Apple Silicon** — optimized for SSD-to-Metal streaming.
* **Persistent memory** — remembers details over time automatically.
* **Built to evolve** — designed for continuous adaptation and reflection.

---

## Demo
*(Placeholders below. Update with actual assets once generated.)*

![Hero Demo GIF](assets/hero-demo-placeholder.gif)
*Talk to GARY in real time*

<details>
<summary><b>View Interface Screenshots</b></summary>

![Conversation UI](assets/chat-ui-placeholder.png)
*Real-time voice pipeline interface*

![Memory Search](assets/memory-ui-placeholder.png)
*Semantic retrieval and memory inspection*
</details>

---

## Who this is for

* Mac users who want a private, local AI companion lacking cloud fatigue.
* Engineers interested in long-lived, high-performance voice agents.
* Researchers exploring memory, reflection, and cognitive adaptation over time.
* Open-source contributors working on pushing local AI hardware limits.
* People who demand ownership instead of monthly subscriptions.

---

## Quick Start

### Try it now (For Users)
```bash
git clone https://github.com/jeppsontaylor/neverhuman.git
cd neverhuman
bash install.sh
```
*Installation auto-fetches dependencies, models, and boots the zero-configuration UI. See [Getting Started](docs/getting-started.md) for deeper details.*

### Develop Locally (For Engineers)
Please refer to our [Path B: Local Development Guide](docs/getting-started.md) to initialize the virtual environment, start the websocket orchestraton via `gary/server.py`, and bind the frontend to `localhost:8000`.

**Hardware Requirements:**
* **Tested on:** macOS 13+ (Apple Silicon only)
* **Minimum RAM:** 16GB
* **Recommended RAM:** 32GB+
* **Storage:** ~50GB SSD space

---

## What GARY is

GARY is a cognitive operating system designed for persistence. Rather than feeling like a stateless chat window, GARY:
* **listens** through a finely tuned Voice Activity Detection layer.
* **speaks** with ultra-low latency TTS (<100ms per turn).
* **remembers** context permanently via pgvector similarity search.
* **retrieves** facts instantly from local Postgres.
* **reflects** on conversations in the background while idle.
* **adapts** by consolidating themes based on continuous interactions.

---

## What works today vs Roadmap

Be honest about our maturity: this project is in an **Alpha / Developer Preview** state.
**What works today:**
* Real-time voice pipeline input/output.
* On-device inference and memory vectorization.
* Local permanent memory retrieval.
* Background reflection and daemon tasks.
* Model selection via setup flow.

**What is on the roadmap:**
* Deeper long-term adaptation and personalized tuning.
* Richer personality shaping via "Humanity Slider".
* Tool and plugin architectures.
* Multimodal (Video/Image) inputs.

[View the full Roadmap](ROADMAP.md)

---

## Why SSD-to-Metal streaming matters

Instead of requiring huge amounts of expensive RAM for large local models, **NeverHuman streams model weights efficiently from SSD directly to Apple's Metal stack.** 
This enables massive models to run smoothly on standard consumer M-series Macs without paging bottlenecks or memory crashing. 
For deep details, see our [Inference Runtime Guide](docs/inference-runtime.md).

---

## Architecture Overview

![Architecture System Diagram](assets/architecture-placeholder.svg)

**The 5-Part System:**
1. **UI layer** — Browser SPA and setup flow handling.
2. **Reflex core** — The FastAPI instance handling orchestration and state.
3. **Inference runtime** — Local ASR (Whisper), TTS (Kokoro), LLM (Qwen) execution.
4. **Memory spine** — The Postgres + pgvector instance handling durable structured storage.
5. **Mind daemon** — Background reflection and event processing.

[Read the full Architecture Documentation](ARCHITECTURE.md)

---

## Features

**Interaction**
* Real-time conversational pipeline (<400ms turnaround)
* Natural Text-to-Speech (54 voices)
* Low-latency turn-taking and context retention

**Memory**
* Searchable persistent memory backend
* Semantic retrieval architecture
* Structured relationship graphs

**Runtime**
* Exclusively on-device model execution
* Apple Silicon hyper-optimization
* High-speed SSD streaming

**Personalization**
* Asynchronous memory growth over time
* Adaptation pathways derived from sentiment
* Configurable humanity/personality boundaries

**Privacy**
* Zero cloud calls
* 100% data remains local
* Inspectable storage engine

---

## Benchmarks

See our full metric testing in [Benchmarks.md](docs/benchmarks.md).

| Metric | M1 Max | M2 Pro | M3 Max |
| :--- | :---: | :---: | :---: |
| Voice Response (TTS) | <140ms | <120ms | <100ms |
| First Token (LLM) | <600ms | <450ms | <400ms |
| Memory Retrieval | <80ms | <60ms | <50ms |
| RAM Footprint | ~4.5GB | ~4GB | ~4GB |

---

## Reliability, Status & FAQ

**Project Status:** Active Alpha. We are refining the core loop; expect APIs and layouts to shift. Crash tolerance is robust during standard dictation, but heavy concurrent background reflection loops may stall weaker M1 chips. 

* **[Privacy & Telemetry](docs/privacy-and-telemetry.md)**: Zero tracker pings. Telemetry defaults exclusively to OFF.
* **[Security Standard](SECURITY.md)**: Open vulnerability disclosure policy.
* **[FAQ](docs/faq.md)**: Read answers to the most common questions on Docker requirements, available models, and disk utilization.

---

## Join the Project

We’re building a private, local, voice-first cognitive assistant for Apple Silicon. If you care about local AI, memory systems, voice UX, inference performance, or making computing feel more personal, we’d love your help.

We maintain clear lanes for contributors:
* `voice` - ASR/TTS/VAD pipelines
* `memory` - Postgres/rag strategies
* `runtime` - Metal inference ops
* `ui` - Dashboard/console frontends
* `docs` - Strategy and communication
* `benchmarks` - Output scaling metrics

Check out the [Contributing Guide](CONTRIBUTING.md) to dive in, or grab any issue labeled `good first issue` or `help wanted` in our tracker.
