import { Component, inject } from '@angular/core';

import { FuriganaService } from '../core/furigana.service';

/** Shows and hides the readings; every instance drives the same setting. */
@Component({
  selector: 'app-furigana-toggle',
  imports: [],
  template: `<button
    type="button"
    class="btn btn-ghost"
    [attr.aria-pressed]="enabled()"
    title="Lesungen über den Kanji ein- oder ausblenden"
    (click)="furigana.toggle()"
  >
    {{ enabled() ? 'Furigana ausblenden' : 'Furigana anzeigen' }}
  </button>`,
})
export class FuriganaToggle {
  protected readonly furigana = inject(FuriganaService);
  protected readonly enabled = this.furigana.enabled;
}
