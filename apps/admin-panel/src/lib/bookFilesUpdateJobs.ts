/**
 * Book file updates as background operations.
 *
 * The upload and the apply run outside any component and report through the
 * Operations store, so closing the dialog never stops them. The dry-run
 * preview is reviewed in the dialog, reopened from Operations if it was
 * closed. An apply runs on the server; after a page reload it is re-polled.
 */
import { useOperationsStore } from 'stores/operations';
import { appConfig } from 'config/environment';

import {
  authHeaderFor,
  pollBookFilesUpdate,
  previewBookFilesUpdate,
  requestApplyBookFilesUpdate,
  type BookFilesUpdateOptions,
  type BookFilesUpdateReport,
} from './uploads';

const store = () => useOperationsStore.getState();

// Uploads that can still be cancelled, and applies already being polled.
const aborts = new Map<string, () => void>();
const polling = new Set<string>();

export const summarizeBookFilesUpdate = (report: BookFilesUpdateReport): string[] => {
  const c = report.counts;
  const lines = [`${c.written} written, ${c.unchanged} unchanged${c.pruned ? `, ${c.pruned} removed` : ''}`];
  if (c.replaced_same_name) lines.push(`${c.replaced_same_name} replaced under the same name`);
  const changed = report.metadata.changed;
  if (changed.includes('book_title')) {
    lines.push(`Title: ${report.metadata.before.book_title ?? '—'} → ${report.metadata.after.book_title}`);
  }
  if (changed.includes('activity_count')) {
    lines.push(`Activities: ${report.metadata.before.activity_count ?? 0} → ${report.metadata.after.activity_count}`);
  }
  if (report.version_bumped) lines.push(`Content version ${report.content_version}`);
  if (report.notified) {
    lines.push(report.notify_reason === 'title_changed' ? 'Learn notified (title only)' : 'Published to Learn');
  }
  if (report.bundles_regenerating) lines.push('Bundles rebuilding');
  if (report.ai_stale) lines.push('PDF changed: AI data needs reprocessing');
  return lines;
};

/** Upload an archive and run the server's dry run. Returns the operation id. */
export const startBookFilesUpdate = (
  file: File,
  book: { id: number; book_name: string; book_title?: string | null },
  token: string,
  tokenType: string
): string => {
  const opId = `update-${book.id}-${Date.now()}`;
  store().addOperation({
    id: opId,
    type: 'update',
    bookName: book.book_title || book.book_name,
    bookId: book.id,
    phase: 'uploading',
  });
  store().updateOperation(opId, { status: 'in_progress', detail: 'Uploading...' });

  const { promise, abort } = previewBookFilesUpdate(
    file,
    book.id,
    token,
    tokenType,
    (s) => store().updateOperation(opId, { progress: s.progress, detail: s.detail || s.step }),
    appConfig.apiBaseUrl
  );
  aborts.set(opId, abort);
  promise
    .then(({ jobId }) => {
      store().updateOperation(opId, {
        status: 'awaiting_review',
        progress: 100,
        jobId,
        detail: 'Preview ready: review and apply',
      });
    })
    .catch((exc) => {
      store().updateOperation(opId, {
        status: 'failed',
        error: exc instanceof Error ? exc.message : 'Upload failed',
      });
    })
    .finally(() => aborts.delete(opId));
  return opId;
};

export const cancelBookFilesUpload = (opId: string) => {
  aborts.get(opId)?.();
};

const pollApply = (opId: string, jobId: string, token: string, tokenType: string) => {
  if (polling.has(opId)) return;
  polling.add(opId);
  pollBookFilesUpdate(
    jobId,
    authHeaderFor(token, tokenType),
    ['completed'],
    (s) => store().updateOperation(opId, { progress: s.progress, detail: s.detail || s.step }),
    { aborted: false },
    appConfig.apiBaseUrl
  )
    .then((final) => {
      store().updateOperation(opId, {
        status: 'completed',
        progress: 100,
        detail: 'Book files updated',
        summary: final.result ? summarizeBookFilesUpdate(final.result) : undefined,
      });
    })
    .catch((exc) => {
      store().updateOperation(opId, {
        status: 'failed',
        error: exc instanceof Error ? exc.message : 'Update failed',
      });
    })
    .finally(() => polling.delete(opId));
};

/** Start applying a reviewed update; progress continues under Operations. */
export const applyBookFilesUpdateJob = async (
  opId: string,
  bookId: number,
  jobId: string,
  options: BookFilesUpdateOptions,
  token: string,
  tokenType: string
): Promise<void> => {
  await requestApplyBookFilesUpdate(bookId, jobId, options, token, tokenType, appConfig.apiBaseUrl);
  store().updateOperation(opId, { status: 'in_progress', phase: 'applying', progress: 65, detail: 'Applying...' });
  pollApply(opId, jobId, token, tokenType);
};

/** Re-poll applies that were running when the page was reloaded. */
export const resumeBookFilesUpdates = (token: string, tokenType: string) => {
  for (const op of store().operations) {
    if (op.type === 'update' && op.status === 'in_progress' && op.phase === 'applying' && op.jobId) {
      pollApply(op.id, op.jobId, token, tokenType);
    }
  }
};
