/** Shared types mirroring the backend's JSON contracts. */

export type JlptLevel = 'N5' | 'N4' | 'N3' | 'N2';

export type SessionPhase = 'setup' | 'connecting' | 'live' | 'analysing' | 'review';

export interface ScenarioPreset {
  id: string;
  title: string;
  prompt: string;
}

export interface TranscriptTurn {
  role: 'user' | 'assistant';
  text: string;
  timestamp?: number;
}

export interface TokenBucket {
  text_tokens: number;
  cached_text_tokens: number;
  audio_tokens: number;
  cached_audio_tokens: number;
}

export interface UsageSnapshot {
  model: string;
  response_count: number;
  total_tokens: number;
  input: TokenBucket;
  output: TokenBucket;
  cost_usd: number;
  rates_known: boolean;
}

export interface GrammarNote {
  original: string;
  correction: string;
  explanation: string;
}

export interface AnkiCard {
  expression: string;
  reading: string;
  meaning: string;
  context_sentence: string;
}

export interface AnalysisResponse {
  summary: string;
  grammar_notes: GrammarNote[];
  anki_cards: AnkiCard[];
  filtered_out: string[];
  wanikani_status: 'disabled' | 'ok' | 'error';
  wanikani_message: string | null;
}

export interface AnkiExportResponse {
  added: number;
  duplicates: number;
  deck_name: string;
  note_ids: (number | null)[];
}

export interface VoiceOption {
  id: string;
  label: string;
  description: string;
}

export interface VoicesResponse {
  voices: VoiceOption[];
  default_voice: string;
  default_speed: number;
  speed_min: number;
  speed_max: number;
}

/** What the backend reports when the realtime session is configured. */
export interface SessionInfo {
  model: string;
  scenario: string;
  jlpt_level: string;
  voice: string;
  speed: number;
  /** The system prompt the tutor actually ran with. */
  instructions: string;
}

/** Self-contained dump of one session, for sharing or debugging. */
export interface SessionExport {
  exported_at: string;
  scenario: string;
  jlpt_level: string;
  model: string;
  voice: string;
  speed: number;
  system_instructions: string;
  duration_seconds: number;
  usage: UsageSnapshot;
  transcript: TranscriptTurn[];
  analysis: AnalysisResponse | null;
}

export interface HealthResponse {
  status: string;
  openai_configured: boolean;
  wanikani_configured: boolean;
  realtime_model: string;
  analysis_model: string;
  sample_rate: number;
  anki_deck_name: string;
}

export const EMPTY_USAGE: UsageSnapshot = {
  model: '',
  response_count: 0,
  total_tokens: 0,
  input: { text_tokens: 0, cached_text_tokens: 0, audio_tokens: 0, cached_audio_tokens: 0 },
  output: { text_tokens: 0, cached_text_tokens: 0, audio_tokens: 0, cached_audio_tokens: 0 },
  cost_usd: 0,
  rates_known: true,
};
