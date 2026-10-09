import { Routes } from '@angular/router';

/**
 * Every page is lazily loaded: the practice screen is what the app opens with,
 * and the editor, history and settings pull in code the common case never
 * needs.
 */
export const routes: Routes = [
  {
    path: '',
    title: 'Practice — Japanese Conversation',
    loadComponent: () => import('./practice/practice').then((m) => m.Practice),
  },
  {
    path: 'scenarios',
    title: 'Scenarios — Japanese Conversation',
    loadComponent: () => import('./scenarios/scenario-list').then((m) => m.ScenarioList),
  },
  {
    path: 'scenarios/:id',
    title: 'Edit scenario — Japanese Conversation',
    loadComponent: () => import('./scenarios/scenario-editor').then((m) => m.ScenarioEditor),
  },
  {
    path: 'history',
    title: 'History — Japanese Conversation',
    loadComponent: () => import('./history/history').then((m) => m.History),
  },
  {
    path: 'settings',
    title: 'Settings — Japanese Conversation',
    loadComponent: () => import('./settings/settings').then((m) => m.SettingsPage),
  },
  { path: '**', redirectTo: '' },
];
