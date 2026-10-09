import { formatNumber } from '@angular/common';
import {
  Component,
  ElementRef,
  LOCALE_ID,
  computed,
  effect,
  inject,
  output,
  signal,
  viewChild,
} from '@angular/core';
import { SUMI_KEYS, injectHotkey } from 'sumi-ui/core';
import { SumiBadge, type SumiBadgeTone, SumiBanner } from 'sumi-ui/layout';
import { SumiDataTable, SumiStatTile, type SumiTableColumn } from 'sumi-ui/charts';
import { SumiButtonDirective, SumiSelectDirective, SumiSliderDirective } from 'sumi-ui/forms';
import { SumiFuriganaText, SumiFuriganaToggle } from 'sumi-ui/practice';

import { EAGERNESS_OPTIONS, VadEagerness, asMarker, asSpeech, toFuriganaSegments } from '../core/models';
import { RealtimeSessionService } from '../core/realtime-session.service';
import { TranscriptMarker } from '../shared/transcript-marker';
import { ContextPanel } from './context-panel';
import { WakaranaiButton } from './wakaranai-button';

@Component({
  selector: 'app-conversation',
  imports: [
    ContextPanel,
    SumiBadge,
    SumiBanner,
    SumiButtonDirective,
    SumiDataTable,
    SumiFuriganaText,
    SumiFuriganaToggle,
    SumiSelectDirective,
    SumiSliderDirective,
    SumiStatTile,
    TranscriptMarker,
    WakaranaiButton,
  ],
  templateUrl: './conversation.html',
  styleUrl: './conversation.scss',
})
export class Conversation {
  private readonly session = inject(RealtimeSessionService);
  private readonly locale = inject(LOCALE_ID);

  readonly finish = output<void>();

  readonly phase = this.session.phase;
  readonly transcript = this.session.transcript;
  readonly usage = this.session.usage;
  readonly muted = this.session.muted;
  readonly micLevel = this.session.micLevel;
  readonly tutorSpeaking = this.session.tutorSpeaking;
  readonly userSpeaking = this.session.userSpeaking;
  readonly errorMessage = this.session.errorMessage;
  readonly speed = this.session.speed;
  readonly speedMin = this.session.speedMin;
  readonly speedMax = this.session.speedMax;
  readonly eagerness = this.session.eagerness;
  readonly eagernessOptions = EAGERNESS_OPTIONS;
  readonly sessionInfo = this.session.sessionInfo;

  readonly showTokenDetails = signal(false);

  readonly tokenColumns: SumiTableColumn[] = [
    { key: 'row', label: '' },
    { key: 'audio', label: 'Audio', align: 'end' },
    { key: 'text', label: 'Text', align: 'end' },
    { key: 'cached', label: 'Cached', align: 'end' },
  ];

  readonly tokenRows = computed(() => {
    const usage = this.usage();
    const n = (value: number) => formatNumber(value, this.locale);
    return [
      {
        row: 'Input',
        audio: n(usage.input.audio_tokens),
        text: n(usage.input.text_tokens),
        cached: n(usage.input.cached_audio_tokens + usage.input.cached_text_tokens),
      },
      {
        row: 'Output',
        audio: n(usage.output.audio_tokens),
        text: n(usage.output.text_tokens),
        cached: '—',
      },
    ];
  });

  // Template narrowing for the transcript's event union; see models.ts.
  protected readonly asSpeech = asSpeech;
  protected readonly asMarker = asMarker;
  protected readonly toFuriganaSegments = toFuriganaSegments;

  private readonly scrollBox = viewChild<ElementRef<HTMLElement>>('scrollBox');

  readonly formattedCost = computed(() => `$${this.usage().cost_usd.toFixed(4)}`);

  readonly formattedTime = computed(() => {
    const total = this.session.elapsedSeconds();
    const minutes = Math.floor(total / 60)
      .toString()
      .padStart(2, '0');
    const seconds = (total % 60).toString().padStart(2, '0');
    return `${minutes}:${seconds}`;
  });

  readonly costPerMinute = computed(() => {
    const seconds = this.session.elapsedSeconds();
    if (seconds < 10) {
      return null;
    }
    return (this.usage().cost_usd / seconds) * 60;
  });

  readonly costHint = computed(() => {
    const rate = this.costPerMinute();
    return rate === null ? undefined : `≈ $${rate.toFixed(3)} / min`;
  });

  readonly durationHint = computed(() => `${this.usage().response_count} responses`);

  readonly statusLabel = computed(() => {
    if (this.phase() === 'connecting') {
      return 'Connecting…';
    }
    if (this.muted()) {
      return 'Microphone muted';
    }
    if (this.tutorSpeaking()) {
      return 'Tutor is speaking';
    }
    if (this.userSpeaking()) {
      return 'You are speaking';
    }
    return 'Listening…';
  });

  /**
   * The accent marks whoever is speaking; muted takes the retry hue as a
   * notice. Wrong/correct stay reserved for judgements (the feedback
   * language shared with the other apps), never for a live indicator.
   */
  readonly statusTone = computed<SumiBadgeTone>(() => {
    if (this.muted()) {
      return 'retry';
    }
    if (this.tutorSpeaking() || this.userSpeaking()) {
      return 'accent';
    }
    return 'neutral';
  });

  readonly micBarWidth = computed(() => `${Math.round(this.micLevel() * 100)}%`);

  constructor() {
    // Keep the newest turn in view as the transcript grows.
    effect(() => {
      this.transcript();
      const element = this.scrollBox()?.nativeElement;
      if (element) {
        queueMicrotask(() => {
          element.scrollTop = element.scrollHeight;
        });
      }
    });

    injectHotkey({
      keys: SUMI_KEYS.mute,
      label: 'Mute / unmute microphone',
      scope: 'practice',
      handler: () => this.toggleMute(),
    });
  }

  toggleMute(): void {
    this.session.toggleMute();
  }

  onSpeedChange(value: string): void {
    this.session.setSpeed(Number(value));
  }

  onEagernessChange(value: string): void {
    this.session.setEagerness(value as VadEagerness);
  }

  onFinish(): void {
    this.finish.emit();
  }
}
