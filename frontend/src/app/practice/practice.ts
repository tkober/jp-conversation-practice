import { Component, computed, inject, signal } from '@angular/core';

import { Conversation } from '../conversation/conversation';
import { ApiService } from '../core/api.service';
import { AnalysisResponse, JlptLevel } from '../core/models';
import { RealtimeSessionService } from '../core/realtime-session.service';
import { Review } from '../review/review';
import { SessionSetup, Setup } from '../setup/setup';

@Component({
  selector: 'app-practice',
  imports: [Setup, Conversation, Review],
  templateUrl: './practice.html',
  styleUrl: './practice.scss',
})
export class Practice {
  private readonly api = inject(ApiService);
  protected readonly session = inject(RealtimeSessionService);

  protected readonly phase = this.session.phase;
  protected readonly analysis = signal<AnalysisResponse | null>(null);
  protected readonly analysisError = signal<string | null>(null);
  protected readonly finalElapsed = signal(0);

  private scenario = '';
  private scenarioId: number | null = null;
  private scenarioTitle = '';
  private jlptLevel: JlptLevel = 'N5';
  /** Row id of the stored session, so the analysis can be attached to it. */
  private storedSessionId: number | null = null;

  protected readonly isReviewing = computed(
    () => this.phase() === 'analysing' || this.phase() === 'review',
  );

  protected async onStart(setup: SessionSetup): Promise<void> {
    this.scenario = setup.scenario;
    this.scenarioId = setup.scenarioId;
    this.scenarioTitle = setup.scenarioTitle;
    this.jlptLevel = setup.jlptLevel;
    this.storedSessionId = null;
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
    this.storeSession();
    this.runAnalysis();
  }

  /**
   * Persist the conversation before the analysis runs.
   *
   * Storing first means a failed or slow analysis cannot cost the user their
   * transcript; the result is attached afterwards when it arrives.
   */
  private storeSession(): void {
    const transcript = this.session.transcript();
    if (transcript.length === 0) {
      return;
    }
    const info = this.session.sessionInfo();

    this.api
      .saveSession({
        scenario_id: this.scenarioId,
        scenario_title: this.scenarioTitle,
        scenario_prompt: this.scenario,
        jlpt_level: this.jlptLevel,
        model: info?.model ?? '',
        voice: info?.voice ?? '',
        speed: info?.speed ?? 1,
        vad_eagerness: info?.vad_eagerness ?? '',
        instructions: info?.instructions ?? '',
        duration_seconds: this.finalElapsed(),
        cost_usd: this.session.usage().cost_usd,
        usage: this.session.usage(),
        transcript,
      })
      .subscribe({
        next: (stored) => {
          this.storedSessionId = stored.id;
          const analysis = this.analysis();
          // The analysis may already have arrived while this was in flight.
          if (analysis) {
            this.attachAnalysis(analysis);
          }
        },
        error: (error: unknown) => console.warn('Session not stored', error),
      });
  }

  private attachAnalysis(analysis: AnalysisResponse): void {
    if (this.storedSessionId === null) {
      return;
    }
    this.api.attachAnalysis(this.storedSessionId, analysis).subscribe({
      error: (error: unknown) => console.warn('Analysis not stored', error),
    });
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
          this.attachAnalysis(result);
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
