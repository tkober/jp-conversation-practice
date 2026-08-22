import { Component, computed, inject, input, output, signal } from '@angular/core';

import { ApiService } from '../core/api.service';
import {
  AnalysisResponse,
  SessionExport,
  SessionInfo,
  TranscriptTurn,
  UsageSnapshot,
} from '../core/models';

type ExportState = 'idle' | 'running' | 'done' | 'error';

@Component({
  selector: 'app-review',
  imports: [],
  templateUrl: './review.html',
  styleUrl: './review.scss',
})
export class Review {
  private readonly api = inject(ApiService);

  readonly analysis = input<AnalysisResponse | null>(null);
  readonly analysisError = input<string | null>(null);
  readonly loading = input(false);
  readonly usage = input.required<UsageSnapshot>();
  readonly transcript = input.required<TranscriptTurn[]>();
  readonly elapsedSeconds = input(0);
  readonly sessionInfo = input<SessionInfo | null>(null);

  readonly restart = output<void>();
  readonly retryAnalysis = output<void>();

  /** Expressions the user unticked; everything else is exported. */
  private readonly deselected = signal<ReadonlySet<string>>(new Set());

  readonly exportState = signal<ExportState>('idle');
  readonly exportMessage = signal<string | null>(null);
  readonly showTranscript = signal(false);
  readonly copyState = signal<'idle' | 'done' | 'error'>('idle');

  readonly selectedCards = computed(() => {
    const skipped = this.deselected();
    return this.analysis()?.anki_cards.filter((card) => !skipped.has(card.expression)) ?? [];
  });

  readonly formattedCost = computed(() => `$${this.usage().cost_usd.toFixed(4)}`);

  readonly formattedTime = computed(() => {
    const total = this.elapsedSeconds();
    const minutes = Math.floor(total / 60)
      .toString()
      .padStart(2, '0');
    const seconds = Math.floor(total % 60)
      .toString()
      .padStart(2, '0');
    return `${minutes}:${seconds}`;
  });

  isSelected(expression: string): boolean {
    return !this.deselected().has(expression);
  }

  toggleCard(expression: string): void {
    this.deselected.update((current) => {
      const next = new Set(current);
      if (next.has(expression)) {
        next.delete(expression);
      } else {
        next.add(expression);
      }
      return next;
    });
  }

  exportToAnki(): void {
    const cards = this.selectedCards();
    if (cards.length === 0 || this.exportState() === 'running') {
      return;
    }

    this.exportState.set('running');
    this.exportMessage.set(null);

    this.api.exportToAnki(cards, ['ai-conversation']).subscribe({
      next: (response) => {
        this.exportState.set('done');
        const duplicates =
          response.duplicates > 0 ? ` (${response.duplicates} Duplikate übersprungen)` : '';
        this.exportMessage.set(
          `${response.added} Karten in „${response.deck_name}" angelegt${duplicates}.`,
        );
      },
      error: (error: unknown) => {
        this.exportState.set('error');
        this.exportMessage.set(this.describeError(error));
      },
    });
  }

  /**
   * Assemble the whole session as one JSON document.
   *
   * Includes the system prompt the tutor actually ran with: a transcript alone
   * rarely explains *why* a conversation went sideways, and the prompt is
   * usually where the answer is.
   */
  private buildExport(): SessionExport {
    const info = this.sessionInfo();
    return {
      exported_at: new Date().toISOString(),
      scenario: info?.scenario ?? '',
      jlpt_level: info?.jlpt_level ?? '',
      model: info?.model ?? this.usage().model,
      voice: info?.voice ?? '',
      speed: info?.speed ?? 1,
      vad_eagerness: info?.vad_eagerness ?? '',
      system_instructions: info?.instructions ?? '',
      duration_seconds: this.elapsedSeconds(),
      usage: this.usage(),
      transcript: this.transcript(),
      analysis: this.analysis(),
    };
  }

  downloadJson(): void {
    const payload = this.buildExport();
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `jp-session-${payload.exported_at.slice(0, 19).replace(/:/g, '-')}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  async copyJson(): Promise<void> {
    try {
      await navigator.clipboard.writeText(JSON.stringify(this.buildExport(), null, 2));
      this.copyState.set('done');
    } catch {
      this.copyState.set('error');
    }
    setTimeout(() => this.copyState.set('idle'), 2500);
  }

  private describeError(error: unknown): string {
    // The backend speaks English; keep the user-facing lead-in German and
    // append the technical detail so the cause stays visible.
    const detail = (error as { error?: { detail?: string } })?.error?.detail;
    return detail
      ? `Export fehlgeschlagen: ${detail}`
      : 'Export fehlgeschlagen. Läuft Anki mit dem AnkiConnect-Add-on?';
  }
}
