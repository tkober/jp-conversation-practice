import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../core/api.service';
import {
  AppSettingsPatch,
  AppSettingsView,
  EAGERNESS_OPTIONS,
  ModelOption,
  ModelSlotView,
  VoiceOption,
} from '../core/models';

type SaveState = 'idle' | 'saving' | 'saved' | 'error';

/**
 * Sentinel option value that switches a slot from the dropdown to a text box.
 * Not a valid model id, so it can never collide with a real one.
 */
const CUSTOM = '__custom__';

@Component({
  selector: 'app-settings',
  imports: [FormsModule],
  templateUrl: './settings.html',
  styleUrl: './settings.scss',
})
export class SettingsPage {
  private readonly api = inject(ApiService);

  readonly eagernessOptions = EAGERNESS_OPTIONS;
  readonly customValue = CUSTOM;

  readonly current = signal<AppSettingsView | null>(null);
  readonly voices = signal<VoiceOption[]>([]);
  readonly modelSlots = signal<ModelSlotView[]>([]);
  /** Why the dropdowns only show curated entries, when that is the case. */
  readonly modelListNote = signal<string | null>(null);
  readonly loadError = signal<string | null>(null);
  readonly saveState = signal<SaveState>('idle');
  readonly saveMessage = signal<string | null>(null);

  /** Only the fields the user actually touched are sent. */
  private readonly draft = signal<AppSettingsPatch>({});

  /** Secrets are write-only: an empty box means "leave as is". */
  readonly openaiKeyInput = signal('');
  readonly wanikaniTokenInput = signal('');

  /**
   * Slots the user switched to free text. Sticky, because picking "Anderes
   * Modell ..." leaves the old -- still valid -- value in place, so the text
   * box cannot be derived from the value alone.
   */
  private readonly customSlots = signal<ReadonlySet<string>>(new Set());

  readonly dirty = computed(
    () =>
      Object.keys(this.draft()).length > 0 ||
      this.openaiKeyInput().length > 0 ||
      this.wanikaniTokenInput().length > 0,
  );

  constructor() {
    this.reload();
    this.api.voices().subscribe({
      next: (response) => this.voices.set(response.voices),
      error: () => undefined,
    });
    this.api.modelCatalog().subscribe({
      next: (catalog) => {
        this.modelSlots.set(catalog.slots);
        this.modelListNote.set(catalog.live_ok ? null : catalog.live_detail);
      },
      error: () => this.modelListNote.set('Die Modellliste konnte nicht geladen werden.'),
    });
  }

  reload(): void {
    this.api.settings().subscribe({
      next: (settings) => {
        this.current.set(settings);
        this.draft.set({});
        this.customSlots.set(new Set());
        this.loadError.set(null);
      },
      error: (error: unknown) => this.loadError.set(this.describe(error)),
    });
  }

  value(key: keyof AppSettingsPatch): string {
    const draft = this.draft()[key];
    if (draft !== undefined) {
      return String(draft);
    }
    const settings = this.current();
    return settings ? String(settings[key as keyof AppSettingsView] ?? '') : '';
  }

  set(key: keyof AppSettingsPatch, value: string | number): void {
    this.draft.update((current) => ({ ...current, [key]: value }));
    this.saveState.set('idle');
  }

  // --- model slots -----------------------------------------------------

  /** The option currently configured for a slot, if it is one of the listed ones. */
  selectedOption(slot: ModelSlotView): ModelOption | null {
    const id = this.value(slot.key);
    return slot.options.find((option) => option.id === id) ?? null;
  }

  /**
   * Whether this slot shows the text box instead of the dropdown.
   *
   * Either the user asked for it, or the configured model is not in the list --
   * a value set before a model was retired, or typed on an earlier visit. A
   * `<select>` renders such a value as blank, so falling back to text keeps it
   * visible rather than silently swallowing it.
   */
  isCustom(slot: ModelSlotView): boolean {
    if (this.customSlots().has(slot.key)) {
      return true;
    }
    const id = this.value(slot.key);
    return id !== '' && !slot.options.some((option) => option.id === id);
  }

