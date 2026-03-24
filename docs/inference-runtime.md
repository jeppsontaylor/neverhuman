# Inference Runtime Guide

## SSD to Metal Streaming (Our Moat)

Instead of requiring huge amounts of RAM for large local models, NeverHuman streams model weights efficiently from the SSD directly to Apple's Metal runtime stack. This means an M-series Mac with 16GB of RAM can run models that historically required 32GB+ of unified memory, dramatically lowering the hardware barrier for a sophisticated local assistant.

### How it works
Through precise memory mapping and chunked loading, we leverage the exceptionally fast read speeds of modern Apple SSDs (often >5GB/s). The chunks are fed into the GPU only when needed for matrix multiplication.

## Speech Pipelines
- **ASR**: Whisper-based transcription with aggressive VAD (Voice Activity Detection) tuning for sub-second recognition.
- **TTS**: Kokoro-82M is utilized to yield highly realistic, low-latency, and emotive voice rendering.
