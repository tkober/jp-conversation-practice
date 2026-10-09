import { Component, computed, inject, signal, viewChild } from '@angular/core';
import { SumiHanko, SumiPage } from 'sumi-ui/layout';
import { SumiSessionGate, SumiSessionSummary, SumiSummaryTile } from 'sumi-ui/practice';

import { Conversation } from '../conversation/conversation';
import { ApiService } from '../core/api.service';
import { AnalysisResponse, JlptLevel, isSpeech } from '../core/models';
import { RealtimeSessionService } from '../core/realtime-session.service';
import { Review } from '../review/review';
import { SessionSetup, Setup } from '../setup/setup';

@Component({
  selector: 'app-practice',
  imports: [
    Conversation,
    Review,
    Setup,
    SumiHanko,
    SumiPage,
    SumiSessionGate,
    SumiSessionSummary,
    SumiSummaryTile,
  ],
  templateUrl: './practice.html',
})
export class Practice {
  private readonly api = inject(ApiService);
  protected readonly session = inject(RealtimeSessionService);

  private readonly setupRef = viewChild(Setup);

  protected readonly phase = this.session.phase;
  protected readonly analysis = signal<AnalysisResponse | null>(null);
  protected readonly analysisError = signal<string | null>(null);
  protected readonly finalElapsed = signal(0);

  /**
   * Whether the T2 gate's "Show review"/Enter has already been used once
   * for this session's end. Stays `false` across the whole analysing phase
   * so the gate shows; once the user opens the review, further analysis
   * retries (phase flipping back to 'analysing') must *not* pull them back
   * to the gate — `app-review` keeps its own `[loading]` state for that,
   * exactly as it did before this screen existed (see practice.html).
   */
  protected readonly reviewOpened = signal(false);

  private scenario = '';
  private scenarioId: number | null = null;
  private scenarioTitle = '';
  private jlptLevel: JlptLevel = 'N5';
  /** Row id of the stored session, so the analysis can be attached to it. */
  private storedSessionId: number | null = null;

  /** T1/details step from the live `Setup` instance — see `Setup.step`. */
  protected readonly setupStep = computed(() => this.setupRef()?.step() ?? 'gate');
  protected readonly setupPageTitle = computed(() => this.setupRef()?.pageTitle());

  /** The T2 gate is ready once the analysis has either landed or failed. */
  protected readonly reviewReady = computed(
    () => this.analysis() !== null || this.analysisError() !== null,
  );

  protected readonly finalTurns = computed(
    () => this.session.transcript().filter(isSpeech).length,
  );
  protected readonly finalCost = computed(() => `$${this.session.usage().cost_usd.toFixed(4)}`);

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
      scenarioId: setup.scenarioId,
      jlptLevel: setup.jlptLevel,
      voice: setup.voice,
      speed: setup.speed,
      contextIds: setup.contextIds,
      material: setup.material,
    });
  }

  protected async onFinish(): Promise<void> {
    this.finalElapsed.set(this.session.elapsedSeconds());
    this.reviewOpened.set(false);
    await this.session.stop();
    this.storeSession();
    this.runAnalysis();
  }

  /**
   * Persist the conversation before the analysis runs.
   *
   * Storing first means a failed or slow analysis cannot cost the user their
   * transcript; the result is attached afterwards when it arrives.
   *
   * Any event is enough to be worth storing, not just speech. A learner who
   * pressed わからない four times and never managed to say anything had the
   * session most worth looking at, and it used to leave no trace at all.
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
        // Anything handed over mid-session is not in `instructions`, which
        // were built before it arrived, so the row needs it separately.
        context_items: this.session.contextItems(),
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

  /**
   * Runs (or retries) the analysis. Always lands on the `'analysing'` phase
   * first — the T2 gate shows while this is in flight the first time (see
   * `reviewOpened`); a retry triggered from the review screen itself just
   * flips `app-review`'s own `[loading]` instead, since `reviewOpened` is
   * already `true` by then.
   */
  protected runAnalysis(): void {
    const transcript = this.session.transcript();
    this.session.phase.set('analysing');

    // Speech, not events: a session of nothing but わからない presses is worth
    // storing (see storeSession) but there is nothing in it to give feedback
    // on. No delay here, so the T2 gate's action is enabled immediately.
    if (!transcript.some(isSpeech)) {
      this.analysisError.set(
        'Nothing was recorded. An analysis needs at least one turn of speech.',
      );
      return;
    }

    this.analysisError.set(null);

    this.api
      .analyse({
        scenario: this.scenario,
        jlpt_level: this.jlptLevel,
        transcript,
        use_wanikani_filter: true,
        // これください is unreadable feedback without the menu これ pointed at.
        context_items: this.session.contextItems(),
      })
      .subscribe({
        next: (result) => {
          this.analysis.set(result);
          this.attachAnalysis(result);
        },
        error: (error: unknown) => {
          this.analysisError.set(this.describeError(error));
        },
      });
  }

  /** T2 gate's `(start)`/summary's `(restart)` — both open the review. */
  protected openReview(): void {
    this.reviewOpened.set(true);
    this.session.phase.set('review');
  }

  protected onRestart(): void {
    this.analysis.set(null);
    this.analysisError.set(null);
    this.finalElapsed.set(0);
    this.reviewOpened.set(false);
    this.session.reset();
    this.session.phase.set('setup');
  }

  private describeError(error: unknown): string {
    const detail = (error as { error?: { detail?: string } })?.error?.detail;
    return detail
      ? `The analysis failed: ${detail}`
      : 'The analysis failed.';
  }
}
