import { Component, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { ApiService } from '../core/api.service';
import { Scenario } from '../core/models';

@Component({
  selector: 'app-scenario-list',
  imports: [RouterLink],
  templateUrl: './scenario-list.html',
  styleUrl: './scenario-list.scss',
})
export class ScenarioList {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  readonly scenarios = signal<Scenario[]>([]);
  readonly error = signal<string | null>(null);
  readonly busy = signal(false);
  /** Scenario awaiting delete confirmation, if any. */
  readonly confirmingDelete = signal<number | null>(null);

  constructor() {
    this.reload();
  }

  reload(): void {
    this.api.scenarios().subscribe({
      next: (scenarios) => this.scenarios.set(scenarios),
      error: (error: unknown) => this.error.set(this.describe(error)),
    });
  }

  createScenario(): void {
    if (this.busy()) {
      return;
    }
    this.busy.set(true);
    this.api
      .createScenario({
        title: 'Neues Szenario',
        summary: '',
        // A skeleton that already follows the role-not-checklist rule, so the
        // starting point does not teach the wrong shape.
        prompt:
          'You are ... . The learner is ... . Deal with them the way a real ' +
          'person in this situation would; what comes up depends on what they ' +
          'actually say.',
      })
      .subscribe({
        next: (scenario) => {
          this.busy.set(false);
          void this.router.navigate(['/scenarios', scenario.id]);
        },
        error: (error: unknown) => {
          this.busy.set(false);
          this.error.set(this.describe(error));
        },
      });
  }

  askDelete(scenario: Scenario): void {
    this.confirmingDelete.set(scenario.id);
  }

  cancelDelete(): void {
    this.confirmingDelete.set(null);
  }

  confirmDelete(scenario: Scenario): void {
    this.api.deleteScenario(scenario.id).subscribe({
      next: () => {
        this.confirmingDelete.set(null);
        this.reload();
      },
      error: (error: unknown) => this.error.set(this.describe(error)),
    });
  }

  private describe(error: unknown): string {
    const detail = (error as { error?: { detail?: string } })?.error?.detail;
    return detail ? `Fehler: ${detail}` : 'Die Szenarien konnten nicht geladen werden.';
  }
}
