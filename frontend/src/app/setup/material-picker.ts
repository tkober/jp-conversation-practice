import {
  Component,
  computed,
  effect,
  inject,
  input,
  model,
  output,
  signal,
  untracked,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  SumiButtonDirective,
  SumiCheckboxDirective,
  SumiInputDirective,
  SumiTextareaDirective,
} from 'sumi-ui/forms';
import { SumiBanner } from 'sumi-ui/layout';

import { ApiService } from '../core/api.service';
import { Attachment } from '../core/models';

/**
 * The context material library, and what this run takes along.
 *
 * It sits on the setup screen rather than in the scenario editor because the
 * scenario is the role and the role is the part that repeats: what varies
 * between two runs of the same konbini is what is on the shelf. So material is
 * picked here, per run, out of a library that belongs to nobody.
 *
 * A scenario can still pre-select entries — the pin — which decides only what
 * is ticked when you pick it, never what is available.
 *
 * Managing the library happens here too, behind the expander: uploading and
 * evaluating are one action, and an evaluation that fails keeps the upload,
 * because the description is an ordinary editable field and a first draft
 * written by a model is not an oracle.
 */
@Component({
  selector: 'app-material-picker',
  imports: [
    FormsModule,
    SumiBanner,
    SumiButtonDirective,
    SumiCheckboxDirective,
    SumiInputDirective,
    SumiTextareaDirective,
  ],
  templateUrl: './material-picker.html',
  styleUrl: './material-picker.scss',
})
export class MaterialPicker {
  private readonly api = inject(ApiService);

  /** The scenario picked right now — frames evaluations and the pre-selection. */
  readonly scenarioId = input<number | null>(null);

  /** Ids this run takes along, and which of them start in the prompt. */
  readonly selected = model<ReadonlySet<number>>(new Set());
  readonly fromStart = model<ReadonlySet<number>>(new Set());

  readonly material = signal<Attachment[]>([]);
  /** Mirrored up so the setup screen can hand the session what was picked. */
  readonly materialChange = output<Attachment[]>();
  readonly busy = signal(false);
  readonly error = signal<string | null>(null);
  readonly expandedId = signal<number | null>(null);
  readonly editingDescription = signal('');
  readonly textDraft = signal('');
  readonly hint = signal('');
  readonly showTextForm = signal(false);

  readonly selectedCount = computed(() => this.selected().size);

  constructor() {
    // Reload when the scenario changes: the list is the same either way, but
    // which entries it marks as pre-selected is not.
    effect(() => {
      const scenario = this.scenarioId();
      untracked(() => this.load(scenario));
    });
  }

  private load(scenarioId: number | null): void {
    this.api.attachments(scenarioId).subscribe({
      next: (items) => {
        this.setMaterial(items);
        // The scenario's pre-selection is a starting point, not a rule: it
        // seeds the ticks and every one of them can be undone for this run.
        this.selected.set(new Set(items.filter((i) => i.default_for_scenario).map((i) => i.id)));
        this.fromStart.set(new Set(items.filter((i) => i.available_from_start).map((i) => i.id)));
      },
      error: (error: unknown) => this.error.set(this.describe(error)),
    });
  }

  fileUrl(id: number): string {
    return this.api.attachmentFileUrl(id);
  }

  // --- what this run takes along -----------------------------------------

  isSelected(id: number): boolean {
    return this.selected().has(id);
  }

  toggleSelected(id: number): void {
    this.selected.set(toggle(this.selected(), id));
  }

  startsInPrompt(id: number): boolean {
    return this.fromStart().has(id);
  }

  toggleFromStart(id: number): void {
    this.fromStart.set(toggle(this.fromStart(), id));
  }

  // --- the scenario's pre-selection --------------------------------------

  toggleDefault(item: Attachment): void {
    const scenario = this.scenarioId();
    if (scenario === null) {
      return;
    }
    const next = !item.default_for_scenario;
    this.api.setAttachmentDefault(item.id, scenario, next).subscribe({
      next: () => this.replace({ ...item, default_for_scenario: next }),
      error: (error: unknown) => this.error.set(this.describe(error)),
    });
  }

  // --- managing the library ----------------------------------------------

  toggleExpanded(item: Attachment): void {
    const open = this.expandedId() === item.id;
    this.expandedId.set(open ? null : item.id);
    this.editingDescription.set(open ? '' : item.description);
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
      .uploadAttachmentImage(file, { hint: this.hint().trim(), scenarioId: this.scenarioId() })
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
      .addAttachmentText({
        body,
        hint: this.hint().trim(),
        scenario_id: this.scenarioId(),
      })
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
   *
   * Freshly uploaded material is ticked straight away: you photographed the
   * shelf in order to use it, not in order to file it.
   */
  private afterUpload(item: Attachment): void {
    this.setMaterial([...this.material(), item]);
    this.selected.set(new Set([...this.selected(), item.id]));
    if (item.available_from_start) {
      this.fromStart.set(new Set([...this.fromStart(), item.id]));
    }
    this.hint.set('');
    this.busy.set(false);
    if (item.analysis_error) {
      this.error.set(
        `Saved, but not evaluated: ${item.analysis_error} ` +
          'You can write the description yourself or try again.',
      );
    }
  }

  evaluate(item: Attachment): void {
    this.busy.set(true);
    this.error.set(null);
    this.api.evaluateAttachment(item.id, this.scenarioId()).subscribe({
      next: (updated) => {
        this.replace({ ...updated, default_for_scenario: item.default_for_scenario });
        if (this.expandedId() === item.id) {
          this.editingDescription.set(updated.description);
        }
        this.busy.set(false);
        if (updated.analysis_error) {
          this.error.set(`Evaluation failed: ${updated.analysis_error}`);
        }
      },
      error: (error: unknown) => this.fail(error),
    });
  }

  saveDescription(item: Attachment): void {
    this.api.updateAttachment(item.id, { description: this.editingDescription() }).subscribe({
      next: (updated) =>
        this.replace({ ...updated, default_for_scenario: item.default_for_scenario }),
      error: (error: unknown) => this.error.set(this.describe(error)),
    });
  }

  remove(item: Attachment): void {
    this.api.deleteAttachment(item.id).subscribe({
      next: () => {
        this.setMaterial(this.material().filter((row) => row.id !== item.id));
        this.selected.set(without(this.selected(), item.id));
        this.fromStart.set(without(this.fromStart(), item.id));
        if (this.expandedId() === item.id) {
          this.expandedId.set(null);
        }
      },
      error: (error: unknown) => this.error.set(this.describe(error)),
    });
  }

  private replace(updated: Attachment): void {
    this.setMaterial(
      this.material().map((item) => (item.id === updated.id ? updated : item)),
    );
  }

  private setMaterial(items: Attachment[]): void {
    this.material.set(items);
    this.materialChange.emit(items);
  }

  private fail(error: unknown): void {
    this.busy.set(false);
    this.error.set(this.describe(error));
  }

  private describe(error: unknown): string {
    const detail = (error as { error?: { detail?: string } })?.error?.detail;
    return detail ? `Error: ${detail}` : 'The request failed.';
  }
}

function toggle(current: ReadonlySet<number>, id: number): ReadonlySet<number> {
  const next = new Set(current);
  if (!next.delete(id)) {
    next.add(id);
  }
  return next;
}

function without(current: ReadonlySet<number>, id: number): ReadonlySet<number> {
  const next = new Set(current);
  next.delete(id);
  return next;
}
