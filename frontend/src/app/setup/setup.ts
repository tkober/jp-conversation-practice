import { Component, computed, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';

import { ApiService } from '../core/api.service';
import { RealtimeSessionService } from '../core/realtime-session.service';
import { HealthResponse, JlptLevel, Scenario, VoiceOption } from '../core/models';

const JLPT_LEVELS: { level: JlptLevel; label: string }[] = [
  { level: 'N5', label: 'Anfänger — einfache Sätze, langsames Tempo' },
  { level: 'N4', label: 'Fortgeschrittener Anfänger — Alltagsgespräche' },
  { level: 'N3', label: 'Mittelstufe — natürliches Tempo' },
  { level: 'N2', label: 'Obere Mittelstufe — muttersprachliches Tempo' },
];

export interface SessionSetup {
  /** The prompt the tutor runs with. */
  scenario: string;
  /** Row id, so the stored session can point back at the scenario. */
  scenarioId: number | null;
  scenarioTitle: string;
  jlptLevel: JlptLevel;
  voice: string;
  speed: number;
}

@Component({
  selector: 'app-setup',
  imports: [FormsModule, RouterLink],
  templateUrl: './setup.html',
  styleUrl: './setup.scss',
})
export class Setup {
  private readonly api = inject(ApiService);
  private readonly session = inject(RealtimeSessionService);

  readonly start = output<SessionSetup>();

  readonly levels = JLPT_LEVELS;
  readonly scenarios = signal<Scenario[]>([]);
  readonly health = signal<HealthResponse | null>(null);
  readonly backendUnreachable = signal(false);

  readonly voices = signal<VoiceOption[]>([]);
  readonly selectedVoice = signal('');
  readonly speed = signal(1);
  readonly speedMin = signal(0.6);
  readonly speedMax = signal(1.4);

  /** Voice whose sample is currently loading or playing, if any. */
  readonly samplePlaying = signal<string | null>(null);
  readonly sampleError = signal<string | null>(null);

  private sampleAudio: HTMLAudioElement | null = null;

  readonly selectedScenarioId = signal<number | null>(null);
  readonly customScenario = signal('');
  readonly jlptLevel = signal<JlptLevel>('N5');

  readonly selectedScenario = computed(() =>
    this.scenarios().find((item) => item.id === this.selectedScenarioId()) ?? null,
  );

  readonly effectiveScenario = computed(() => {
    const custom = this.customScenario().trim();
    return custom || this.selectedScenario()?.prompt || '';
  });

  /** True when the free-text field overrides the picked scenario. */
  readonly usingCustomText = computed(() => this.customScenario().trim().length > 0);

  readonly canStart = computed(
    () => this.effectiveScenario().length > 0 && this.health()?.openai_configured === true,
  );

  constructor() {
    this.api.scenarios().subscribe({
      next: (scenarios) => {
        this.scenarios.set(scenarios);
        this.selectedScenarioId.set(scenarios[0]?.id ?? null);
      },
      error: () => this.backendUnreachable.set(true),
    });

    this.api.health().subscribe({
      next: (response) => this.health.set(response),
      error: () => this.backendUnreachable.set(true),
    });

    this.api.voices().subscribe({
      next: (response) => {
        this.voices.set(response.voices);
        this.selectedVoice.set(response.default_voice);
        this.speed.set(response.default_speed);
        this.speedMin.set(response.speed_min);
        this.speedMax.set(response.speed_max);
        this.session.speedMin.set(response.speed_min);
        this.session.speedMax.set(response.speed_max);
      },
      error: () => this.backendUnreachable.set(true),
    });
  }

  /**
   * Play the spoken preview for one voice.
   *
   * The first request per voice is rendered server-side and cached, so it can
   * take a moment; afterwards it is instant.
   */
  playSample(voice: VoiceOption): void {
    this.sampleError.set(null);
    this.stopSample();

    const audio = new Audio(this.api.voiceSampleUrl(voice.id));
    this.sampleAudio = audio;
    this.samplePlaying.set(voice.id);

    audio.onended = () => this.clearSample(voice.id);
    audio.onerror = () => {
      this.sampleError.set(`Für „${voice.label}" konnte keine Hörprobe geladen werden.`);
      this.clearSample(voice.id);
    };

    void audio.play().catch(() => {
      this.sampleError.set('Die Hörprobe konnte nicht abgespielt werden.');
      this.clearSample(voice.id);
    });
  }

  private stopSample(): void {
    if (this.sampleAudio) {
      this.sampleAudio.pause();
      this.sampleAudio.onended = null;
      this.sampleAudio.onerror = null;
      this.sampleAudio = null;
    }
    this.samplePlaying.set(null);
  }

  private clearSample(voiceId: string): void {
    if (this.samplePlaying() === voiceId) {
      this.samplePlaying.set(null);
      this.sampleAudio = null;
    }
  }

  selectScenario(scenario: Scenario): void {
    this.selectedScenarioId.set(scenario.id);
    // Picking a scenario replaces whatever free text was there before.
    this.customScenario.set('');
  }

  onStart(): void {
    if (!this.canStart()) {
      return;
    }
    this.stopSample();
    const picked = this.usingCustomText() ? null : this.selectedScenario();
    this.start.emit({
      scenario: this.effectiveScenario(),
      scenarioId: picked?.id ?? null,
      scenarioTitle: picked?.title ?? 'Eigenes Szenario',
      jlptLevel: this.jlptLevel(),
      voice: this.selectedVoice(),
      speed: this.speed(),
    });
  }
}
