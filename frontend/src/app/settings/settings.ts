import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../core/api.service';
import {
  AppSettingsPatch,
  AppSettingsView,
  EAGERNESS_OPTIONS,
  VoiceOption,
} from '../core/models';

type SaveState = 'idle' | 'saving' | 'saved' | 'error';

/** A model field the user can override, rendered as a labelled text input. */
interface ModelField {
  key: keyof AppSettingsPatch;
  label: string;
  hint: string;
}

const MODEL_FIELDS: ModelField[] = [
  {
    key: 'realtime_model',
    label: 'Konversation (Realtime)',
    hint: 'Führt das Live-Gespräch. gpt-realtime-2.1-mini ist günstig, gpt-realtime deutlich kohärenter.',
  },
  {
    key: 'analysis_model',
    label: 'Auswertung',
    hint: 'Erzeugt Feedback, Grammatik-Hinweise und Anki-Karten nach der Session.',
  },
  {
    key: 'scenario_assistant_model',
    label: 'Szenario-Assistent',
    hint: 'Hilft im Szenario-Editor beim Formulieren. Schreibt Prosa statt zu sprechen — ein stärkeres Modell lohnt sich hier eher.',
  },
  {
    key: 'transcription_model',
    label: 'Transkription',
    hint: 'Wandelt deine Sprache in Text für Transkript und Auswertung.',
  },
  {
    key: 'tts_model',
    label: 'Stimmproben (TTS)',
    hint: 'Erzeugt die Hörproben in der Stimmauswahl.',
  },
];

@Component({
  selector: 'app-settings',
  imports: [FormsModule],
  templateUrl: './settings.html',
  styleUrl: './settings.scss',
})
export class SettingsPage {
  private readonly api = inject(ApiService);

  readonly modelFields = MODEL_FIELDS;
  readonly eagernessOptions = EAGERNESS_OPTIONS;

  readonly current = signal<AppSettingsView | null>(null);
  readonly voices = signal<VoiceOption[]>([]);
  readonly loadError = signal<string | null>(null);
  readonly saveState = signal<SaveState>('idle');
  readonly saveMessage = signal<string | null>(null);

  /** Only the fields the user actually touched are sent. */
  private readonly draft = signal<AppSettingsPatch>({});

  /** Secrets are write-only: an empty box means "leave as is". */
  readonly openaiKeyInput = signal('');
  readonly wanikaniTokenInput = signal('');

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
  }

  reload(): void {
    this.api.settings().subscribe({
      next: (settings) => {
        this.current.set(settings);
        this.draft.set({});
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
