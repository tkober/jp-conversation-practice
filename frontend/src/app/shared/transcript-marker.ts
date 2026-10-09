import { Component, computed, input } from '@angular/core';

import { ContextEvent, HelpEvent } from '../core/models';

/**
 * A line in the transcript for something that happened but was not said.
 *
 * Shared by the session, review and history screens deliberately: the three
 * lay speech out differently — bubbles in one, compact lines in the other two
 * — but a わからない press is the same event everywhere, and it reads best when
 * it looks the same everywhere too.
 */
@Component({
  selector: 'app-transcript-marker',
  imports: [],
  template: `<p class="marker" [class.help]="event().type === 'help'">
    <span class="rule"></span>
    <span class="label">{{ label() }}</span>
    <span class="rule"></span>
  </p>`,
  styles: `
    .marker {
      display: flex;
      align-items: center;
      gap: 10px;
      margin: 10px 0;
      font-size: 12px;
      color: var(--text-faint);
    }

    .label {
      flex-shrink: 0;
    }

    .help .label {
      color: var(--warning);
    }

    .rule {
      flex: 1;
      height: 1px;
      background: var(--border);
    }
  `,
})
export class TranscriptMarker {
  readonly event = input.required<HelpEvent | ContextEvent>();

  protected readonly label = computed(() => {
    const event = this.event();
    if (event.type === 'help') {
      return `わからない · Stage ${event.stage} of ${event.max_stage}`;
    }
    return `Material shown: ${event.item.title || 'Material'}`;
  });
}
