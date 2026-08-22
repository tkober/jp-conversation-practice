import { Injectable, computed, signal } from '@angular/core';

import { AudioPlayer } from './audio-player';
import { AudioRecorder } from './audio-recorder';
import {
  EMPTY_USAGE,
  JlptLevel,
  SessionInfo,
  SessionPhase,
  TranscriptTurn,
  UsageSnapshot,
  VadEagerness,
} from './models';

const SAMPLE_RATE = 24000;
const LEVEL_POLL_MS = 100;

interface StartOptions {
  scenario: string;
  jlptLevel: JlptLevel;
  voice: string;
  speed: number;
}

/**
 * Owns one live conversation: the WebSocket to the backend relay, microphone
 * capture and speaker playback, plus all reactive state the UI renders.
 */
@Injectable({ providedIn: 'root' })
export class RealtimeSessionService {
  readonly phase = signal<SessionPhase>('setup');
  readonly usage = signal<UsageSnapshot>(EMPTY_USAGE);
  readonly transcript = signal<TranscriptTurn[]>([]);
  readonly elapsedSeconds = signal(0);
  readonly micLevel = signal(0);
  readonly muted = signal(false);
  readonly tutorSpeaking = signal(false);
  readonly userSpeaking = signal(false);
  readonly errorMessage = signal<string | null>(null);
  readonly sessionInfo = signal<SessionInfo | null>(null);
  readonly speed = signal(1);
  // Filled from /api/voices by the setup screen; the backend clamps regardless.
  readonly speedMin = signal(0.6);
  readonly speedMax = signal(1.4);
  /** Overwritten from `app.session.started` with whatever the backend is using. */
  readonly eagerness = signal<VadEagerness>('low');
  /**
   * わからない: how much help the tutor is currently giving. 0 means none has
   * been asked for since the last thing you said; the backend owns the value
   * and pushes every change, so this only ever mirrors it.
   */
  readonly helpStage = signal(0);
  readonly maxHelpStage = signal(1);
  /** How much the tutor slows down for a help turn; 1 means not at all. */
  readonly helpSpeedFactor = signal(1);
  /** True between pressing the button and the backend confirming the stage. */
  readonly helpPending = signal(false);

  readonly costUsd = computed(() => this.usage().cost_usd);
  readonly isLive = computed(() => this.phase() === 'live');

  private socket: WebSocket | null = null;
  private context: AudioContext | null = null;
  private recorder: AudioRecorder | null = null;
  private player: AudioPlayer | null = null;
  private timerId: ReturnType<typeof setInterval> | null = null;
  private levelId: ReturnType<typeof setInterval> | null = null;
  private startedAt = 0;

  /** Open the relay socket, start capturing audio and go live. */
  async start(options: StartOptions): Promise<void> {
    this.reset();
    this.phase.set('connecting');

    try {
      // Safari only resumes an AudioContext inside a user gesture, which is
      // where this method is called from.
      this.context = new AudioContext({ sampleRate: SAMPLE_RATE });
      await this.context.resume();
      this.player = new AudioPlayer(this.context, SAMPLE_RATE);

      await this.openSocket(options);

      this.recorder = new AudioRecorder(this.context, (chunk) => this.sendAudio(chunk));
      await this.recorder.start();

      this.startTimers();
      this.phase.set('live');
    } catch (error) {
      this.errorMessage.set(this.describe(error));
      await this.stop();
      this.phase.set('setup');
    }
  }

