import { Component, computed, inject, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { SumiButtonDirective, SumiSelectDirective } from 'sumi-ui/forms';
import { SumiBanner } from 'sumi-ui/layout';
import { SumiSessionGate } from 'sumi-ui/practice';

import { ApiService } from '../core/api.service';
import { microphoneBlockedReason } from '../core/audio-recorder';
import { RealtimeSessionService } from '../core/realtime-session.service';
import { MaterialPicker } from './material-picker';
import { Attachment, HealthResponse, JlptLevel, Scenario, VoiceOption } from '../core/models';

const JLPT_LEVELS: { level: JlptLevel; label: string }[] = [
  { level: 'N5', label: 'Beginner — simple sentences, slow pace' },
  { level: 'N4', label: 'Advanced beginner — everyday conversations' },
  { level: 'N3', label: 'Intermediate — natural pace' },
  { level: 'N2', label: 'Upper intermediate — native pace' },
];

/** Sentinel `<select>` value for the "Your own scenario…" option. */
const OWN_SCENARIO = 'own';

export interface SessionSetup {
  /** The prompt the tutor runs with. */
  scenario: string;
  /** Row id, so the stored session can point back at the scenario. */
  scenarioId: number | null;
  scenarioTitle: string;
  jlptLevel: JlptLevel;
  voice: string;
  speed: number;
  /** Everything the scenario has, so the session screen can show it. */
  material: Attachment[];
  /** The subset the tutor knows about from the first turn. */
  contextIds: number[];
}

/** Which of the two steps is on screen — see `Practice`'s outer `sumi-page`,
 *  which reads `step`/`pageTitle` to pick its title and ink. */
export type SetupStep = 'gate' | 'details';

@Component({
  selector: 'app-setup',
  imports: [FormsModule, MaterialPicker, RouterLink, SumiBanner, SumiButtonDirective, SumiSelectDirective, SumiSessionGate],
  templateUrl: './setup.html',
  styleUrl: './setup.scss',
})
export class Setup {
  private readonly api = inject(ApiService);
  private readonly session = inject(RealtimeSessionService);

  readonly start = output<SessionSetup>();

  /**
   * T1 (scenario pick) vs. the details step (material/level/voice/own-text)
   * — one component instance for both, so "Change scenario" going back to
   * T1 keeps whatever was already picked instead of resetting it (there is
   * no persistence to invent here: the component simply never unmounts
   * between the two steps, see `practice.html`).
   */
  readonly step = signal<SetupStep>('gate');

  readonly levels = JLPT_LEVELS;
  readonly scenarios = signal<Scenario[]>([]);
  readonly health = signal<HealthResponse | null>(null);
  readonly backendUnreachable = signal(false);
  /** Set when the browsing context has no microphone to offer at all. */
  readonly microphoneBlocked = signal(microphoneBlockedReason());
  /** Why the previous attempt to go live failed, if one did. */
  readonly startError = this.session.errorMessage;

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
  /** True once "Your own scenario…" is picked in the T1 select. */
  readonly customPicked = signal(false);
  readonly customScenario = signal('');
  readonly jlptLevel = signal<JlptLevel>('N5');

  /**
   * What this run takes along, and which of it starts in the tutor's prompt.
   * Owned here rather than in the picker so `onStart` can read it, seeded by
   * the picker from the scenario's pre-selection.
   */
  readonly pickedMaterial = signal<ReadonlySet<number>>(new Set());
  readonly startingIds = signal<ReadonlySet<number>>(new Set());
  readonly material = signal<Attachment[]>([]);

  readonly selectedScenario = computed(() =>
    this.scenarios().find((item) => item.id === this.selectedScenarioId()) ?? null,
  );

  /** `<select>`'s own value: the sentinel, or the scenario id as a string. */
  readonly selectedOption = computed(() =>
    this.customPicked() ? OWN_SCENARIO : String(this.selectedScenarioId() ?? ''),
  );

  readonly effectiveScenario = computed(() => {
    if (this.customPicked()) {
      return this.customScenario().trim();
    }
    return this.selectedScenario()?.prompt ?? '';
  });

  /** The details step's `sumi-page` title (see `Practice`). */
  readonly pageTitle = computed(
    () => (this.customPicked() ? 'Your own scenario' : this.selectedScenario()?.title) ??
      'Your own scenario',
  );

  /**
   * Blocks T1's "Continue", mirrored in its `actionDisabled` so `Enter`
   * cannot jump ahead either. Only the two conditions that make *any*
   * session impossible — a missing microphone only matters once "Start
   * conversation" is actually pressed on the details step, which `canStart`
   * still guards.
   */
  readonly gateBlocked = computed(
    () => this.backendUnreachable() || this.health()?.openai_configured === false,
  );

  readonly canStart = computed(
    () =>
      this.effectiveScenario().length > 0 &&
      this.health()?.openai_configured === true &&
      this.microphoneBlocked() === null,
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
      this.sampleError.set(`Could not load a preview for "${voice.label}".`);
      this.clearSample(voice.id);
    };

    void audio.play().catch(() => {
      this.sampleError.set('The preview could not be played.');
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

  /** Material the tutor gets, in the order the picker shows it. */
  private takenMaterial(): Attachment[] {
    const picked = this.pickedMaterial();
    return this.material().filter((item) => picked.has(item.id));
  }

  selectScenario(scenario: Scenario): void {
    this.customPicked.set(false);
    this.selectedScenarioId.set(scenario.id);
  }

  /** T1's `<select>` change handler — `OWN_SCENARIO` or a scenario id. */
  onScenarioOptionChange(value: string): void {
    if (value === OWN_SCENARIO) {
      this.customPicked.set(true);
      return;
    }
    this.customPicked.set(false);
    this.selectedScenarioId.set(Number(value));
  }

  /** T1's "Continue" — moves to the details step without starting a session. */
  goToDetails(): void {
    if (this.gateBlocked()) {
      return;
    }
    this.step.set('details');
  }

  /** Details step's "Change scenario" — back to T1, choices kept. */
  backToGate(): void {
    this.step.set('gate');
  }

  onStart(): void {
    if (!this.canStart()) {
      return;
    }
    this.stopSample();
    const picked = this.customPicked() ? null : this.selectedScenario();
    this.start.emit({
      scenario: this.effectiveScenario(),
      scenarioId: picked?.id ?? null,
      scenarioTitle: picked?.title ?? 'Custom scenario',
      jlptLevel: this.jlptLevel(),
      voice: this.selectedVoice(),
      speed: this.speed(),
      material: this.takenMaterial(),
      contextIds: this.takenMaterial()
        .filter((item) => this.startingIds().has(item.id))
        .map((item) => item.id),
    });
  }
}
