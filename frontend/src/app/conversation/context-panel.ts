import { Component, computed, inject, signal } from '@angular/core';

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
  imports: [],
  template: `@if (visible().length || pending().length) {
    <section class="material">
      @if (visible().length) {
        <div class="shown">
          @for (item of visible(); track item.id) {
            <figure class="item" [class.text]="item.kind === 'text'">
              @if (item.kind === 'image') {
                <button type="button" class="thumb" (click)="enlarge(item)">
                  <img [src]="fileUrl(item.id)" [alt]="item.title" />
                </button>
              } @else {
                <pre class="body jp">{{ item.body }}</pre>
              }
              <figcaption>{{ item.title || 'Material' }}</figcaption>
            </figure>
          }
        </div>
      }

      @if (pending().length) {
        <div class="pending">
          <span class="pending-label">Noch nicht dabei:</span>
          @for (item of pending(); track item.id) {
            <button
              type="button"
              class="btn btn-secondary"
              [disabled]="!canHandOver()"
              [title]="'Der Lehrkraft zeigen: ' + (item.title || 'Material')"
              (click)="handOver(item)"
            >
              + {{ item.title || 'Material' }}
            </button>
          }
        </div>
      }
    </section>
  }

  @if (zoomed(); as item) {
    <div class="overlay" (click)="zoomed.set(null)">
      <img [src]="fileUrl(item.id)" [alt]="item.title" />
      <span class="overlay-hint">{{ item.title }} — irgendwohin klicken zum Schließen</span>
    </div>
  }`,
  styles: `
    .material {
      display: flex;
      flex-direction: column;
      gap: 10px;
      padding: 12px 16px;
      background: var(--bg-elevated);
      border: 1px solid var(--border);
      border-radius: var(--radius);
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
      color: var(--text-faint);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .thumb {
      padding: 0;
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      background: var(--bg-input);
      cursor: zoom-in;

      &:hover {
        border-color: var(--accent);
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
      background: var(--bg-input);
      border: 1px solid var(--border);
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
      color: var(--text-faint);
    }

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
      color: var(--text-faint);
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
