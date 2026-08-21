import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  AnalysisResponse,
  AnkiCard,
  AnkiExportResponse,
  AppSettingsPatch,
  AppSettingsView,
  AssistantMessage,
  AssistantReply,
  HealthResponse,
  JlptLevel,
  Scenario,
  ScenarioDraft,
  SessionDetail,
  SessionStats,
  SessionSummary,
  TranscriptTurn,
  UsageSnapshot,
  VoicesResponse,
} from './models';

/** REST calls to the FastAPI backend (proxied under /api during development). */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  // --- practice ---

  health(): Observable<HealthResponse> {
    return this.http.get<HealthResponse>('/api/health');
  }

  voices(): Observable<VoicesResponse> {
    return this.http.get<VoicesResponse>('/api/voices');
  }

  /** URL of the spoken preview for one voice. */
  voiceSampleUrl(voiceId: string): string {
    return `/api/voices/${encodeURIComponent(voiceId)}/sample`;
  }

  analyse(request: {
    scenario: string;
    jlpt_level: JlptLevel;
    transcript: TranscriptTurn[];
    use_wanikani_filter: boolean;
  }): Observable<AnalysisResponse> {
    return this.http.post<AnalysisResponse>('/api/analysis', request);
  }

  exportToAnki(cards: AnkiCard[], tags: string[]): Observable<AnkiExportResponse> {
    return this.http.post<AnkiExportResponse>('/api/anki/export', { cards, tags });
  }

  // --- scenarios ---

  scenarios(): Observable<Scenario[]> {
    return this.http.get<Scenario[]>('/api/scenarios');
  }

  createScenario(draft: ScenarioDraft): Observable<Scenario> {
    return this.http.post<Scenario>('/api/scenarios', draft);
  }

  updateScenario(id: number, draft: Partial<ScenarioDraft>): Observable<Scenario> {
    return this.http.put<Scenario>(`/api/scenarios/${id}`, draft);
  }

  deleteScenario(id: number): Observable<void> {
    return this.http.delete<void>(`/api/scenarios/${id}`);
  }

  resetScenario(id: number): Observable<Scenario> {
    return this.http.post<Scenario>(`/api/scenarios/${id}/reset`, {});
  }

  askScenarioAssistant(request: {
    draft: string;
    title: string;
    messages: AssistantMessage[];
  }): Observable<AssistantReply> {
    return this.http.post<AssistantReply>('/api/scenarios/assistant', request);
  }

  // --- settings ---

  settings(): Observable<AppSettingsView> {
    return this.http.get<AppSettingsView>('/api/settings');
  }

  saveSettings(patch: AppSettingsPatch): Observable<AppSettingsView> {
    return this.http.put<AppSettingsView>('/api/settings', patch);
  }

  // --- sessions ---

  sessions(): Observable<SessionSummary[]> {
    return this.http.get<SessionSummary[]>('/api/sessions');
  }

  session(id: number): Observable<SessionDetail> {
    return this.http.get<SessionDetail>(`/api/sessions/${id}`);
  }

  sessionStats(): Observable<SessionStats> {
    return this.http.get<SessionStats>('/api/sessions/stats');
  }

  saveSession(payload: {
    scenario_id: number | null;
    scenario_title: string;
    scenario_prompt: string;
    jlpt_level: JlptLevel;
    model: string;
    voice: string;
    speed: number;
    instructions: string;
    duration_seconds: number;
    cost_usd: number;
    usage: UsageSnapshot;
    transcript: TranscriptTurn[];
  }): Observable<SessionSummary> {
    return this.http.post<SessionSummary>('/api/sessions', payload);
  }

  attachAnalysis(id: number, analysis: AnalysisResponse): Observable<SessionSummary> {
    return this.http.put<SessionSummary>(`/api/sessions/${id}/analysis`, analysis);
  }

  deleteSession(id: number): Observable<void> {
    return this.http.delete<void>(`/api/sessions/${id}`);
  }
}
