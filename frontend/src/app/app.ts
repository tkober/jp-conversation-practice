import { Component, computed, inject, signal } from '@angular/core';

import { Conversation } from './conversation/conversation';
import { ApiService } from './core/api.service';
import { AnalysisResponse, JlptLevel } from './core/models';
import { RealtimeSessionService } from './core/realtime-session.service';
import { Review } from './review/review';
import { SessionSetup, Setup } from './setup/setup';

@Component({
  selector: 'app-root',
  imports: [Setup, Conversation, Review],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  private readonly api = inject(ApiService);
  protected readonly session = inject(RealtimeSessionService);

  protected readonly phase = this.session.phase;
  protected readonly analysis = signal<AnalysisResponse | null>(null);
  protected readonly analysisError = signal<string | null>(null);
  protected readonly finalElapsed = signal(0);

  private scenario = '';
  private jlptLevel: JlptLevel = 'N5';

  protected readonly isReviewing = computed(
    () => this.phase() === 'analysing' || this.phase() === 'review',
  );

  protected async onStart(setup: SessionSetup): Promise<void> {
    this.scenario = setup.scenario;
    this.jlptLevel = setup.jlptLevel;
    this.analysis.set(null);
    this.analysisError.set(null);
    await this.session.start({
      scenario: setup.scenario,
      jlptLevel: setup.jlptLevel,
      voice: setup.voice,
      speed: setup.speed,
    });
  }

  protected async onFinish(): Promise<void> {
    this.finalElapsed.set(this.session.elapsedSeconds());
    await this.session.stop();
    this.runAnalysis();
  }

  protected runAnalysis(): void {
    const transcript = this.session.transcript();
    if (transcript.length === 0) {
      this.analysisError.set(
        'Es wurde nichts aufgezeichnet. Für eine Auswertung braucht es mindestens einen Redebeitrag.',
      );
      this.session.phase.set('review');
      return;
    }

    this.analysisError.set(null);
    this.session.phase.set('analysing');

    this.api
      .analyse({
        scenario: this.scenario,
        jlpt_level: this.jlptLevel,
        transcript,
        use_wanikani_filter: true,
      })
      .subscribe({
        next: (result) => {
          this.analysis.set(result);
          this.session.phase.set('review');
        },
        error: (error: unknown) => {
          this.analysisError.set(this.describeError(error));
          this.session.phase.set('review');
        },
      });
  }

  protected onRestart(): void {
    this.analysis.set(null);
    this.analysisError.set(null);
    this.finalElapsed.set(0);
    this.session.reset();
    this.session.phase.set('setup');
  }

  private describeError(error: unknown): string {
    const detail = (error as { error?: { detail?: string } })?.error?.detail;
    return detail
      ? `Die Auswertung ist fehlgeschlagen: ${detail}`
      : 'Die Auswertung ist fehlgeschlagen.';
  }
}
