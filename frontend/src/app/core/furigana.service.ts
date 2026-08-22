import { Injectable, signal } from '@angular/core';

const STORAGE_KEY = 'jp-practice.furigana';

/**
 * Whether the transcript shows its readings, shared by every view that renders
 * Japanese and remembered across reloads: it is a property of the learner, not
 * of the screen they happen to be on.
 */
@Injectable({ providedIn: 'root' })
export class FuriganaService {
  readonly enabled = signal(read());

  toggle(): void {
    const next = !this.enabled();
    this.enabled.set(next);
    try {
      localStorage.setItem(STORAGE_KEY, next ? 'on' : 'off');
    } catch {
      // Private mode or storage disabled: the setting just lasts for this visit.
    }
  }
}

/** Default on — a learner who does not need the readings can switch them off. */
function read(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== 'off';
  } catch {
    return true;
  }
}
