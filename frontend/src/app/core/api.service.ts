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
  Attachment,
  AttachmentPatch,
  ContextItem,
  HealthResponse,
  JlptLevel,
  ModelCatalogResponse,
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
    context_items: ContextItem[];
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

  // --- context material ---

  attachments(scenarioId: number): Observable<Attachment[]> {
    return this.http.get<Attachment[]>(`/api/scenarios/${scenarioId}/attachments`);
  }

  /**
   * Upload an image and have it described in one request.
   *
   * Multipart rather than base64 JSON: a phone photo is megabytes, and base64
   * would add a third on top of that for no gain.
   */
  uploadAttachmentImage(
    scenarioId: number,
    file: File,
    options: { title?: string; hint?: string; availableFromStart?: boolean } = {},
  ): Observable<Attachment> {
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('title', options.title ?? '');
    form.append('hint', options.hint ?? '');
    form.append('available_from_start', String(options.availableFromStart ?? true));
    return this.http.post<Attachment>(`/api/scenarios/${scenarioId}/attachments/image`, form);
  }

  addAttachmentText(
    scenarioId: number,
    payload: { body: string; title?: string; hint?: string; available_from_start?: boolean },
  ): Observable<Attachment> {
    return this.http.post<Attachment>(`/api/scenarios/${scenarioId}/attachments/text`, payload);
  }

  updateAttachment(id: number, patch: AttachmentPatch): Observable<Attachment> {
    return this.http.put<Attachment>(`/api/attachments/${id}`, patch);
  }

  /** Describe the material again, replacing whatever description it has. */
  evaluateAttachment(id: number): Observable<Attachment> {
    return this.http.post<Attachment>(`/api/attachments/${id}/evaluate`, {});
  }

  deleteAttachment(id: number): Observable<void> {
    return this.http.delete<void>(`/api/attachments/${id}`);
  }

  /** Where the image itself lives — used directly as an `<img>` source. */
  attachmentFileUrl(id: number): string {
    return `/api/attachments/${id}/file`;
  }

  // --- settings ---

  settings(): Observable<AppSettingsView> {
    return this.http.get<AppSettingsView>('/api/settings');
  }

  saveSettings(patch: AppSettingsPatch): Observable<AppSettingsView> {
    return this.http.put<AppSettingsView>('/api/settings', patch);
  }

  /** Which models the dropdowns may offer, curated plus whatever the key can call. */
  modelCatalog(): Observable<ModelCatalogResponse> {
    return this.http.get<ModelCatalogResponse>('/api/settings/models');
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
    vad_eagerness: string;
    instructions: string;
    duration_seconds: number;
    cost_usd: number;
    usage: UsageSnapshot;
    transcript: TranscriptTurn[];
    context_items: ContextItem[];
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
