import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  AnalysisResponse,
  AnkiCard,
  AnkiExportResponse,
  HealthResponse,
  JlptLevel,
  ScenarioPreset,
  TranscriptTurn,
  VoicesResponse,
} from './models';

/** REST calls to the FastAPI backend (proxied under /api during development). */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  health(): Observable<HealthResponse> {
    return this.http.get<HealthResponse>('/api/health');
  }

  scenarios(): Observable<{ scenarios: ScenarioPreset[] }> {
    return this.http.get<{ scenarios: ScenarioPreset[] }>('/api/scenarios');
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
}
