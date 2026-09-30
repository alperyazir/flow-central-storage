import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import BookFilesUpdateDialog from 'components/BookFilesUpdateDialog';
import type { BookRecord } from 'lib/books';
import type { BookFilesUpdateReport } from 'lib/uploads';
import { useOperationsStore } from 'stores/operations';

const getStatus = vi.fn();
const discard = vi.fn();
const start = vi.fn();
const applyJob = vi.fn();
const cancel = vi.fn();

vi.mock('lib/uploads', () => ({
  getBookFilesUpdateStatus: (...args: unknown[]) => getStatus(...args),
  discardBookFilesUpdate: (...args: unknown[]) => discard(...args),
}));

vi.mock('lib/bookFilesUpdateJobs', () => ({
  startBookFilesUpdate: (...args: unknown[]) => start(...args),
  applyBookFilesUpdateJob: (...args: unknown[]) => applyJob(...args),
  cancelBookFilesUpload: (...args: unknown[]) => cancel(...args),
}));

const book = { id: 380, book_name: 'Dream_Test_Book_V_1', book_title: 'Dream Test Book 111', book_type: 'standard' } as BookRecord;

const report = (overrides: Partial<BookFilesUpdateReport> = {}): BookFilesUpdateReport => ({
  book_id: 380,
  book_name: 'Dream_Test_Book_V_1',
  book_title: 'Dream Test Book 111',
  dry_run: true,
  prune: false,
  root_folder: 'Dream_Test_Book_V_1',
  full_archive: true,
  config_book_title: 'Dream Test Book 2',
  invalid_json: [],
  counts: {
    written: 2,
    unchanged: 10,
    renamed: 0,
    replaced_same_name: 0,
    prune_candidates: 1,
    pruned: 0,
    protected_kept: 5,
  },
  written: ['games.json', 'assets/a.js'],
  unchanged: [],
  renamed: [],
  replaced_same_name: [],
  prune_candidates: ['audio/old.mp3'],
  pruned: [],
  ai_stale: false,
  warnings: [],
  metadata: {
    before: { book_title: 'Dream Test Book 111', total_size: 1 },
    after: { book_title: 'Dream Test Book 2', total_size: 2 },
    changed: ['book_title', 'total_size'],
  },
  content_version: 2,
  version_bumped: false,
  notified: false,
  notify_reason: 'title_changed',
  bundles_regenerating: false,
  ...overrides,
});

const ops = () => useOperationsStore.getState();

/** What startBookFilesUpdate leaves behind once the server's preview is ready. */
const previewReadyOp = (id = 'op1', jobId = 'job1') => {
  ops().addOperation({ id, type: 'update', bookName: book.book_name, bookId: 380, phase: 'uploading' });
  ops().updateOperation(id, { status: 'awaiting_review', jobId, progress: 100 });
  return id;
};

const renderDialog = (props: Partial<Parameters<typeof BookFilesUpdateDialog>[0]> = {}) => {
  const onClose = vi.fn();
  const onUpdated = vi.fn();
  const utils = render(
    <BookFilesUpdateDialog
      open
      book={book}
      token="t"
      tokenType="Bearer"
      onClose={onClose}
      onUpdated={onUpdated}
      {...props}
    />
  );
  return { ...utils, onClose, onUpdated };
};

