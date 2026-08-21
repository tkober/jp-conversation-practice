/** Shared types mirroring the backend's JSON contracts. */

export type JlptLevel = 'N5' | 'N4' | 'N3' | 'N2';

export type SessionPhase = 'setup' | 'connecting' | 'live' | 'analysing' | 'review';

export interface Scenario {
  id: number;
  slug: string;
  title: string;
  summary: string;
  prompt: string;
  is_builtin: boolean;
  is_customized: boolean;
}

export interface ScenarioDraft {
  title: string;
  summary: string;
  prompt: string;
}

export interface AssistantMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface AssistantReply {
  reply: string;
  suggested_prompt: string | null;
}

export interface AppSettingsView {
  realtime_model: string;
  analysis_model: string;
  scenario_assistant_model: string;
  transcription_model: string;
  tts_model: string;
  realtime_voice: string;
  realtime_speed: number;
  ankiconnect_url: string;
  anki_deck_name: string;
  openai_api_key_set: boolean;
  openai_api_key_hint: string | null;
  openai_api_key_from_env: boolean;
  wanikani_api_token_set: boolean;
  wanikani_api_token_hint: string | null;
  wanikani_api_token_from_env: boolean;
  speed_min: number;
  speed_max: number;
}

export type AppSettingsPatch = Partial<{
  openai_api_key: string;
  realtime_model: string;
  analysis_model: string;
  scenario_assistant_model: string;
  transcription_model: string;
  tts_model: string;
  realtime_voice: string;
  realtime_speed: number;
  wanikani_api_token: string;
  ankiconnect_url: string;
  anki_deck_name: string;
}>;

export interface SessionSummary {
  id: number;
  scenario_title: string;
  jlpt_level: string;
  model: string;
  voice: string;
  started_at: string;
  duration_seconds: number;
  cost_usd: number;
  turn_count: number;
  has_analysis: boolean;
}

export interface SessionDetail extends SessionSummary {
  scenario_prompt: string;
  speed: number;
  instructions: string;
  usage: UsageSnapshot;
  transcript: TranscriptTurn[];
  analysis: AnalysisResponse | null;
}

export interface SessionStats {
  session_count: number;
  total_cost_usd: number;
  total_seconds: number;
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
