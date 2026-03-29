# Python File Inventory and Rust-Port Status

Complete inventory of all `*.py` files in this repository and why each still exists in Python.

| File | Category | Why still Python |
|---|---|---|
| `flash-moe/build_expert_index_35b.py` | Model artifact tooling | One-off model conversion/export scripts; not on hot path and not required for runtime latency. |
| `flash-moe/export_tokenizer_35b.py` | Model artifact tooling | One-off model conversion/export scripts; not on hot path and not required for runtime latency. |
| `flash-moe/export_vocab_35b.py` | Model artifact tooling | One-off model conversion/export scripts; not on hot path and not required for runtime latency. |
| `flash-moe/extract_weights_35b.py` | Model artifact tooling | One-off model conversion/export scripts; not on hot path and not required for runtime latency. |
| `flash-moe/repack_experts_35b.py` | Model artifact tooling | One-off model conversion/export scripts; not on hot path and not required for runtime latency. |
| `gary/apps/__init__.py` | Misc utility | Not yet ported; lower priority than runtime hot path. |
| `gary/apps/forged/__init__.py` | Control-plane service | Python implementation exists today; candidate for Rust after deterministic policy/logger ports. |
| `gary/apps/forged/planner.py` | Control-plane service | Python implementation exists today; candidate for Rust after deterministic policy/logger ports. |
| `gary/apps/mindd/__init__.py` | Mind sidecar API | FastAPI sidecar around Python model/prompt workflow; tied to Python model-serving path. |
| `gary/apps/mindd/pulse_worker.py` | Mind sidecar API | FastAPI sidecar around Python model/prompt workflow; tied to Python model-serving path. |
| `gary/apps/mindd/serve.py` | Mind sidecar API | FastAPI sidecar around Python model/prompt workflow; tied to Python model-serving path. |
| `gary/apps/routerd/__init__.py` | Control-plane service | Python implementation exists today; candidate for Rust after deterministic policy/logger ports. |
| `gary/apps/routerd/serve.py` | Control-plane service | Python implementation exists today; candidate for Rust after deterministic policy/logger ports. |
| `gary/apps/selfd/__init__.py` | Service/API surface | FastAPI/WebSocket service not yet migrated; targeted by Rust server-loop split phase. |
| `gary/apps/selfd/serve.py` | Service/API surface | FastAPI/WebSocket service not yet migrated; targeted by Rust server-loop split phase. |
| `gary/benchmarks/latency.py` | Benchmark script | Developer benchmarking utility; low runtime impact, keep until core migration completes. |
| `gary/core/__init__.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/affect_types.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/change_router.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/commitments.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/drift_audit.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/drive_types.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/eval_harness.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/eval_metrics.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/events.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/llm_watchdog.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/log_writer.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/mind.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/mind_pulse.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/policies.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/question_ledger.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/resource_arbiter.py` | Deterministic runtime logic | Hybrid wrapper; Rust-first arbitration path available via `GARY_RESOURCE_ARBITER_BIN` with Python compatibility fallback. |
| `gary/core/rumination_governor.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/self_model.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/session_checkpoint.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/core/session_logger.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/memory/__init__.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/memory/db.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/memory/event_writer.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/memory/mind_persist.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/memory/retrieval.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/memory/retrieval_audit.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/memory/spool.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/pipeline/__init__.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/pipeline/_ws_helpers.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/pipeline/asr.py` | Model/runtime adapter | Depends on Python ML/runtime bindings (MLX/ONNX/Numpy stack) or existing model loader glue. |
| `gary/pipeline/context_hints.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/pipeline/context_pack.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/pipeline/download_worker.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/pipeline/filler_audio.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/pipeline/llm.py` | LLM transport adapter | Current SSE streaming client in httpx/asyncio; planned Rust port after policy stack. |
| `gary/pipeline/model_manager.py` | Model/runtime adapter | Depends on Python ML/runtime bindings (MLX/ONNX/Numpy stack) or existing model loader glue. |
| `gary/pipeline/output_sanitizer.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/pipeline/parallel_tts.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/pipeline/silero_vad.py` | Model/runtime adapter | Depends on Python ML/runtime bindings (MLX/ONNX/Numpy stack) or existing model loader glue. |
| `gary/pipeline/tts.py` | Model/runtime adapter | Depends on Python ML/runtime bindings (MLX/ONNX/Numpy stack) or existing model loader glue. |
| `gary/pipeline/tts_normalizer.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/pipeline/turn_classifier.py` | Deterministic runtime logic | Thin compatibility wrapper; primary depth + v2 intent/reasoning classification now Rust-first via `GARY_TURN_CLASSIFIER_BIN`. |
| `gary/pipeline/turn_policy.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/pipeline/turn_supervisor.py` | Deterministic runtime logic | High-priority Rust candidate; still Python due migration sequencing and parity-test backlog. |
| `gary/pipeline/vad.py` | Model/runtime adapter | Depends on Python ML/runtime bindings (MLX/ONNX/Numpy stack) or existing model loader glue. |
| `gary/server.py` | Service/API surface | FastAPI/WebSocket service not yet migrated; targeted by Rust server-loop split phase. |
| `gary/testing/test_affect.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_change_router.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_chaos.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_context_hints.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_context_pack.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_db.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_drift_audit.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_eval_metrics.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_events.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_floor_sovereignty.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_forge_workflow.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_llm.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_memory.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_mind.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_mind_persist.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_mind_pulse.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_parallel_tts.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_policies.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_resource_arbiter.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_routerd.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_self_pack.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_selfd.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_selfd_api.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_session_checkpoint.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_session_logger.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_silero_vad.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_tempo_controller.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_tts.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_turn_classifier.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_turn_control.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_turn_policy.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_v3_changes.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
| `gary/testing/test_vad.py` | Test harness | Pytest suite; kept in Python for rapid iteration and fixture reuse. |
