import { Routes } from '@angular/router';

/**
 * Every page is lazily loaded: the practice screen is what the app opens with,
 * and the editor, history and settings pull in code the common case never
 * needs.
 */
export const routes: Routes = [
  {
    path: '',
    title: 'Üben — Japanisch-Konversation',
    loadComponent: () => import('./practice/practice').then((m) => m.Practice),
  },
  {
    path: 'scenarios',
    title: 'Szenarien — Japanisch-Konversation',
    loadComponent: () => import('./scenarios/scenario-list').then((m) => m.ScenarioList),
  },
  {
    path: 'scenarios/:id',
    title: 'Szenario bearbeiten — Japanisch-Konversation',
    loadComponent: () => import('./scenarios/scenario-editor').then((m) => m.ScenarioEditor),
  },
  {
    path: 'history',
    title: 'Verlauf — Japanisch-Konversation',
    loadComponent: () => import('./history/history').then((m) => m.History),
  },
  {
    path: 'settings',
    title: 'Einstellungen — Japanisch-Konversation',
    loadComponent: () => import('./settings/settings').then((m) => m.SettingsPage),
  },
  { path: '**', redirectTo: '' },
];
