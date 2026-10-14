import type { ReportInputs } from './reports';

export interface AISource {
  document_id: number; title: string; excerpt: string; source_url?: string | null;
  version_id?: number | null; section_id?: number | null; section_code?: string | null;
  heading?: string | null; page_start?: number | null; page_end?: number | null; viewer_path?: string | null;
}
export interface AIExternalSource { title: string; url: string }
export type AIChatAction =
  | { action_type: 'NAVIGATE'; data: { path: '/leaves' | '/attendance' | '/reports' | '/knowledge' } }
  | { action_type: 'DRAFT_LEAVE'; data: { leave_date: string; session: 'MORNING' | 'AFTERNOON' | 'FULL_DAY'; leave_type_id: number; reason?: string } }
  | { action_type: 'DRAFT_FIX'; data: { work_date: string; event_type: 'CHECK_IN' | 'CHECK_OUT'; requested_time?: string; reason?: string } }
  | { action_type: 'OPEN_REPORT'; data: ReportInputs & { path?: '/reports'; run_id?: string } }
  | { action_type: 'OPEN_APPROVALS'; data: { path: '/leaves' | '/attendance'; target: 'leave' | 'attendance'; tab: 'approvals' } }
  | { action_type: 'OPEN_KNOWLEDGE'; data: { path: '/knowledge' } }
  | { action_type: 'OPEN_SOURCE'; data: { document_id: number; version_id: number; section_id: number } }
  | { action_type: 'SHOW_DATA'; data: { type: 'LEAVE_BALANCE'; balance: string | null } | { type: 'ATTENDANCE_SUMMARY'; present_days: number; incomplete: string[] } };
export interface AISuggestion { suggestion_id: string; prompt: string; catalog_version: string; source?: AISource | null; default_inputs?: Partial<ReportInputs> }
export type AIDraftInputs = Partial<Record<'leave_date' | 'session' | 'leave_type_id' | 'reason' | 'work_date' | 'event_type' | 'requested_time' | 'kind' | 'start_date' | 'end_date' | 'scope' | 'department_id' | 'employee_id', string | number>>;
export interface AIChatRequest { message?: string; conversation_history?: { role: 'user' | 'assistant'; content: string }[]; suggestion_id?: string; report_run_id?: string; request_key?: string; draft_id?: string; draft_revision?: number; inputs?: AIDraftInputs }
export interface AIInputOption { field: 'kind' | 'scope' | 'leave_type_id'; value: string | number; label: string }
export interface AIChatResponse {
  reply: string; action: AIChatAction | null; sources: AISource[];
  external_sources: AIExternalSource[]; search_suggestions_html: string | null;
  answer_mode: 'RULE' | 'RAG' | 'GEMINI' | 'OUT_OF_SCOPE';
  draft_id: string | null; draft_revision: number | null; missing_fields: string[]; input_options: AIInputOption[];
  status: 'OK' | 'NEEDS_INPUT' | 'DENIED' | 'UNAVAILABLE' | 'OUT_OF_SCOPE'; message_code: string | null; request_id: string | null;
}
export interface AIError { code: string; message: string; request_id: string; action: null; sources: []; detail: string }
export const SESSION_EXPIRED_MESSAGE = 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại để tiếp tục.';
