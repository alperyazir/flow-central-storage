import { describe, it, expect, vi, beforeEach } from 'vitest';

import type { BookFilesUpdateReport } from 'lib/uploads';
import { useOperationsStore } from 'stores/operations';

const preview = vi.fn();
const requestApply = vi.fn();
const poll = vi.fn();

vi.mock('lib/uploads', () => ({
  authHeaderFor: (token: string, type: string) => `${type} ${token}`,
  previewBookFilesUpdate: (...args: unknown[]) => preview(...args),
  requestApplyBookFilesUpdate: (...args: unknown[]) => requestApply(...args),
  pollBookFilesUpdate: (...args: unknown[]) => poll(...args),
}));

import {
  applyBookFilesUpdateJob,
  cancelBookFilesUpload,
  resumeBookFilesUpdates,
  startBookFilesUpdate,
  summarizeBookFilesUpdate,
} from 'lib/bookFilesUpdateJobs';

const ops = () => useOperationsStore.getState();
const flush = () => new Promise((r) => setTimeout(r, 0));

const finalReport = {
  counts: { written: 13, unchanged: 500, renamed: 0, replaced_same_name: 1, prune_candidates: 0, pruned: 0, protected_kept: 531 },
  metadata: {
    before: { book_title: 'Old', activity_count: 3 },
    after: { book_title: 'New', activity_count: 4 },
    changed: ['book_title', 'activity_count'],
  },
  version_bumped: true,
  content_version: 3,
  notified: true,
  notify_reason: 'publish',
  bundles_regenerating: false,
  ai_stale: false,
} as unknown as BookFilesUpdateReport;

describe('book file update operations', () => {
  beforeEach(() => {
    [preview, requestApply, poll].forEach((m) => m.mockReset());
    useOperationsStore.setState({ operations: [] });
  });

  it('an upload becomes an operation that waits for review', async () => {
    let onProgress: (p: { progress: number; detail: string; step: string }) => void = () => {};
    preview.mockImplementation((_file, _id, _t, _tt, cb) => {
      onProgress = cb;
      return { promise: Promise.resolve({ jobId: 'job9', report: {} }), abort: vi.fn() };
    });

    const opId = startBookFilesUpdate(new File(['z'], 'b.zip'), { id: 380, book_name: 'B' }, 't', 'Bearer');
    onProgress({ progress: 30, detail: '30MB', step: 'uploading' });
    expect(ops().operations[0]).toMatchObject({ id: opId, type: 'update', bookId: 380, phase: 'uploading', progress: 30 });

    await flush();
    expect(ops().operations[0]).toMatchObject({ status: 'awaiting_review', jobId: 'job9' });
  });

  it('a failed or cancelled upload fails the operation', async () => {
    const abort = vi.fn();
    preview.mockReturnValue({ promise: Promise.reject(new Error('Upload aborted')), abort });

    const opId = startBookFilesUpdate(new File(['z'], 'b.zip'), { id: 380, book_name: 'B' }, 't', 'Bearer');
    cancelBookFilesUpload(opId);
    await flush();

    expect(abort).toHaveBeenCalled();
    expect(ops().operations[0]).toMatchObject({ status: 'failed', error: 'Upload aborted' });
  });

  it('apply runs in the background and ends with a summary', async () => {
    ops().addOperation({ id: 'op1', type: 'update', bookName: 'B', bookId: 380 });
    ops().updateOperation('op1', { status: 'awaiting_review', jobId: 'job1' });
    requestApply.mockResolvedValue(undefined);
    poll.mockResolvedValue({ step: 'completed', result: finalReport });

    await applyBookFilesUpdateJob('op1', 380, 'job1', { prune: false, bump_version: true, notify: true, regenerate_bundles: false }, 't', 'Bearer');
    expect(requestApply.mock.calls[0].slice(0, 3)).toEqual([380, 'job1', { prune: false, bump_version: true, notify: true, regenerate_bundles: false }]);
    await flush();

    expect(ops().operations[0]).toMatchObject({ status: 'completed', phase: 'applying' });
    expect(ops().operations[0].summary).toEqual(summarizeBookFilesUpdate(finalReport));
  });

  it('a rejected apply leaves the preview in place', async () => {
    ops().addOperation({ id: 'op1', type: 'update', bookName: 'B', bookId: 380 });
    ops().updateOperation('op1', { status: 'awaiting_review', jobId: 'job1' });
    requestApply.mockRejectedValue(new Error('This update is not waiting to be applied'));

    await expect(
      applyBookFilesUpdateJob('op1', 380, 'job1', { prune: false, bump_version: false, notify: false, regenerate_bundles: false }, 't', 'Bearer')
    ).rejects.toThrow('not waiting');
    expect(ops().operations[0].status).toBe('awaiting_review');
    expect(poll).not.toHaveBeenCalled();
  });

  it('applies still running after a reload are polled again, once', async () => {
    ops().addOperation({ id: 'op2', type: 'update', bookName: 'B', bookId: 380, phase: 'applying' });
    ops().updateOperation('op2', { status: 'in_progress', jobId: 'job2' });
    poll.mockReturnValue(new Promise(() => {}));

    resumeBookFilesUpdates('t', 'Bearer');
    resumeBookFilesUpdates('t', 'Bearer');

    expect(poll).toHaveBeenCalledTimes(1);
    expect(poll.mock.calls[0][0]).toBe('job2');
  });

  it('summary covers title, activities, version and Learn', () => {
    expect(summarizeBookFilesUpdate(finalReport)).toEqual([
      '13 written, 500 unchanged',
      '1 replaced under the same name',
      'Title: Old → New',
      'Activities: 3 → 4',
      'Content version 3',
      'Published to Learn',
    ]);
  });
});

describe('operations store after a reload', () => {
  it('keeps applying updates alive and fails interrupted uploads', async () => {
    localStorage.setItem(
      'fcs-operations',
      JSON.stringify({
        state: {
          isExpanded: true,
          operations: [
            { id: 'a', type: 'update', bookName: 'B', status: 'in_progress', phase: 'applying', jobId: 'j', progress: 70, timestamp: '' },
            { id: 'u', type: 'update', bookName: 'B', status: 'in_progress', phase: 'uploading', progress: 20, timestamp: '' },
            { id: 'r', type: 'update', bookName: 'B', status: 'awaiting_review', jobId: 'k', progress: 100, timestamp: '' },
          ],
        },
        version: 0,
      })
    );

    await useOperationsStore.persist.rehydrate();

    const byId = Object.fromEntries(ops().operations.map((o) => [o.id, o.status]));
    expect(byId).toEqual({ a: 'in_progress', u: 'failed', r: 'awaiting_review' });
  });
});