  selectValue(slot: ModelSlotView): string {
    return this.isCustom(slot) ? CUSTOM : this.value(slot.key);
  }

  onSlotSelect(slot: ModelSlotView, value: string): void {
    this.customSlots.update((current) => {
      const next = new Set(current);
      if (value === CUSTOM) {
        next.add(slot.key);
      } else {
        next.delete(slot.key);
      }
      return next;
    });
    if (value !== CUSTOM) {
      this.set(slot.key, value);
    }
  }

  /** One line per option: the id, plus what we know that the id does not say. */
  optionText(option: ModelOption): string {
    const notes: string[] = [];
    if (option.price_hint) {
      notes.push(option.price_hint);
    } else if (option.rates_known === false) {
      notes.push('kein Preis hinterlegt');
    }
    if (option.shutdown_date) {
      // The field is the shutdown date, not the date it was deprecated: it is
      // callable until then, it just has a successor already.
      notes.push(`deprecated (Abschaltung ${option.shutdown_date})`);
    }
    return notes.length ? `${option.label} — ${notes.join(', ')}` : option.label;
  }

  curatedOptions(slot: ModelSlotView): ModelOption[] {
    return slot.options.filter((option) => option.curated);
  }

  liveOptions(slot: ModelSlotView): ModelOption[] {
    return slot.options.filter((option) => !option.curated);
  }

  /**
   * Whether the chosen model would be billed at guessed rates.
   *
   * Only the realtime slot is cost-tracked at all; there, a model missing from
   * MODEL_RATES falls back to the mini rates. The session screen says so once
   * the conversation runs -- saying it here is cheaper.
   */
  ratesUnknown(slot: ModelSlotView): boolean {
    if (!slot.cost_tracked || this.value(slot.key) === '') {
      return false;
    }
    const option = this.selectedOption(slot);
    return option ? option.rates_known === false : true;
  }

  save(): void {
    if (!this.dirty() || this.saveState() === 'saving') {
      return;
    }

    const patch: AppSettingsPatch = { ...this.draft() };
    // Sent only when non-empty, so saving other fields never clears a secret.
    if (this.openaiKeyInput().trim()) {
      patch.openai_api_key = this.openaiKeyInput().trim();
    }
    if (this.wanikaniTokenInput().trim()) {
      patch.wanikani_api_token = this.wanikaniTokenInput().trim();
    }

    this.saveState.set('saving');
    this.api.saveSettings(patch).subscribe({
      next: (settings) => {
        this.current.set(settings);
        this.draft.set({});
        // A typed value that turned out to be a listed model settles back into
        // the dropdown; one that did not stays in the text box on its own.
        this.customSlots.set(new Set());
        this.openaiKeyInput.set('');
        this.wanikaniTokenInput.set('');
        this.saveState.set('saved');
        this.saveMessage.set('Gespeichert.');
      },
      error: (error: unknown) => {
        this.saveState.set('error');
        this.saveMessage.set(this.describe(error));
      },
    });
  }

  /** Clear an override so the value from the environment applies again. */
  clearSecret(field: 'openai_api_key' | 'wanikani_api_token'): void {
    this.saveState.set('saving');
    this.api.saveSettings({ [field]: '' }).subscribe({
      next: (settings) => {
        this.current.set(settings);
        this.saveState.set('saved');
        this.saveMessage.set('Zurückgesetzt — es gilt wieder der Wert aus der .env.');
      },
      error: (error: unknown) => {
        this.saveState.set('error');
        this.saveMessage.set(this.describe(error));
      },
    });
  }

  private describe(error: unknown): string {
    const detail = (error as { error?: { detail?: string } })?.error?.detail;
    return detail ? `Fehler: ${detail}` : 'Die Einstellungen konnten nicht geladen werden.';
  }
}
