import { Component, computed, inject, input } from '@angular/core';

import { FuriganaService } from '../core/furigana.service';
import { RubySegment } from '../core/models';

/**
 * One line of Japanese, with the readings above the kanji when they are
 * switched on. Falls back to the plain text whenever the backend sent no
 * segments, so a line is never missing.
 *
 * The template is deliberately free of whitespace between the segments:
 * Japanese has no word spacing, and Angular would keep a literal newline as a
 * space inside the line.
 */
@Component({
  selector: 'app-furigana-text',
  imports: [],
  template: `@if (segments(); as parts) {
      @for (part of parts; track $index) {
        @if (part.reading) {
          <ruby>{{ part.text }}<rt>{{ part.reading }}</rt></ruby>
        } @else {
          <span>{{ part.text }}</span>
        }
      }
    } @else {
      <span>{{ text() }}</span>
    }`,
  host: { '[class.has-ruby]': 'segments() !== null' },
  styles: `
    :host {
      display: inline;
    }

    /* Ruby sits above the line, so the line box needs the room -- without it
       the readings crowd the line above. Only when they are actually shown. */
    :host(.has-ruby) {
      line-height: 2.1;
    }

    ruby {
      ruby-align: center;
    }

    rt {
      font-size: 0.52em;
      font-weight: 400;
      color: var(--text-muted);
      /* Copying a line should yield the sentence, not sentence-with-readings. */
      user-select: none;
    }
  `,
})
export class FuriganaText {
  private readonly furigana = inject(FuriganaService);

  readonly text = input.required<string>();
  readonly ruby = input<RubySegment[] | null | undefined>(null);

  protected readonly segments = computed(() =>
    this.furigana.enabled() ? (this.ruby() ?? null) : null,
  );
}
