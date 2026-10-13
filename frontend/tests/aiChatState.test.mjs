import test from 'node:test';
import assert from 'node:assert/strict';
import { acceptsServerSuggestions, actorChanged, chatFailureState, readableISODate } from '../src/api/aiChatState.ts';

test('server suggestions are accepted without local role filtering', () => {
  const response = [{ suggestion_id: 'knowledge_admin', prompt: 'Mở tài liệu', catalog_version: 'v1' }];
  assert.equal(acceptsServerSuggestions(response), true);
  assert.equal(acceptsServerSuggestions(undefined), false);
  assert.equal(acceptsServerSuggestions([{ suggestion_id: 'knowledge_admin' }]), false);
});

test('provider timeout during follow-up preserves draft and allows retry', () => {
  assert.deepEqual(chatFailureState(503, 'AI_UNAVAILABLE'), { clearDraft: false, retryable: true });
  assert.deepEqual(chatFailureState(undefined), { clearDraft: false, retryable: true });
});

test('expiry and authorization have different recovery', () => {
  assert.deepEqual(chatFailureState(410, 'AI_DRAFT_EXPIRED'), { clearDraft: true, retryable: false });
  assert.deepEqual(chatFailureState(403, 'PERMISSION_DENIED'), { clearDraft: false, retryable: false });
});

test('validated date is readable and actor switch is detected', () => {
  assert.equal(readableISODate('2026-11-02'), '02/11/2026');
  assert.equal(readableISODate('2026-02-31'), '2026-02-31');
  assert.equal(actorChanged(9, 10), true);
  assert.equal(actorChanged(9, 9), false);
});
