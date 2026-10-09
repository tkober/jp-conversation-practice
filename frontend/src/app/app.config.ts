import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { provideSumi } from 'sumi-ui/core';

import { routes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideHttpClient(),
    // Route params arrive as component inputs, so pages read `id` directly.
    provideRouter(routes, withComponentInputBinding()),
    // `accent`, `motif`, `pattern` and `companion` are all placeholders —
    // the real design for this app's ink landscape and pattern lands in
    // tkober/sumi-ui#25. `asagi` is docs/concept.md's provisional preset
    // for this app.
    provideSumi({ accent: 'asagi', motif: 'torii', pattern: 'shippo', companion: 'kitsune' }),
  ],
};
