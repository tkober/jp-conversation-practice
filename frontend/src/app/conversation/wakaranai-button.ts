import { Component, computed, inject } from '@angular/core';
import { SUMI_KEYS, detectPlatform, formatKeys, injectHotkey } from 'sumi-ui/core';
import { SumiButtonDirective, SumiKbdDirective } from 'sumi-ui/forms';

import { RealtimeSessionService } from '../core/realtime-session.service';

/**
 * わからない: one press tells the tutor you are stuck.
 *
 * A teacher notices when someone is out of their depth and eases off without
 * being asked; the model cannot see that, and asking for help in Japanese is
 * exactly what a stuck learner cannot do. Each press without saying anything
 * in between escalates the help one step — the backend owns the escalation,
 * this only shows where it stands.
 *
 * The button itself is `button[sumiButton]` (sumi-ui#17) rather than the
 * app's own `.btn` — there is no library variant for the amber "help"
 * meaning the old hand-styled button had (only primary/secondary/ghost/
 * danger exist, see `sumi-ui` `forms/button.ts` and
 * `styles/components/_button.scss`), so this picks `secondary` and reports
 * the gap in the implementation report instead of hand-styling a colour
 * back in. The step dots below keep `--sumi-retry` — that is the library's
 * own feedback-colour token, not a new one.
 */
@Component({
  selector: 'app-wakaranai-button',
  imports: [SumiButtonDirective, SumiKbdDirective],
  template: `<div class="help">
    <button
      type="button"
      sumiButton
      variant="secondary"
      lang="ja"
      (click)="session.requestHelp()"
      [disabled]="!canRequest()"
      title="Tell the tutor you're stuck right now"
    >
      わからない
      <span class="keys" aria-hidden="true">
        @for (key of keyParts; track $index) {
          <kbd sumiKbd>{{ key }}</kbd>
        }
      </span>
    </button>
    <div class="text">
      <span>{{ hint() }}</span>
      <div class="steps" role="img" [attr.aria-label]="'Help stage ' + stage() + ' of ' + max()">
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
      background: var(--sumi-surface);
      border: 1px solid var(--sumi-line);
      border-radius: var(--sumi-radius);
    }

    .keys {
      display: inline-flex;
      gap: 3px;
      margin-left: 8px;
    }

    .text {
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-size: 12.5px;
      color: var(--sumi-muted);
    }

    .steps {
      display: flex;
      gap: 5px;
    }

    .step {
      width: 22px;
      height: 4px;
      border-radius: 999px;
      background: var(--sumi-sunken);

      &.reached {
        background: var(--sumi-retry);
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

  /** `['Alt', 'H']` or `['⌥', 'H']` — rendered as one `sumi-kbd` per part,
   *  the same split `sumi-hotkey-help` itself uses. */
  protected readonly keyParts = formatKeys(SUMI_KEYS.iDontKnow, detectPlatform());

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
    return `; the help comes at ${rate.toFixed(2)}×`;
  });

  protected readonly hint = computed(() => {
    if (this.session.helpPending()) {
      return 'The tutor is responding to that…';
    }
    const stage = this.stage();
    const max = this.max();
    if (stage === 0) {
      return `Press when you're stuck${this.slower()} — you don't have to ask for help in words.`;
    }
    if (stage < max) {
      return `Stage ${stage} of ${max} — press again if that wasn't enough.`;
    }
    return `Stage ${stage} of ${max} — that's the limit, the tutor now explains in English.`;
  });

  constructor() {
    injectHotkey({
      keys: SUMI_KEYS.iDontKnow,
      label: 'わからない — ask for help',
      scope: 'practice',
      enabled: () => this.canRequest(),
      handler: () => this.session.requestHelp(),
    });
  }
}
