# Third-Party Notices

NeverHuman (GARY) incorporates the following open source components:

---

## Python runtime dependencies

| Package | License | Notes |
|---------|---------|-------|
| [FastAPI](https://github.com/tiangolo/fastapi) | MIT | Web framework |
| [uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause | ASGI server |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause | Async HTTP client |
| [numpy](https://numpy.org) | BSD-3-Clause | Numerical computing |
| [asyncpg](https://github.com/MagicStack/asyncpg) | Apache-2.0 | Postgres async driver |
| [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) | Apache-2.0 | TTS via ONNX Runtime |
| [soundfile](https://github.com/bastibe/python-soundfile) | BSD-3-Clause | Audio I/O |
| [huggingface_hub](https://github.com/huggingface/huggingface_hub) | Apache-2.0 | Model download |
| [mlx-qwen3-asr](https://github.com/ml-explore/mlx) | MIT | ASR on Apple Silicon |
| [pytest](https://github.com/pytest-dev/pytest) | MIT | Test framework |
| [ruff](https://github.com/astral-sh/ruff) | MIT | Linter/formatter |

---

## AI models

| Model | License | Source |
|-------|---------|--------|
| Qwen3-ASR-0.6B | Apache-2.0 | [HuggingFace: Qwen/Qwen3-ASR-0.6B](https://huggingface.co/Qwen/Qwen3-ASR-0.6B) |
| Kokoro-82M | Apache-2.0 | [HuggingFace: hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) |
| Qwen3.5-35B-A3B-4bit | Apache-2.0 | [HuggingFace: mlx-community/Qwen3.5-35B-A3B-4bit](https://huggingface.co/mlx-community/Qwen3.5-35B-A3B-4bit) |

---

## Infrastructure

| Software | License | Notes |
|---------|---------|-------|
| PostgreSQL 16 | PostgreSQL License | Database |
| pgvector | PostgreSQL License | Vector similarity search |
| Docker | Apache-2.0 | Container runtime |
| ONNX Runtime | MIT | TTS inference |

---

## Fonts

| Font | License | Source |
|------|---------|--------|
| Inter | OFL-1.1 | [Google Fonts](https://fonts.google.com/specimen/Inter) |
| JetBrains Mono | OFL-1.1 | [Google Fonts](https://fonts.google.com/specimen/JetBrains+Mono) |

---

Full license texts for all dependencies are available via `pip show <package>` or at the linked repositories above.
