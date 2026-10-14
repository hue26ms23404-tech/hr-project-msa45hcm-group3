/** Pure UI decisions; server authorization and draft ownership remain authoritative. */
import type { AISuggestion } from '../types/ai';

export function actorChanged(previous?: number, current?: number): boolean {
  return previous !== current;
}

export function readableISODate(value: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const [year, month, day] = value.split('-').map(Number);
  const parsed = new Date(Date.UTC(year, month - 1, day));
  return parsed.getUTCFullYear() === year && parsed.getUTCMonth() + 1 === month && parsed.getUTCDate() === day
    ? `${String(day).padStart(2, '0')}/${String(month).padStart(2, '0')}/${year}` : value;
}
export function chatFailureState(status?: number, code?: string) {
  return {
    clearDraft: status === 404 && code === 'AI_DRAFT_NOT_FOUND'
      || status === 409 && code === 'AI_DRAFT_NOT_COLLECTING'
      || status === 410 && code === 'AI_DRAFT_EXPIRED',
    restartRequired: status === 409 && code === 'AI_DRAFT_REVISION_CONFLICT',
    // /ai/chat can persist a draft or report before an HTTP response reaches
    // the browser. Missing responses and 5xx never authorize replaying this POST.
    retryable: status === 429,
    uncertainOutcome: status === undefined || status >= 500,
  };
}

export function acceptsServerSuggestions(value: unknown): value is AISuggestion[] {
  return Array.isArray(value) && value.every((item) => item && typeof item.suggestion_id === 'string'
    && typeof item.prompt === 'string' && typeof item.catalog_version === 'string');
}

/** A conflicting revision must be explicitly discarded or resynced before a new send. */
export function canSubmitChat(restartRequired: boolean): boolean {
  return !restartRequired;
}


/** Idempotency token, not authentication; supported on insecure localhost too. */
export function newChatRequestKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}
