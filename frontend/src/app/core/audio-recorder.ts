/**
 * Microphone capture producing PCM16 chunks at the Realtime API's sample rate.
 *
 * The AudioContext is opened at 24 kHz so the browser resamples the microphone
 * for us; the worklet then only has to convert float samples to int16. Echo
 * cancellation matters here: without it the tutor's own voice leaks back into
 * the mic and the server-side VAD treats it as the user speaking.
 */
const WORKLET_URL = 'audio/pcm-recorder-worklet.js';

/**
 * Why the microphone cannot be used in this browsing context, or null if it
 * can.
 *
 * Both `navigator.mediaDevices` and `BaseAudioContext.audioWorklet` are marked
 * `[SecureContext]`, so over plain HTTP to a LAN address they are not merely
 * denied but absent, and `start()` would fail with a bare TypeError naming
 * neither the cause nor the fix. Checking up front turns that into a sentence
 * the setup screen can show before the user even presses start.
 */
export function microphoneBlockedReason(): string | null {
  if (!window.isSecureContext) {
    return (
      `Der Browser gibt das Mikrofon nur über eine sichere Verbindung frei, diese Seite ` +
      `läuft aber über ${location.origin}. Rufe sie über HTTPS oder über localhost auf.`
    );
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    return 'Dieser Browser stellt keinen Zugriff auf das Mikrofon bereit.';
  }
  return null;
}

export class AudioRecorder {
  private stream: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private worklet: AudioWorkletNode | null = null;
  private analyser: AnalyserNode | null = null;
  private levelData: Uint8Array<ArrayBuffer> | null = null;
  private muted = false;

  constructor(
    private readonly context: AudioContext,
    private readonly onChunk: (chunk: ArrayBuffer) => void,
  ) {}

  async start(): Promise<void> {
    const blocked = microphoneBlockedReason();
    if (blocked) {
      throw new Error(blocked);
    }

    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    await this.context.audioWorklet.addModule(WORKLET_URL);

    this.source = this.context.createMediaStreamSource(this.stream);
    this.worklet = new AudioWorkletNode(this.context, 'pcm-recorder', {
      numberOfInputs: 1,
      numberOfOutputs: 0,
      channelCount: 1,
    });
    this.worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      if (!this.muted) {
        this.onChunk(event.data);
      }
    };

    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = 512;
    this.levelData = new Uint8Array(new ArrayBuffer(this.analyser.fftSize));

    this.source.connect(this.worklet);
    this.source.connect(this.analyser);
  }

  /** Stop sending audio without dropping the microphone permission. */
  setMuted(muted: boolean): void {
    this.muted = muted;
  }

  /** Current input loudness in the 0..1 range, for the level meter. */
  getLevel(): number {
    if (!this.analyser || !this.levelData) {
      return 0;
    }
    this.analyser.getByteTimeDomainData(this.levelData);

    let sumOfSquares = 0;
    for (const sample of this.levelData) {
      const centred = (sample - 128) / 128;
      sumOfSquares += centred * centred;
    }
    const rms = Math.sqrt(sumOfSquares / this.levelData.length);
    // RMS of speech rarely exceeds ~0.3, so scale for a usable meter.
    return Math.min(rms * 3, 1);
  }

  stop(): void {
    this.worklet?.port.close();
    this.worklet?.disconnect();
    this.source?.disconnect();
    this.analyser?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());

    this.worklet = null;
    this.source = null;
    this.analyser = null;
    this.levelData = null;
    this.stream = null;
  }
}
