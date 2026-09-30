import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import BookFilesUpdateDialog from 'components/BookFilesUpdateDialog';
import type { BookRecord } from 'lib/books';
import type { BookFilesUpdateReport } from 'lib/uploads';

const preview = vi.fn();
const apply = vi.fn();
const discard = vi.fn();

vi.mock('lib/uploads', () => ({
  previewBookFilesUpdate: (...args: unknown[]) => preview(...args),
  applyBookFilesUpdate: (...args: unknown[]) => apply(...args),
  discardBookFilesUpdate: (...args: unknown[]) => discard(...args),
}));

const book = { id: 349, book_name: 'Glory_Trio_3', book_title: 'Glory Trio 3', book_type: 'standard' } as BookRecord;

const report = (overrides: Partial<BookFilesUpdateReport> = {}): BookFilesUpdateReport => ({
  book_id: 349,
  book_name: 'Glory_Trio_3',
  book_title: 'Glory Trio 3',
  dry_run: true,
  prune: false,
  root_folder: 'Glory_Trio_3',
  full_archive: true,
  config_book_title: 'Glory Trio 3',
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
  metadata: { before: { total_size: 1 }, after: { total_size: 2 }, changed: ['total_size'] },
  content_version: 2,
  version_bumped: false,
  notified: false,
  bundles_regenerating: false,
  ...overrides,
});

const renderDialog = () =>
  render(
    <BookFilesUpdateDialog open book={book} token="t" tokenType="Bearer" onClose={() => {}} onUpdated={() => {}} />
  );

const uploadZip = async (user: ReturnType<typeof userEvent.setup>) => {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  await user.upload(input, new File(['zip'], 'Glory_Trio_3.zip', { type: 'application/zip' }));
  await user.click(screen.getByRole('button', { name: /upload & preview/i }));
};

describe('BookFilesUpdateDialog', () => {
  beforeEach(() => {
    preview.mockReset();
    apply.mockReset();
    discard.mockReset();
  });

  it('shows the preview and applies with safe defaults', async () => {
    const user = userEvent.setup();
    preview.mockReturnValue({ promise: Promise.resolve({ jobId: 'job1', report: report() }), abort: vi.fn() });
    apply.mockResolvedValue(report({ dry_run: false }));
    renderDialog();

    await uploadZip(user);
    expect(preview.mock.calls[0][1]).toBe(349);
    await screen.findByText(/new or changed files/i);

    await user.click(screen.getByRole('button', { name: /apply update/i }));

    await waitFor(() => expect(apply).toHaveBeenCalled());
    expect(apply.mock.calls[0].slice(0, 3)).toEqual([
      349,
      'job1',
      { prune: false, bump_version: false, notify: false, regenerate_bundles: false },
    ]);
    await screen.findByText(/updated: 2 file\(s\) written/i);
  });

  it('publishing to Learn always bumps the version', async () => {
    const user = userEvent.setup();
    preview.mockReturnValue({ promise: Promise.resolve({ jobId: 'job2', report: report() }), abort: vi.fn() });
    apply.mockResolvedValue(report());
    renderDialog();

    await uploadZip(user);
    await user.click(await screen.findByLabelText(/publish to learn/i));
    await user.click(screen.getByRole('button', { name: /apply update/i }));

    await waitFor(() => expect(apply).toHaveBeenCalled());
    expect(apply.mock.calls[0][2]).toMatchObject({ notify: true, bump_version: true });
  });

  it('a folder name mismatch must be confirmed before applying', async () => {
    const user = userEvent.setup();
    const warned = report({
      root_folder: 'Glory_Trio_4',
      warnings: [{ code: 'folder_name_mismatch', message: "The archive's folder is 'Glory_Trio_4'" }],
    });
    preview.mockReturnValue({ promise: Promise.resolve({ jobId: 'job3', report: warned }), abort: vi.fn() });
    renderDialog();

    await uploadZip(user);
    await screen.findByText(/archive's folder is 'Glory_Trio_4'/i);
    const applyButton = screen.getByRole('button', { name: /apply update/i });
    expect(applyButton).toBeDisabled();

    await user.click(screen.getByLabelText(/i checked the warnings/i));
    expect(applyButton).toBeEnabled();
  });

  it('pruning is unavailable for a partial archive', async () => {
    const user = userEvent.setup();
    preview.mockReturnValue({
      promise: Promise.resolve({ jobId: 'job4', report: report({ full_archive: false }) }),
      abort: vi.fn(),
    });
    renderDialog();

    await uploadZip(user);

    expect(await screen.findByLabelText(/needs the whole book folder/i)).toBeDisabled();
  });

  it('discarding the preview frees the staged upload', async () => {
    const user = userEvent.setup();
    preview.mockReturnValue({ promise: Promise.resolve({ jobId: 'job5', report: report() }), abort: vi.fn() });
    renderDialog();

    await uploadZip(user);
    await user.click(await screen.findByRole('button', { name: /discard/i }));

    expect(discard).toHaveBeenCalledWith(349, 'job5', 't', 'Bearer', expect.anything());
  });
});
