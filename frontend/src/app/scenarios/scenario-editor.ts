import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { ApiService } from '../core/api.service';
import { AssistantMessage, Scenario } from '../core/models';

type SaveState = 'idle' | 'saving' | 'saved' | 'error';

/** One bubble in the assistant conversation, plus any draft it proposed. */
interface ChatEntry extends AssistantMessage {
  suggestion?: string | null;
}

@Component({
  selector: 'app-scenario-editor',
  imports: [FormsModule, RouterLink],
  templateUrl: './scenario-editor.html',
  styleUrl: './scenario-editor.scss',
})
export class ScenarioEditor {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  /** Route parameter, bound by withComponentInputBinding(). */
  readonly id = input.required<string>();

  readonly scenario = signal<Scenario | null>(null);
  readonly loadError = signal<string | null>(null);

  readonly title = signal('');
  readonly summary = signal('');
  readonly prompt = signal('');

  readonly saveState = signal<SaveState>('idle');
  readonly saveMessage = signal<string | null>(null);

  // --- assistant ---
  readonly chat = signal<ChatEntry[]>([]);
  readonly chatInput = signal('');
  readonly chatBusy = signal(false);
  readonly chatError = signal<string | null>(null);

  readonly dirty = computed(() => {
    const original = this.scenario();
    if (!original) {
      return false;
    }
    return (
      this.title() !== original.title ||
      this.summary() !== original.summary ||
      this.prompt() !== original.prompt
    );
  });

  readonly canReset = computed(() => this.scenario()?.is_builtin === true);

  constructor() {
    effect(() => {
      const id = Number(this.id());
      if (Number.isFinite(id)) {
        this.load(id);
      }
    });
  }

  private load(id: number): void {
    this.api.scenarios().subscribe({
      next: (scenarios) => {
        const found = scenarios.find((row) => row.id === id) ?? null;
        if (!found) {
          this.loadError.set('Dieses Szenario existiert nicht (mehr).');
          return;
        }
        this.apply(found);
      },
      error: (error: unknown) => this.loadError.set(this.describe(error)),
    });
  }

  private apply(scenario: Scenario): void {
    this.scenario.set(scenario);
    this.title.set(scenario.title);
    this.summary.set(scenario.summary);
    this.prompt.set(scenario.prompt);
    this.loadError.set(null);
  }

  save(): void {
    const current = this.scenario();
    if (!current || !this.dirty() || this.saveState() === 'saving') {
      return;
    }

    this.saveState.set('saving');
    this.api
      .updateScenario(current.id, {
        title: this.title().trim(),
        summary: this.summary().trim(),
        prompt: this.prompt().trim(),
      })
      .subscribe({
        next: (scenario) => {
          this.apply(scenario);
          this.saveState.set('saved');
          this.saveMessage.set('Gespeichert.');
        },
        error: (error: unknown) => {
          this.saveState.set('error');
          this.saveMessage.set(this.describe(error));
        },
      });
  }

  /** Restore a built-in scenario from the Markdown file it shipped as. */
  resetToFile(): void {
    const current = this.scenario();
    if (!current) {
      return;
    }
    this.api.resetScenario(current.id).subscribe({
      next: (scenario) => {
        this.apply(scenario);
        this.saveState.set('saved');
        this.saveMessage.set('Auf die mitgelieferte Fassung zurückgesetzt.');
      },
      error: (error: unknown) => {
        this.saveState.set('error');
        this.saveMessage.set(this.describe(error));
      },
    });
  }

  // --- assistant ---

  ask(): void {
    const question = this.chatInput().trim();
    if (!question || this.chatBusy()) {
      return;
    }

    const history: ChatEntry[] = [...this.chat(), { role: 'user', content: question }];
    this.chat.set(history);
    this.chatInput.set('');
    this.chatBusy.set(true);
    this.chatError.set(null);

    this.api
      .askScenarioAssistant({
        // The live draft travels with every turn, so the assistant reasons
        // about what is in the editor now rather than its own last suggestion.
        draft: this.prompt(),
        title: this.title(),
        messages: history.map(({ role, content }) => ({ role, content })),
      })
      .subscribe({
        next: (reply) => {
          this.chat.set([
            ...history,
            {
              role: 'assistant',
              content: reply.reply,
              suggestion: reply.suggested_prompt,
            },
          ]);
          this.chatBusy.set(false);
        },
        error: (error: unknown) => {
          this.chatBusy.set(false);
          this.chatError.set(this.describe(error));
        },
      });
  }

  applySuggestion(suggestion: string): void {
    this.prompt.set(suggestion);
    this.saveState.set('idle');
    this.saveMessage.set('Vorschlag übernommen — noch nicht gespeichert.');
  }

  clearChat(): void {
    this.chat.set([]);
    this.chatError.set(null);
  }

  onChatKeydown(event: KeyboardEvent): void {
    // Enter sends, Shift+Enter makes a new line — the usual chat convention.
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.ask();
    }
  }

  private describe(error: unknown): string {
    const detail = (error as { error?: { detail?: string } })?.error?.detail;
    return detail ? `Fehler: ${detail}` : 'Die Anfrage ist fehlgeschlagen.';
  }
}
