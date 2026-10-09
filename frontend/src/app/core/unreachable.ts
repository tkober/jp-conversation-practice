import { HttpErrorResponse } from '@angular/common/http';

/**
 * Whether a failed request means the backend itself could not be reached —
 * a connection failure (`status 0`) or the dev proxy/nginx answering on its
 * own behalf (5xx) — rather than the backend itself producing an
 * application-level error. A page's initial load uses this to decide
 * between T6 (`sumi-error-state`, "Try again") and an ordinary banner/empty
 * state: see docs/concept.md#tuschemotive and jp-conjugation's
 * `practice`/`words`/`stats`/`rules` components (`failed`/`loadFailed`
 * signals), which make the same call for the same reason — every one of
 * those pages' initial GETs can only fail this way, a 4xx would mean the
 * backend answered, i.e. is reachable. A scenario that genuinely does not
 * exist (`ScenarioEditor`) is never an HTTP error here — the full list loads
 * fine and the row is simply missing from it — so it is unaffected and
 * stays a banner, per the epic's "404 is not an outage" rule.
 */
export function isBackendUnreachable(error: unknown): boolean {
  if (!(error instanceof HttpErrorResponse)) {
    return false;
  }
  return error.status === 0 || error.status >= 500;
}
