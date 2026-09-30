import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle, FileUp, Loader2, ShieldCheck } from 'lucide-react';

import { Button } from 'components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from 'components/ui/dialog';
import { Alert, AlertDescription } from 'components/ui/alert';
import { Checkbox } from 'components/ui/checkbox';
import { Label } from 'components/ui/label';
import { Progress } from 'components/ui/progress';

import type { BookRecord } from 'lib/books';
import {
  discardBookFilesUpdate,
  getBookFilesUpdateStatus,
  type BookFilesUpdateOptions,
  type BookFilesUpdateReport,
} from 'lib/uploads';
import {
  applyBookFilesUpdateJob,
  cancelBookFilesUpload,
  startBookFilesUpdate,
} from 'lib/bookFilesUpdateJobs';
import { useOperationsStore, type Operation } from 'stores/operations';
import { appConfig } from 'config/environment';

interface BookFilesUpdateDialogProps {
  open: boolean;
  book: BookRecord;
  token: string;
  tokenType: string;
  onClose: () => void;
  onUpdated?: () => void;
  /** Reopen an update already started (from the Operations panel). */
  reviewOpId?: string | null;
}

type Stage = 'select' | 'uploading' | 'preview' | 'applying' | 'done' | 'failed';

const NO_OPTIONS: BookFilesUpdateOptions = {
  prune: false,
  bump_version: false,
  notify: false,
  regenerate_bundles: false,
};

const stageOf = (op: Operation | undefined): Stage => {
  if (!op) return 'select';
  if (op.status === 'awaiting_review') return 'preview';
  if (op.status === 'completed') return 'done';
  if (op.status === 'failed') return 'failed';
  return op.phase === 'applying' ? 'applying' : 'uploading';
};

const formatBytes = (bytes: number | undefined): string => {
  if (!bytes) return '—';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
};

const PathList = ({ title, paths }: { title: string; paths: string[] }) => {
  if (!paths.length) return null;
  return (
    <details className="rounded-md border px-3 py-2 text-sm">
      <summary className="cursor-pointer select-none">
        {title} <span className="text-muted-foreground">({paths.length})</span>
      </summary>
      <ul className="mt-2 max-h-40 overflow-auto font-mono text-xs leading-5 break-all">
        {paths.map((p) => (
          <li key={p}>{p}</li>
        ))}
      </ul>
    </details>
  );
};

const FIELD_LABELS: Record<string, string> = {
  book_title: 'Title',
  activity_count: 'Activities',
  activity_details: 'Activity types',
  book_cover: 'Cover',
  total_size: 'Size',
};

