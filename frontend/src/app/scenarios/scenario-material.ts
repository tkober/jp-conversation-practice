import { Component, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../core/api.service';
import { Attachment } from '../core/models';

/**
 * The context material belonging to one scenario.
 *
 * Uploading and evaluating are deliberately one action: material the tutor has
 * no description of is material it does not know about, so a flow that stops
 * halfway is the one worth avoiding. When the evaluation fails the upload is
 * still kept — the description is an ordinary editable field, because a first
 * draft written by a model is not an oracle. A misread price is corrected here
 * rather than argued with.
 */
@Component({
  selector: 'app-scenario-material',
  imports: [FormsModule],
  templateUrl: './scenario-material.html',
  styleUrl: './scenario-material.scss',
})
export class ScenarioMaterial {
  private readonly api = inject(ApiService);

  readonly scenarioId = input.required<number>();

  readonly material = signal<Attachment[]>([]);
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  /** Which item's description is open for editing, and the draft text. */
  readonly editingId = signal<number | null>(null);
  readonly editingDescription = signal('');
  readonly textDraft = signal('');
  readonly hint = signal('');
  readonly showTextForm = signal(false);

  constructor() {
    effect(() => this.load(this.scenarioId()));
  }

  private load(scenarioId: number): void {
    this.api.attachments(scenarioId).subscribe({
      next: (items) => this.material.set(items),
      error: (error: unknown) => this.error.set(this.describe(error)),
    });
  }

  fileUrl(id: number): string {
    return this.api.attachmentFileUrl(id);
  }

  onFilePicked(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    // Clearing it lets the same file be picked again after a failure.
    input.value = '';
    if (!file || this.busy()) {
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.api
      .uploadAttachmentImage(this.scenarioId(), file, { hint: this.hint().trim() })
      .subscribe({
        next: (item) => this.afterUpload(item),
        error: (error: unknown) => this.fail(error),
      });
  }

  addText(): void {
    const body = this.textDraft().trim();
    if (!body || this.busy()) {
      return;
    }

    this.busy.set(true);
    this.error.set(null);
    this.api
      .addAttachmentText(this.scenarioId(), { body, hint: this.hint().trim() })
      .subscribe({
        next: (item) => {
          this.textDraft.set('');
          this.showTextForm.set(false);
          this.afterUpload(item);
        },
        error: (error: unknown) => this.fail(error),
      });
  }

  /**
   * The upload succeeded either way. `analysis_error` only says the
   * description is missing, and that is a field the user can fill in.
   */
  private afterUpload(item: Attachment): void {
    this.material.update((items) => [...items, item]);
    this.hint.set('');
    this.busy.set(false);
    if (item.analysis_error) {
      this.error.set(
        `Gespeichert, aber nicht ausgewertet: ${item.analysis_error} ` +
          'Du kannst die Beschreibung selbst schreiben oder es noch einmal versuchen.',
      );
    }
  }

  evaluate(item: Attachment): void {
    this.busy.set(true);
    this.error.set(null);
    this.api.evaluateAttachment(item.id).subscribe({
      next: (updated) => {
        this.replace(updated);
        this.busy.set(false);
        if (updated.analysis_error) {
          this.error.set(`Auswertung fehlgeschlagen: ${updated.analysis_error}`);
        }
      },
      error: (error: unknown) => this.fail(error),
    });
  }

  toggleFromStart(item: Attachment): void {
    this.api
      .updateAttachment(item.id, { available_from_start: !item.available_from_start })
      .subscribe({
        next: (updated) => this.replace(updated),
        error: (error: unknown) => this.error.set(this.describe(error)),
      });
  }

  startEditing(item: Attachment): void {
    this.editingId.set(item.id);
    this.editingDescription.set(item.description);
  }

  cancelEditing(): void {
    this.editingId.set(null);
    this.editingDescription.set('');
  }

  saveDescription(item: Attachment): void {
    this.api.updateAttachment(item.id, { description: this.editingDescription() }).subscribe({
      next: (updated) => {
        this.replace(updated);
        this.cancelEditing();
      },
      error: (error: unknown) => this.error.set(this.describe(error)),
    });
  }

  remove(item: Attachment): void {
    this.api.deleteAttachment(item.id).subscribe({
      next: () => this.material.update((items) => items.filter((row) => row.id !== item.id)),
      error: (error: unknown) => this.error.set(this.describe(error)),
    });
  }

  private replace(updated: Attachment): void {
    this.material.update((items) =>
      items.map((item) => (item.id === updated.id ? updated : item)),
    );
  }

  private fail(error: unknown): void {
    this.busy.set(false);
    this.error.set(this.describe(error));
  }

  private describe(error: unknown): string {
    const detail = (error as { error?: { detail?: string } })?.error?.detail;
    return detail ? `Fehler: ${detail}` : 'Die Anfrage ist fehlgeschlagen.';
  }
}
