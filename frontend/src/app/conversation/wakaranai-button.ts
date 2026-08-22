import { Component, computed, inject } from '@angular/core';

import { RealtimeSessionService } from '../core/realtime-session.service';

/**
 * わからない: one press tells the tutor you are stuck.
 *
 * A teacher notices when someone is out of their depth and eases off without
 * being asked; the model cannot see that, and asking for help in Japanese is
 * exactly what a stuck learner cannot do. Each press without saying anything
 * in between escalates the help one step — the backend owns the escalation,
 * this only shows where it stands.
 */
@Component({
  selector: 'app-wakaranai-button',
  imports: [],
  template: `<div class="help">
    <button
      type="button"
      class="btn jp"
      (click)="session.requestHelp()"
      [disabled]="!canRequest()"
      title="Sag der Lehrkraft, dass du gerade nicht weiterkommst"
    >
      わからない
    </button>
    <div class="text">
      <span>{{ hint() }}</span>
      <div class="steps" role="img" [attr.aria-label]="'Hilfestufe ' + stage() + ' von ' + max()">
        @for (step of steps(); track step) {
          <span class="step" [class.reached]="step <= stage()"></span>
        }
      </div>
    </div>
  </div>`,
  styles: `
    .help {
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 12px 16px;
      background: var(--bg-elevated);
      border: 1px solid var(--border);
      border-radius: var(--radius);
    }

    .btn {
      flex-shrink: 0;
      font-size: 17px;
      background: rgba(232, 182, 76, 0.12);
      border: 1px solid rgba(232, 182, 76, 0.4);
      color: var(--warning);

      &:hover:not(:disabled) {
        background: rgba(232, 182, 76, 0.2);
        border-color: var(--warning);
      }
    }

    .text {
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-size: 12.5px;
      color: var(--text-faint);
    }

    .steps {
      display: flex;
      gap: 5px;
    }

    .step {
      width: 22px;
      height: 4px;
      border-radius: 999px;
      background: var(--bg-input);

      &.reached {
        background: var(--warning);
      }
    }

    @media (max-width: 620px) {
      .help {
        flex-wrap: wrap;
      }
    }
  `,
})
export class WakaranaiButton {
  protected readonly session = inject(RealtimeSessionService);

  protected readonly stage = this.session.helpStage;
  protected readonly max = this.session.maxHelpStage;

  /** One marker per escalation step, so the button shows where it stands. */
  protected readonly steps = computed(() =>
    Array.from({ length: this.max() }, (_, index) => index + 1),
  );

  protected readonly canRequest = computed(
    () => this.session.phase() === 'live' && !this.session.helpPending(),
  );

  /**
   * The rate a help turn comes out at — the live tempo times the configured
   * factor, so it moves with the tempo slider. Empty when the factor is 1,
   * which switches the slowdown off.
   */
  private readonly slower = computed(() => {
    const factor = this.session.helpSpeedFactor();
    if (factor >= 1) {
      return '';
    }
    const rate = Math.max(this.session.speedMin(), this.session.speed() * factor);
    return `; die Hilfe kommt mit ${rate.toFixed(2)}×`;
  });

  protected readonly hint = computed(() => {
    if (this.session.helpPending()) {
      return 'Die Lehrkraft geht gerade darauf ein …';
    }
    const stage = this.stage();
    const max = this.max();
    if (stage === 0) {
      return `Drücken, wenn du nicht weiterkommst${this.slower()} — du musst nicht extra nach Hilfe fragen.`;
    }
    if (stage < max) {
      return `Stufe ${stage} von ${max} — noch mal drücken, wenn das nicht gereicht hat.`;
    }
    return `Stufe ${stage} von ${max} — mehr geht nicht, jetzt wird auf Deutsch erklärt.`;
  });
}