const MetadataDiff = ({ report }: { report: BookFilesUpdateReport }) => {
  const { before, after, changed } = report.metadata;
  if (!changed.length) {
    return <p className="text-sm text-muted-foreground">Book details (title, activities, cover, size) stay the same.</p>;
  }
  const show = (field: string, value: unknown) =>
    field === 'total_size'
      ? formatBytes(value as number)
      : field === 'activity_details'
        ? Object.entries((value as Record<string, number>) || {})
            .map(([k, v]) => `${k}: ${v}`)
            .join(', ') || '—'
        : String(value ?? '—');
  return (
    <table className="w-full text-sm">
      <tbody>
        {changed.map((field) => (
          <tr key={field} className="align-top">
            <td className="py-1 pr-3 text-muted-foreground whitespace-nowrap">{FIELD_LABELS[field] ?? field}</td>
            <td className="py-1 break-words">
              <span className="line-through text-muted-foreground">
                {show(field, before[field as keyof typeof before])}
              </span>{' '}
              → {show(field, after[field as keyof typeof after])}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};

/**
 * Replace or add files of an existing book without touching its AI data.
 * Upload → server dry run → review → apply. Nothing is written before apply.
 * The upload and the apply run as Operations, so the dialog can be closed at
 * any point and reopened from there.
 */
const BookFilesUpdateDialog = ({
  open,
  book,
  token,
  tokenType,
  onClose,
  onUpdated,
  reviewOpId,
}: BookFilesUpdateDialogProps) => {
  const [opId, setOpId] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<BookFilesUpdateReport | null>(null);
  const [options, setOptions] = useState<BookFilesUpdateOptions>(NO_OPTIONS);
  const [confirmed, setConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const notifiedDone = useRef<string | null>(null);

  const op = useOperationsStore((s) => (opId ? s.operations.find((o) => o.id === opId) : undefined));
  const removeOperation = useOperationsStore((s) => s.removeOperation);
  const stage = stageOf(op);

  useEffect(() => {
    if (open) {
      setOpId(reviewOpId ?? null);
    } else {
      setOpId(null);
      setFile(null);
      setError(null);
      setReport(null);
      setOptions(NO_OPTIONS);
      setConfirmed(false);
      setSubmitting(false);
    }
  }, [open, reviewOpId]);

  // The preview and the final report live on the server; read the one the
  // operation is at whenever the dialog shows it.
  const jobId = op?.jobId;
  useEffect(() => {
    if (!open || !jobId || (stage !== 'preview' && stage !== 'done')) return;
    const wantDryRun = stage === 'preview';
    if (report && report.dry_run === wantDryRun) return;
    let cancelled = false;
    getBookFilesUpdateStatus(jobId, token, tokenType, appConfig.apiBaseUrl)
      .then((s) => {
        if (cancelled) return;
        if (s?.result && s.result.dry_run === wantDryRun) {
          setReport(s.result);
        } else if (wantDryRun) {
          setError('This preview has expired. Upload the ZIP again.');
        }
      })
      .catch((exc) => !cancelled && setError(exc instanceof Error ? exc.message : 'Failed to load the report'));
    return () => {
      cancelled = true;
    };
  }, [open, jobId, stage, report, token, tokenType]);

  useEffect(() => {
    if (stage === 'done' && opId && notifiedDone.current !== opId) {
      notifiedDone.current = opId;
      onUpdated?.();
    }
  }, [stage, opId, onUpdated]);

  const needsConfirm = !!report?.warnings.some((w) => w.code === 'folder_name_mismatch');

  const startUpload = () => {
    if (!file) return;
    setError(null);
    setReport(null);
    setOpId(startBookFilesUpdate(file, book, token, tokenType));
  };

  const apply = async () => {
    if (!opId || !jobId) return;
    setSubmitting(true);
    setError(null);
    try {
      await applyBookFilesUpdateJob(opId, book.id, jobId, options, token, tokenType);
      setReport(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Update failed');
    } finally {
      setSubmitting(false);
    }
  };

  const discard = () => {
    if (opId && jobId) {
      void discardBookFilesUpdate(book.id, jobId, token, tokenType, appConfig.apiBaseUrl);
      removeOperation(opId);
    }
    onClose();
  };

  const startOver = () => {
    if (opId) removeOperation(opId);
    setOpId(null);
    setFile(null);
    setError(null);
    setReport(null);
  };

  const setOption = (key: keyof BookFilesUpdateOptions, value: boolean) =>
    setOptions((prev) => {
      const next = { ...prev, [key]: value };
      // Learn only re-syncs activities when the version moves, so these go together.
      if (key === 'notify') next.bump_version = value;
      if (key === 'bump_version' && !value) next.notify = false;
      return next;
    });

  const titleChanges = !!report?.metadata.changed.includes('book_title');

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Update Book Files</DialogTitle>
          <DialogDescription>
            Add or replace files of <strong>{book.book_title || book.book_name}</strong> (
            <span className="font-mono">{book.book_name}</span>). AI data and additional resources are kept.
          </DialogDescription>
        </DialogHeader>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {stage === 'select' && (
          <div className="space-y-3">
            <input
              ref={inputRef}
              type="file"
              accept=".zip"
              className="hidden"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                e.target.value = '';
              }}
            />
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              className="flex w-full flex-col items-center gap-2 rounded-md border-2 border-dashed p-6 text-sm text-muted-foreground hover:bg-muted/50"
            >
              <FileUp className="h-6 w-6" />
              {file ? (
                <span className="text-foreground break-all">
                  {file.name} · {formatBytes(file.size)}
                </span>
              ) : (
                'Choose a ZIP: the whole book folder, or only the files to add'
              )}
            </button>
            <p className="text-xs text-muted-foreground">
              The files always go into this book, whatever the ZIP or folder is called. You will see what changes
              before anything is written.
            </p>
          </div>
        )}

        {(stage === 'uploading' || stage === 'applying') && (
          <div className="space-y-2 py-2">
            <Progress value={op?.progress ?? 0} />
            <p className="text-sm text-muted-foreground flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" /> {op?.detail}
            </p>
            <p className="text-xs text-muted-foreground">
              {stage === 'uploading'
                ? 'You can close this window; the upload continues under Operations (keep this tab open). You will be able to review the changes from there.'
                : 'You can close this window; the update runs on the server and its result shows under Operations.'}
            </p>
          </div>
        )}

        {stage === 'failed' && (
          <Alert variant="destructive">
            <AlertDescription>{op?.error || 'The update failed.'}</AlertDescription>
          </Alert>
        )}

        {stage === 'done' && !report && op?.summary && (
          <Alert>
            <CheckCircle className="h-4 w-4" />
            <AlertDescription>{op.summary.join(' · ')}</AlertDescription>
          </Alert>
        )}

        {(stage === 'preview' || stage === 'done') && !report && !error && (
          <p className="text-sm text-muted-foreground flex items-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading the report...
          </p>
        )}

        {(stage === 'preview' || stage === 'done') && report && (
          <div className="space-y-4">
            {stage === 'done' ? (
              <Alert>
                <CheckCircle className="h-4 w-4" />
                <AlertDescription>
                  Updated: {report.counts.written} file(s) written
                  {report.counts.pruned ? `, ${report.counts.pruned} removed` : ''}.
                  {report.version_bumped ? ` Content version is now ${report.content_version}.` : ''}
                  {report.notified
                    ? report.notify_reason === 'title_changed'
                      ? ' Learn was told about the new title.'
                      : ' Published to Learn.'
                    : ''}
                  {report.bundles_regenerating ? ' Bundles are being rebuilt.' : ''}
                </AlertDescription>
              </Alert>
            ) : (
              report.warnings.map((w) => (
                <Alert key={w.code} variant={w.code === 'ai_stale' ? 'destructive' : 'default'}>
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>{w.message}</AlertDescription>
                </Alert>
              ))
            )}

            <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
              <div className="rounded-md border p-2">
                <div className="text-xs text-muted-foreground">{stage === 'done' ? 'Written' : 'To write'}</div>
                <div className="text-lg font-semibold">{report.counts.written}</div>
              </div>
              <div className="rounded-md border p-2">
                <div className="text-xs text-muted-foreground">Unchanged</div>
                <div className="text-lg font-semibold">{report.counts.unchanged}</div>
              </div>
              <div className="rounded-md border p-2">
                <div className="text-xs text-muted-foreground">Not in ZIP</div>
                <div className="text-lg font-semibold">{report.counts.prune_candidates}</div>
              </div>
              <div className="rounded-md border p-2">
                <div className="text-xs text-muted-foreground flex items-center gap-1">
                  <ShieldCheck className="h-3 w-3" /> Protected
                </div>
                <div className="text-lg font-semibold">{report.counts.protected_kept}</div>
              </div>
            </div>

            <div className="space-y-2">
              <PathList title="New or changed files" paths={report.written} />
              <PathList title="Existing files that get new content" paths={report.replaced_same_name} />
              <PathList title="Renamed while normalizing" paths={report.renamed.map((r) => `${r.from} → ${r.to}`)} />
              <PathList
                title={
                  stage === 'done' && report.pruned.length ? 'Removed' : 'Stored files not in the ZIP (kept unless pruned)'
                }
                paths={stage === 'done' && report.pruned.length ? report.pruned : report.prune_candidates}
              />
            </div>

            <div>
              <div className="mb-1 text-sm font-medium">Book details</div>
              <MetadataDiff report={report} />
            </div>

            {stage === 'preview' && (
              <div className="space-y-3 rounded-md border p-3">
                <div className="flex items-start gap-2">
                  <Checkbox
                    id="uf-prune"
                    checked={options.prune}
                    disabled={!report.full_archive || report.counts.prune_candidates === 0}
                    onCheckedChange={(v) => setOption('prune', v === true)}
                  />
                  <Label htmlFor="uf-prune" className="text-sm font-normal leading-5">
                    Remove the {report.counts.prune_candidates} stored file(s) that are not in the ZIP
                    {!report.full_archive && ' (needs the whole book folder)'}
                  </Label>
                </div>
                <div className="flex items-start gap-2">
                  <Checkbox
                    id="uf-notify"
                    checked={options.notify}
                    onCheckedChange={(v) => setOption('notify', v === true)}
                  />
                  <Label htmlFor="uf-notify" className="text-sm font-normal leading-5">
                    Publish to Learn: bump the content version and notify Learn, which re-syncs the activities
                    {titleChanges && !options.notify && (
                      <span className="block text-xs text-muted-foreground">
                        Unchecked, Learn is still told about the new title (no activity re-sync).
                      </span>
                    )}
                  </Label>
                </div>
                <div className="flex items-start gap-2">
                  <Checkbox
                    id="uf-bundles"
                    checked={options.regenerate_bundles}
                    onCheckedChange={(v) => setOption('regenerate_bundles', v === true)}
                  />
                  <Label htmlFor="uf-bundles" className="text-sm font-normal leading-5">
                    Rebuild the standalone app bundles
                  </Label>
                </div>
                {needsConfirm && (
                  <div className="flex items-start gap-2 border-t pt-3">
                    <Checkbox id="uf-confirm" checked={confirmed} onCheckedChange={(v) => setConfirmed(v === true)} />
                    <Label htmlFor="uf-confirm" className="text-sm font-normal leading-5">
                      I checked the warnings above. This ZIP belongs to{' '}
                      <span className="font-mono">{book.book_name}</span>.
                    </Label>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          {stage === 'select' && (
            <>
              <Button variant="outline" onClick={onClose}>
                Cancel
              </Button>
              <Button onClick={startUpload} disabled={!file}>
                Upload & preview
              </Button>
            </>
          )}
          {stage === 'uploading' && (
            <>
              <Button variant="outline" onClick={() => opId && cancelBookFilesUpload(opId)}>
                Cancel upload
              </Button>
              <Button onClick={onClose}>Continue in background</Button>
            </>
          )}
          {stage === 'preview' && (
            <>
              <Button variant="outline" onClick={discard}>
                Discard
              </Button>
              <Button onClick={apply} disabled={!report || submitting || (needsConfirm && !confirmed)}>
                {submitting && <Loader2 className="h-4 w-4 animate-spin" />} Apply update
              </Button>
            </>
          )}
          {stage === 'applying' && <Button onClick={onClose}>Continue in background</Button>}
          {stage === 'failed' && (
            <>
              <Button variant="outline" onClick={onClose}>
                Close
              </Button>
              <Button onClick={startOver}>Start over</Button>
            </>
          )}
          {stage === 'done' && <Button onClick={onClose}>Close</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default BookFilesUpdateDialog;