describe('BookFilesUpdateDialog', () => {
  beforeEach(() => {
    [getStatus, discard, start, applyJob, cancel].forEach((m) => m.mockReset());
    useOperationsStore.setState({ operations: [] });
  });

  it('uploads as an operation and can be closed while it runs', async () => {
    const user = userEvent.setup();
    start.mockImplementation(() => {
      ops().addOperation({ id: 'op1', type: 'update', bookName: 'x', bookId: 380, phase: 'uploading' });
      ops().updateOperation('op1', { status: 'in_progress', progress: 20, detail: '10MB / 50MB' });
      return 'op1';
    });
    const { onClose } = renderDialog();

    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(['zip'], 'Dream.zip', { type: 'application/zip' }));
    await user.click(screen.getByRole('button', { name: /upload & preview/i }));

    expect(start.mock.calls[0][1]).toBe(book);
    expect(await screen.findByText('10MB / 50MB')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /continue in background/i }));
    expect(onClose).toHaveBeenCalled();
    expect(cancel).not.toHaveBeenCalled();
    expect(ops().operations[0].status).toBe('in_progress');
  });

  it('reviews a preview reopened from Operations, title change included', async () => {
    const opId = previewReadyOp();
    getStatus.mockResolvedValue({ step: 'preview_ready', result: report() });
    renderDialog({ reviewOpId: opId });

    expect(await screen.findByText('Title')).toBeInTheDocument();
    const titleRow = screen.getByText('Title').closest('tr') as HTMLElement;
    expect(titleRow).toHaveTextContent('Dream Test Book 111 → Dream Test Book 2');
    expect(screen.getByText(/still told about the new title/i)).toBeInTheDocument();
    expect(getStatus.mock.calls[0][0]).toBe('job1');
  });

  it('apply hands off to the background job with the chosen options', async () => {
    const user = userEvent.setup();
    const opId = previewReadyOp();
    getStatus.mockResolvedValue({ step: 'preview_ready', result: report() });
    applyJob.mockImplementation(async () => {
      ops().updateOperation(opId, { status: 'in_progress', phase: 'applying', detail: 'Applying...' });
    });
    renderDialog({ reviewOpId: opId });

    await user.click(await screen.findByLabelText(/publish to learn/i));
    await user.click(screen.getByRole('button', { name: /apply update/i }));

    await waitFor(() => expect(applyJob).toHaveBeenCalled());
    expect(applyJob.mock.calls[0].slice(0, 4)).toEqual([
      opId,
      380,
      'job1',
      { prune: false, bump_version: true, notify: true, regenerate_bundles: false },
    ]);
    expect(await screen.findByRole('button', { name: /continue in background/i })).toBeInTheDocument();
  });

  it('shows the final report once the operation completes', async () => {
    const opId = previewReadyOp();
    ops().updateOperation(opId, { status: 'completed', phase: 'applying' });
    getStatus.mockResolvedValue({
      step: 'completed',
      result: report({ dry_run: false, notified: true, counts: { ...report().counts, written: 13 } }),
    });
    const { onUpdated } = renderDialog({ reviewOpId: opId });

    expect(await screen.findByText(/updated: 13 file\(s\) written/i)).toBeInTheDocument();
    expect(screen.getByText(/learn was told about the new title/i)).toBeInTheDocument();
    expect(onUpdated).toHaveBeenCalledTimes(1);
  });

  it('a folder name mismatch must be confirmed before applying', async () => {
    const user = userEvent.setup();
    const opId = previewReadyOp();
    getStatus.mockResolvedValue({
      step: 'preview_ready',
      result: report({ warnings: [{ code: 'folder_name_mismatch', message: "The archive's folder is 'Other'" }] }),
    });
    renderDialog({ reviewOpId: opId });

    await screen.findByText(/archive's folder is 'Other'/i);
    const applyButton = screen.getByRole('button', { name: /apply update/i });
    expect(applyButton).toBeDisabled();
    await user.click(screen.getByLabelText(/i checked the warnings/i));
    expect(applyButton).toBeEnabled();
  });

  it('invalid games.json blocks apply until confirmed', async () => {
    const user = userEvent.setup();
    const opId = previewReadyOp();
    getStatus.mockResolvedValue({
      step: 'preview_ready',
      result: report({
        invalid_json: [{ path: 'games.json', error: 'the file is empty' }],
        warnings: [{ code: 'invalid_json', path: 'games.json', message: 'games.json is not valid JSON (the file is empty).' }],
      }),
    });
    renderDialog({ reviewOpId: opId });

    await screen.findByText(/games.json is not valid json/i);
    const applyButton = screen.getByRole('button', { name: /apply update/i });
    expect(applyButton).toBeDisabled();
    await user.click(screen.getByLabelText(/i checked the warnings/i));
    expect(applyButton).toBeEnabled();
  });

  it('an expired preview says so', async () => {
    const opId = previewReadyOp();
    getStatus.mockResolvedValue(null);
    renderDialog({ reviewOpId: opId });

    expect(await screen.findByText(/preview has expired/i)).toBeInTheDocument();
  });

  it('discard frees the staged upload and drops the operation', async () => {
    const user = userEvent.setup();
    const opId = previewReadyOp();
    getStatus.mockResolvedValue({ step: 'preview_ready', result: report() });
    renderDialog({ reviewOpId: opId });

    await user.click(await screen.findByRole('button', { name: /discard/i }));

    expect(discard).toHaveBeenCalledWith(380, 'job1', 't', 'Bearer', expect.anything());
    expect(ops().operations).toHaveLength(0);
  });

  it('pruning is unavailable for a partial archive', async () => {
    const opId = previewReadyOp();
    getStatus.mockResolvedValue({ step: 'preview_ready', result: report({ full_archive: false }) });
    renderDialog({ reviewOpId: opId });

    expect(await screen.findByLabelText(/needs the whole book folder/i)).toBeDisabled();
  });

  it('follows the operation when it changes under the open dialog', async () => {
    const opId = previewReadyOp();
    ops().updateOperation(opId, { status: 'in_progress', phase: 'applying', detail: 'Writing files...' });
    renderDialog({ reviewOpId: opId });

    expect(await screen.findByText('Writing files...')).toBeInTheDocument();
    getStatus.mockResolvedValue({ step: 'completed', result: report({ dry_run: false }) });
    act(() => ops().updateOperation(opId, { status: 'completed' }));
    expect(await screen.findByText(/updated: 2 file\(s\) written/i)).toBeInTheDocument();
  });
});
