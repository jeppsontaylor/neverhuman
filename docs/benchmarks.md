# Benchmarks & Supported Hardware

We optimize exclusively for Apple Silicon and Metal to yield maximum inference performance while minimizing RAM impact using SSD streaming.

## Supported Hardware Matrix

| Hardware | Status | Recommended? | Notes |
|----------|--------|--------------|-------|
| M1 16GB | Supported | Basic | Smaller models recommended; heavy memory load. |
| M2 Pro 32GB | Supported | Yes | Good interactive performance; fast generation. |
| M3 Max 64GB | Supported | Excellent | Best experience; highest throughput. |
| M4 Max 128GB | Supported | Overkill | Near instantaneous response. |

## Expected Latency (Average on M3 Max)

| Metric | Measured Latency |
|--------|-----------------|
| Voice Response (TTS) | <100 ms |
| First Token (LLM) | <400 ms |
| Memory Retrieval | <50 ms |
