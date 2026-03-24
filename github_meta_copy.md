# GitHub Meta Copy Guide

This document contains the exact string lengths and formatting required for NeverHuman's GitHub repository settings. Please copy and paste these directly into the GitHub web interface.

---

## 1. Repository "About" Blurb
**(Location: Top right corner gear icon on the main repo page)**

A private, voice-first cognitive assistant for Apple Silicon. Built for persistent memory, fast local inference via Metal, and zero cloud dependency.

*(154 / 350 characters)*

---

## 2. GitHub Topics / Semantic Tags
**(Location: Right beneath the "About" blurb in the gear menu)**

Use the following exact tags to maximize visibility within relevant OSS circuits:

`apple-silicon` `local-ai` `voice-assistant` `vector-database` `fastapi` `memory` `metal-inference` `whisper` `tts`

---

## 3. GitHub v1.0.0 Release Notes Template
**(Location: Create a new release via the right sidebar)**

**Title:** v1.0.0 (Developer Preview) — Local Runtime and Voice Engine

**Description:**
```markdown
The inaugural Developer Preview of NeverHuman's GARY cognitive assistant is mapped entirely for Apple Silicon.

### Highlights
- **Persistent Memory Pipeline**: End-to-end vector storage and context retrieval utilizing local Postgres/pgvector.
- **Sub-Second Voice Target**: Pipeline integrations for Whisper ASR and Kokoro TTS handling <400ms turnaround.
- **Metal Offload Architecture**: Initial support for streaming massive LLMs directly from SSD-to-Metal.

### Known Constraints
- The `UI` framework is currently experimental.
- Heavy background reflection tasks may introduce voice stutter on base M1 configurations.

Please refer to documentation in `/docs` for getting started and hardware matrix expectations. We welcome PRs against the `/gary/core` and `/gary/pipeline` directories.
```
