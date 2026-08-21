import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { RealtimeSessionService } from './core/realtime-session.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  private readonly session = inject(RealtimeSessionService);

  /** Navigating away mid-conversation would silently drop the session. */
  protected readonly conversationRunning = this.session.isLive;
}
