import { Component, computed, inject, signal } from '@angular/core';
import { SumiButtonDirective } from 'sumi-ui/forms';
import { SumiCard } from 'sumi-ui/layout';

import { ApiService } from '../core/api.service';
import { Attachment } from '../core/models';
import { RealtimeSessionService } from '../core/realtime-session.service';

/**
 * The material the learner is looking at while they talk.
 *
 * This panel is not decoration. A description of a menu that only the tutor
 * has is a menu nobody can point at — これ and その赤いの only work if both
 * sides are looking at the same thing, and that is the whole reason the
 * feature exists. Everything the tutor was told about is on screen here.
 *
 * The rest of the scenario's material sits below as buttons: pressing one
 * hands it over mid-conversation, the way a waiter brings the menu.
 */
@Component({
  selector: 'app-context-panel',
  imports: [SumiButtonDirective, SumiCard],
  template: `@if (ready() && (visible().length || pending().length)) {
    <sumi-card class="material">
      @if (visible().length) {
        <div class="shown">
          @for (item of visible(); track item.id) {
            <figure class="item" [class.text]="item.kind === 'text'">
              @if (item.kind === 'image') {
                <button type="button" class="thumb" (click)="enlarge(item)">
                  <img [src]="fileUrl(item.id)" [alt]="item.title" />
                </button>
              } @else {
                <pre class="body" lang="ja">{{ item.body }}</pre>
              }
              <figcaption>{{ item.title || 'Material' }}</figcaption>
            </figure>
          }
        </div>
      }

      @if (pending().length) {
        <div class="pending">
          <span class="pending-label">Not shown yet:</span>
          @for (item of pending(); track item.id) {
            <button
              type="button"
              sumiButton
              variant="secondary"
              size="sm"
              [disabled]="!canHandOver()"
              [title]="'Show the tutor: ' + (item.title || 'Material')"
              (click)="handOver(item)"
            >
              + {{ item.title || 'Material' }}
            </button>
          }
        </div>
      }
    </sumi-card>
  }

  @if (zoomed(); as item) {
    <div class="overlay" (click)="zoomed.set(null)">
      <img [src]="fileUrl(item.id)" [alt]="item.title" />
      <span class="overlay-hint">{{ item.title }} — click anywhere to close</span>
    </div>
  }`,
  styles: `
    // sumi-card's own body is a plain padded block; this piece needs a
    // flex column instead (the pending-material row sits right under the
    // shown-material row with a smaller gap than the card's own padding),
    // so it reaches into its own body the same way sumi-card's stylesheet
    // reaches into its header/footer slots.
    .material ::ng-deep .sumi-card__body {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    .shown {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
    }

    .item {
      display: flex;
      flex-direction: column;
      gap: 4px;
      margin: 0;
      max-width: 180px;
    }

    .item.text {
      max-width: 260px;
    }

    figcaption {
      font-size: 12px;
      color: var(--sumi-muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .thumb {
      padding: 0;
      border: 1px solid var(--sumi-line);
      border-radius: 6px;
      overflow: hidden;
      background: var(--sumi-sunken);
      cursor: zoom-in;

      &:hover {
        border-color: var(--sumi-accent-ink);
      }

      img {
        display: block;
        width: 100%;
        max-height: 130px;
        object-fit: cover;
      }
    }

    .body {
      margin: 0;
      padding: 8px 10px;
      max-height: 130px;
      overflow: auto;
      font-size: 13px;
      line-height: 1.5;
      white-space: pre-wrap;
      background: var(--sumi-sunken);
      border: 1px solid var(--sumi-line);
      border-radius: 6px;
    }

    .pending {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
    }

    .pending-label {
      font-size: 12.5px;
      color: var(--sumi-muted);
    }

    /* Deliberately not a token: a lightbox backdrop behind an enlarged photo
       is dark either way, for contrast with the image — it is independent
       of the page's own light/dark theme, not a surface that should follow it. */
    .overlay {
      position: fixed;
      inset: 0;
      z-index: 50;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 12px;
      padding: 24px;
      background: rgba(0, 0, 0, 0.82);
      cursor: zoom-out;

      img {
        max-width: 100%;
        max-height: calc(100vh - 96px);
        object-fit: contain;
        border-radius: 6px;
      }
    }

    .overlay-hint {
      font-size: 12.5px;
      color: #fff;
    }
  `,
})
export class ContextPanel {
  private readonly api = inject(ApiService);
  private readonly session = inject(RealtimeSessionService);

  protected readonly zoomed = signal<Attachment | null>(null);

  /** Material the tutor knows about, in the order it reached the prompt. */
  protected readonly visible = computed(() => {
    const known = this.session.contextItems().map((item) => item.id);
    const byId = new Map(this.session.material().map((item) => [item.id, item]));
    return known.map((id) => byId.get(id)).filter((item): item is Attachment => !!item);
  });

  protected readonly pending = this.session.pendingMaterial;

  /**
   * Nothing is drawn until the backend has said what the tutor knows about.
   * Between going live and that message arriving, `contextItems` is still
   * empty, so every piece — including the ones that started in the prompt —
   * would briefly show up under "not shown yet".
   */
  protected readonly ready = computed(() => this.session.sessionInfo() !== null);

  protected readonly canHandOver = computed(() => this.session.phase() === 'live');

  protected fileUrl(id: number): string {
    return this.api.attachmentFileUrl(id);
  }

  protected enlarge(item: Attachment): void {
    this.zoomed.set(item);
  }

  protected handOver(item: Attachment): void {
    this.session.addContext(item.id);
  }
}
