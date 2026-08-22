import { DecimalPipe } from '@angular/common';
import {
  Component,
  ElementRef,
  computed,
  effect,
  inject,
  output,
  signal,
  viewChild,
} from '@angular/core';

import { EAGERNESS_OPTIONS, VadEagerness } from '../core/models';
import { RealtimeSessionService } from '../core/realtime-session.service';
import { FuriganaText } from '../shared/furigana-text';
import { FuriganaToggle } from '../shared/furigana-toggle';

@Component({
  selector: 'app-conversation',
  imports: [DecimalPipe, FuriganaText, FuriganaToggle],
  templateUrl: './conversation.html',
  styleUrl: './conversation.scss',
})
export class Conversation {
  private readonly session = inject(RealtimeSessionService);

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

  readonly statusLabel = computed(() => {
    if (this.phase() === 'connecting') {
      return 'Verbinde …';
    }
    if (this.muted()) {
      return 'Mikrofon stumm';
    }
    if (this.tutorSpeaking()) {
      return 'Lehrkraft spricht';
    }
    if (this.userSpeaking()) {
      return 'Du sprichst';
    }
    return 'Zuhören …';
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
