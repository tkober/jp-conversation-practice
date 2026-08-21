/**
 * Gap-free playback queue for the PCM16 audio chunks streamed by the backend.
 *
 * Chunks arrive faster than real time, so each one is scheduled to start
 * exactly where the previous one ends instead of being played on arrival.
 * `stop()` drops everything still queued -- that is what makes barge-in
 * (the user interrupting the tutor) sound immediate.
 */
export class AudioPlayer {
  private nextStartTime = 0;
  private readonly scheduled = new Set<AudioBufferSourceNode>();
  private readonly gain: GainNode;

  constructor(
    private readonly context: AudioContext,
    private readonly sampleRate: number,
  ) {
    this.gain = context.createGain();
    this.gain.connect(context.destination);
  }

  /** Seconds of audio still queued ahead of the playback cursor. */
  get bufferedSeconds(): number {
    return Math.max(this.nextStartTime - this.context.currentTime, 0);
  }

  get isPlaying(): boolean {
    return this.scheduled.size > 0;
  }

  /** Queue one PCM16 mono chunk. */
  enqueue(pcm16: ArrayBuffer): void {
    const samples = new Int16Array(pcm16);
    if (samples.length === 0) {
      return;
    }

    const buffer = this.context.createBuffer(1, samples.length, this.sampleRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i++) {
      channel[i] = samples[i] / 0x8000;
    }

    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.gain);
    source.onended = () => this.scheduled.delete(source);

    // A small lead time absorbs jitter without being audible.
    const startAt = Math.max(this.nextStartTime, this.context.currentTime + 0.05);
    source.start(startAt);
    this.scheduled.add(source);
    this.nextStartTime = startAt + buffer.duration;
  }

  /** Drop all queued audio immediately (used when the user interrupts). */
  stop(): void {
    for (const source of this.scheduled) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        // Already finished; nothing to stop.
      }
      source.disconnect();
    }
    this.scheduled.clear();
    this.nextStartTime = 0;
  }

  dispose(): void {
    this.stop();
    this.gain.disconnect();
  }
}
