/**
 * AudioWorklet processor that converts microphone input to PCM16.
 *
 * The AudioContext is created at the Realtime API's sample rate (24 kHz), so
 * the browser has already resampled by the time audio reaches this node. All
 * that is left is float -> int16 conversion and batching into chunks big
 * enough that the WebSocket is not flooded with 128-sample frames.
 */
const SAMPLES_PER_CHUNK = 2400; // 100 ms at 24 kHz

class PcmRecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Int16Array(SAMPLES_PER_CHUNK);
    this.offset = 0;
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) {
      // No input connected (yet); keep the processor alive.
      return true;
    }

    for (let i = 0; i < channel.length; i++) {
      const clamped = Math.max(-1, Math.min(1, channel[i]));
      // Asymmetric scaling keeps the full negative range without wrapping.
      this.buffer[this.offset++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;

      if (this.offset === SAMPLES_PER_CHUNK) {
        const chunk = this.buffer.slice();
        this.port.postMessage(chunk.buffer, [chunk.buffer]);
        this.offset = 0;
      }
    }

    return true;
  }
}

registerProcessor('pcm-recorder', PcmRecorderProcessor);