  /** End the session and release microphone and audio resources. */
  async stop(): Promise<void> {
    this.stopTimers();

    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: 'app.session.stop' }));
    }
    this.socket?.close();
    this.socket = null;

    this.recorder?.stop();
    this.recorder = null;

    this.player?.dispose();
    this.player = null;

    if (this.context && this.context.state !== 'closed') {
      await this.context.close();
    }
    this.context = null;

    this.tutorSpeaking.set(false);
    this.userSpeaking.set(false);
    this.micLevel.set(0);
  }

  toggleMute(): void {
    const next = !this.muted();
    this.muted.set(next);
    this.recorder?.setMuted(next);
  }

  /**
   * Change the tutor's speaking rate mid-session. Takes effect from the tutor's
   * next reply -- a response already being generated keeps its original rate.
   */
  setSpeed(speed: number): void {
    this.speed.set(speed);
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: 'app.session.speed', speed }));
    }
  }

  /**
   * Change how long a pause may last before the tutor answers. Takes effect on
   * the next silence -- a reply already being generated is unaffected.
   */
  setEagerness(eagerness: VadEagerness): void {
    this.eagerness.set(eagerness);
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: 'app.session.eagerness', eagerness }));
    }
  }

  /**
   * わからない: tell the tutor you are stuck. Each press without saying
   * anything in between escalates the help one step; the last step is an
   * explanation in German.
   *
   * The tutor is silenced right away, because the press usually happens *while*
   * it is talking — the backend cancels the response upstream, and the queued
   * audio has to go here too, exactly as it does on barge-in.
   */
  requestHelp(): void {
    if (this.socket?.readyState !== WebSocket.OPEN) {
      return;
    }
    this.player?.stop();
    this.tutorSpeaking.set(false);
    this.helpPending.set(true);
    this.socket.send(JSON.stringify({ type: 'app.session.help' }));
  }

  /** Clear everything so a new session can start from the setup screen. */
  reset(): void {
    this.usage.set(EMPTY_USAGE);
    this.transcript.set([]);
    this.elapsedSeconds.set(0);
    this.errorMessage.set(null);
    this.muted.set(false);
    this.sessionInfo.set(null);
    this.speed.set(1);
    this.helpStage.set(0);
    this.helpPending.set(false);
  }

  // --- socket ------------------------------------------------------------

  private openSocket(options: StartOptions): Promise<void> {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${protocol}//${location.host}/ws/realtime`);
    socket.binaryType = 'arraybuffer';
    this.socket = socket;

    return new Promise<void>((resolve, reject) => {
      let settled = false;

      socket.onopen = () => {
        socket.send(
          JSON.stringify({
            type: 'app.session.start',
            scenario: options.scenario,
            jlpt_level: options.jlptLevel,
            voice: options.voice,
            speed: options.speed,
          }),
        );
        settled = true;
        resolve();
      };

      socket.onerror = () => {
        if (!settled) {
          settled = true;
          reject(new Error('Verbindung zum Backend fehlgeschlagen.'));
        }
      };

      socket.onclose = () => {
        if (!settled) {
          settled = true;
          reject(new Error('Das Backend hat die Verbindung abgelehnt.'));
          return;
        }
        if (this.phase() === 'live') {
          void this.stop();
          this.phase.set('review');
        }
      };

      socket.onmessage = (event) => this.handleMessage(event);
    });
  }

  private handleMessage(event: MessageEvent): void {
    if (event.data instanceof ArrayBuffer) {
      this.player?.enqueue(event.data);
      this.tutorSpeaking.set(true);
      return;
    }

    let message: Record<string, unknown>;
    try {
      message = JSON.parse(event.data as string);
    } catch {
      return;
    }

    switch (message['type']) {
      case 'app.session.started': {
        const speed = Number(message['speed'] ?? 1);
        const eagerness = (message['vad_eagerness'] ?? 'low') as VadEagerness;
        this.sessionInfo.set({
          model: String(message['model'] ?? ''),
          scenario: String(message['scenario'] ?? ''),
          jlpt_level: String(message['jlpt_level'] ?? ''),
          voice: String(message['voice'] ?? ''),
          speed,
          vad_eagerness: eagerness,
          instructions: String(message['instructions'] ?? ''),
        });
        this.speed.set(speed);
        this.eagerness.set(eagerness);
        this.maxHelpStage.set(Number(message['help_stages'] ?? 1));
        this.helpSpeedFactor.set(Number(message['help_speed_factor'] ?? 1));
        break;
      }

      case 'app.speed.changed':
        // The server clamps to its supported range, so trust its value.
        this.speed.set(Number(message['speed'] ?? this.speed()));
        break;

      case 'app.eagerness.changed':
        // Same as the speed: the server validates, so its value is the truth.
        this.eagerness.set((message['eagerness'] ?? this.eagerness()) as VadEagerness);
        break;

      case 'app.help.stage':
        // Includes the reset to 0 when you start speaking again, so the button
        // never has to guess where the escalation stands.
        this.helpStage.set(Number(message['stage'] ?? 0));
        this.maxHelpStage.set(Number(message['max_stage'] ?? this.maxHelpStage()));
        this.helpPending.set(false);
        break;

      case 'app.cost.update':
        this.usage.set(message['usage'] as UsageSnapshot);
        break;

      case 'app.transcript.turn':
        this.transcript.update((turns) => [...turns, message['turn'] as TranscriptTurn]);
        break;

      case 'app.session.ended':
        this.usage.set(message['usage'] as UsageSnapshot);
        break;

      case 'app.error':
        this.errorMessage.set(String(message['message'] ?? 'Unbekannter Fehler.'));
        break;

      case 'error':
        this.errorMessage.set(this.describeApiError(message['error']));
        break;

      case 'input_audio_buffer.speech_started':
        // Barge-in: drop whatever the tutor still had queued.
        this.userSpeaking.set(true);
        this.player?.stop();
        this.tutorSpeaking.set(false);
        break;

      case 'input_audio_buffer.speech_stopped':
        this.userSpeaking.set(false);
        break;

      case 'response.done':
        this.tutorSpeaking.set(false);
        break;

      default:
        break;
    }
  }

  private sendAudio(chunk: ArrayBuffer): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(chunk);
    }
  }

  // --- timers ------------------------------------------------------------

  private startTimers(): void {
    this.startedAt = Date.now();
    this.timerId = setInterval(() => {
      this.elapsedSeconds.set(Math.floor((Date.now() - this.startedAt) / 1000));
    }, 1000);

    this.levelId = setInterval(() => {
      this.micLevel.set(this.recorder?.getLevel() ?? 0);
      if (this.player && !this.player.isPlaying) {
        this.tutorSpeaking.set(false);
      }
    }, LEVEL_POLL_MS);
  }

  private stopTimers(): void {
    if (this.timerId !== null) {
      clearInterval(this.timerId);
      this.timerId = null;
    }
    if (this.levelId !== null) {
      clearInterval(this.levelId);
      this.levelId = null;
    }
  }

  // --- errors ------------------------------------------------------------

  private describe(error: unknown): string {
    if (error instanceof DOMException && error.name === 'NotAllowedError') {
      return 'Kein Zugriff auf das Mikrofon. Bitte die Berechtigung im Browser erlauben.';
    }
    if (error instanceof DOMException && error.name === 'NotFoundError') {
      return 'Kein Mikrofon gefunden.';
    }
    return error instanceof Error ? error.message : 'Die Session konnte nicht gestartet werden.';
  }

  private describeApiError(error: unknown): string {
    if (error && typeof error === 'object' && 'message' in error) {
      return String((error as { message: unknown }).message);
    }
    return 'Die Realtime API hat einen Fehler gemeldet.';
  }
}
