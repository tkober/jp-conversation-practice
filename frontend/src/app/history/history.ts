import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';

import { ApiService } from '../core/api.service';
import {
  EAGERNESS_OPTIONS,
  SessionDetail,
  SessionStats,
  SessionSummary,
  withoutFurigana,
} from '../core/models';
import { FuriganaText } from '../shared/furigana-text';
import { FuriganaToggle } from '../shared/furigana-toggle';

@Component({
  selector: 'app-history',
  imports: [DatePipe, DecimalPipe, FuriganaText, FuriganaToggle],
  templateUrl: './history.html',
  styleUrl: './history.scss',
})
export class History {
  private readonly api = inject(ApiService);

  readonly sessions = signal<SessionSummary[]>([]);
  readonly stats = signal<SessionStats | null>(null);
  readonly error = signal<string | null>(null);

  /** Detail of the expanded row, loaded on demand. */
  readonly openId = signal<number | null>(null);
  readonly detail = signal<SessionDetail | null>(null);
  readonly detailLoading = signal(false);
  readonly confirmingDelete = signal<number | null>(null);

  readonly totalMinutes = computed(() => Math.round((this.stats()?.total_seconds ?? 0) / 60));

  constructor() {
    this.reload();
  }

  reload(): void {
    this.api.sessions().subscribe({
      next: (sessions) => this.sessions.set(sessions),
      error: (error: unknown) => this.error.set(this.describe(error)),
    });
    this.api.sessionStats().subscribe({
      next: (stats) => this.stats.set(stats),
      error: () => undefined,
    });
  }

  toggle(session: SessionSummary): void {
    if (this.openId() === session.id) {
      this.openId.set(null);
      this.detail.set(null);
      return;
    }

    this.openId.set(session.id);
    this.detail.set(null);
    this.detailLoading.set(true);
    this.api.session(session.id).subscribe({
      next: (detail) => {
        this.detail.set(detail);
        this.detailLoading.set(false);
      },
      error: (error: unknown) => {
        this.detailLoading.set(false);
        this.error.set(this.describe(error));
      },
    });
  }

  askDelete(session: SessionSummary, event: Event): void {
    event.stopPropagation();
    this.confirmingDelete.set(session.id);
  }

  cancelDelete(event: Event): void {
    event.stopPropagation();
    this.confirmingDelete.set(null);
  }

  confirmDelete(session: SessionSummary, event: Event): void {
    event.stopPropagation();
    this.api.deleteSession(session.id).subscribe({
      next: () => {
        this.confirmingDelete.set(null);
        if (this.openId() === session.id) {
          this.openId.set(null);
          this.detail.set(null);
        }
        this.reload();
      },
      error: (error: unknown) => this.error.set(this.describe(error)),
    });
  }

  /** Download one stored session as JSON, prompt included. */
  downloadJson(detail: SessionDetail, event: Event): void {
    event.stopPropagation();
    const payload = { ...detail, transcript: withoutFurigana(detail.transcript) };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `jp-session-${detail.id}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  /** The stored eagerness is the API's wording; the UI shows the German label. */
  eagernessLabel(eagerness: string): string {
    return EAGERNESS_OPTIONS.find((option) => option.id === eagerness)?.label ?? eagerness;
  }

  duration(seconds: number): string {
    const minutes = Math.floor(seconds / 60)
      .toString()
      .padStart(2, '0');
    const rest = Math.floor(seconds % 60)
      .toString()
      .padStart(2, '0');
    return `${minutes}:${rest}`;
  }

  private describe(error: unknown): string {
    const detail = (error as { error?: { detail?: string } })?.error?.detail;
    return detail ? `Fehler: ${detail}` : 'Der Verlauf konnte nicht geladen werden.';
  }
}
