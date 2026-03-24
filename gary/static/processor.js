/**
 * PCMCaptureProcessor - AudioWorkletProcessor (v3.1)
 *
 * Runs on the isolated audio rendering thread.
 * Collects Float32 PCM samples from the mic input and batches them
 * into configured-size chunks before posting to the main thread.
 *
 * v3.1 additions:
 *   - Reduced default chunk size: 1280 (80ms @ 16kHz) for lower latency
 *   - Adaptive RMS onset detector: detects loud onsets while GARY is
 *     speaking and posts { type: 'onset' } to the main thread, which
 *     triggers instant local playback mute before the server even knows.
 *   - EMA noise floor tracker: avoids false triggers in noisy rooms.
 *
 * Target: 16 kHz mono (enforced by AudioContext sampleRate on host side).
 */
class PCMCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    // v3.1: reduced from 2560 (160ms) to 1280 (80ms) for lower transport latency
    this._chunkSamples = opts.chunkSamples || 1280;
    this._buffer = new Float32Array(this._chunkSamples);
    this._writePos = 0;
    this._active = true;

    // ── v3.1 onset detector state ──────────────────────────────────────────
    this._garyState = 'idle';      // 'idle' | 'listening' | 'thinking' | 'speaking'
    this._noiseFloor = 0.01;         // adaptive EMA noise floor
    this._onsetCount = 0;            // consecutive high-RMS subframes
    this._onsetCooldown = 0;         // frames to wait before firing again
    // 320 samples = 20ms subframe for onset detection
    this._subframeBuf = new Float32Array(320);
    this._subframePos = 0;

    this.port.onmessage = (e) => {
      if (e.data && e.data.command === 'stop') {
        this._active = false;
        // Flush any remaining samples
        if (this._writePos > 0) {
          this.port.postMessage(
            { type: 'chunk', data: this._buffer.slice(0, this._writePos) },
            []
          );
          this._writePos = 0;
        }
        this.port.postMessage({ type: 'done' });
      } else if (e.data && e.data.command === 'state') {
        this._garyState = e.data.value || 'idle';
      }
    };
  }

  /**
   * Compute RMS of a Float32 subframe.
   */
  _rms(buf, len) {
    let sum = 0;
    for (let i = 0; i < len; i++) {
      sum += buf[i] * buf[i];
    }
    return Math.sqrt(sum / len);
  }

  /**
   * Process a 20ms subframe for onset detection.
   * Only active when GARY is speaking or thinking.
   */
  _processSubframe() {
    const rms = this._rms(this._subframeBuf, this._subframePos);

    if (this._garyState === 'speaking' || this._garyState === 'thinking') {
      // Onset detection: is this significantly louder than background?
      const threshold = Math.max(0.015, this._noiseFloor * 3.0);

      if (this._onsetCooldown > 0) {
        this._onsetCooldown--;
      } else if (rms > threshold) {
        this._onsetCount++;
        if (this._onsetCount >= 2) {
          // Fire onset — main thread will mute playback and send interrupt_hint
          this.port.postMessage({ type: 'onset' });
          this._onsetCount = 0;
          this._onsetCooldown = 8;   // ~160ms cooldown (8 × 20ms) — v3.2: was 25/500ms
        }
      } else {
        this._onsetCount = Math.max(0, this._onsetCount - 1);
      }
    } else {
      // Adapt noise floor only when GARY is NOT speaking (to avoid
      // tracking TTS echo as "noise")
      this._noiseFloor = 0.995 * this._noiseFloor + 0.005 * rms;
      this._onsetCount = 0;
    }
  }

  process(inputs) {
    if (!this._active) return false;

    const input = inputs[0];
    if (!input || input.length === 0) return true;

    // Mix down to mono if multi-channel (take channel 0)
    const channelData = input[0];
    if (!channelData || channelData.length === 0) return true;

    // ── Feed onset detector subframe buffer ─────────────────────────────
    for (let i = 0; i < channelData.length; i++) {
      this._subframeBuf[this._subframePos++] = channelData[i];
      if (this._subframePos >= 320) {
        this._processSubframe();
        this._subframePos = 0;
      }
    }

    // ── Chunk batching (unchanged logic, new default size) ──────────────
    let srcOffset = 0;
    while (srcOffset < channelData.length) {
      const space = this._chunkSamples - this._writePos;
      const toCopy = Math.min(space, channelData.length - srcOffset);

      this._buffer.set(channelData.subarray(srcOffset, srcOffset + toCopy), this._writePos);
      this._writePos += toCopy;
      srcOffset += toCopy;

      if (this._writePos >= this._chunkSamples) {
        // Transfer ownership to avoid a copy on the main thread
        const out = this._buffer.buffer.slice(0);
        this.port.postMessage({ type: 'chunk', data: new Float32Array(out) }, [out]);

        // Reset — reuse buffer
        this._buffer = new Float32Array(this._chunkSamples);
        this._writePos = 0;
      }
    }

    return true;
  }
}

registerProcessor('pcm-capture-processor', PCMCaptureProcessor);
