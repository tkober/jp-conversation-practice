import { Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { SumiHotkeyHelp } from 'sumi-ui/core';
import { SUMI_LAYOUT, SumiAppShellBrand, SumiNavItem } from 'sumi-ui/layout';

import { RealtimeSessionService } from './core/realtime-session.service';

@Component({
  selector: 'app-root',
  imports: [...SUMI_LAYOUT, SumiHotkeyHelp, RouterOutlet],
  templateUrl: './app.html',
})
export class App {
  private readonly session = inject(RealtimeSessionService);

  /** Navigating away mid-conversation would silently drop the session. */
  protected readonly conversationRunning = this.session.isLive;

  protected readonly brand: SumiAppShellBrand = { glyph: '話', name: 'Conversation Practice' };

  protected readonly navItems: SumiNavItem[] = [
    { label: 'Practice', link: '', icon: 'practice', exact: true },
    { label: 'Scenarios', link: 'scenarios', icon: 'scenarios' },
    { label: 'History', link: 'history', icon: 'history' },
    { label: 'Settings', link: 'settings', icon: 'settings' },
  ];
}
