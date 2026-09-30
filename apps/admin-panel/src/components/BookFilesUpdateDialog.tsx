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
  applyBookFilesUpdate,
  discardBookFilesUpdate,
  previewBookFilesUpdate,
  type BookFilesUpdateOptions,
  type BookFilesUpdateReport,
} from 'lib/uploads';
import { appConfig } from 'config/environment';

interface BookFilesUpdateDialogProps {
  open: boolean;
  book: BookRecord;
  token: string;
  tokenType: string;
  onClose: () => void;
  onUpdated?: () => void;
}

type Stage = 'select' | 'uploading' | 'preview' | 'applying' | 'done';

const NO_OPTIONS: BookFilesUpdateOptions = {
  prune: false,
  bump_version: false,
  notify: false,
  regenerate_bundles: false,
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

const MetadataDiff = ({ report }: { report: BookFilesUpdateReport }) => {
  const { before, after, changed } = report.metadata;
  if (!changed.length) {
    return <p className="text-sm text-muted-foreground">Book details (activities, cover, size) stay the same.</p>;
  }
  const show = (field: string, value: unknown) =>
    field === 'total_size'
      ? formatBytes(value as number)
      : field === 'activity_details'
        ? Object.entries((value as Record<string, number>) || {})
            .map(([k, v]) => `${k}: ${v}`)
            .join(', ') || '—'
        : String(value ?? '—');
  const labels: Record<string, string> = {
    activity_count: 'Activities',
    activity_details: 'Activity types',
    book_cover: 'Cover',
    total_size: 'Size',
  };
  return (
    <table className="w-full text-sm">
      <tbody>
        {changed.map((field) => (
          <tr key={field} className="align-top">
            <td className="py-1 pr-3 text-muted-foreground whitespace-nowrap">{labels[field] ?? field}</td>
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
 */
const BookFilesUpdateDialog = ({ open, book, token, tokenType, onClose, onUpdated }: BookFilesUpdateDialogProps) => {
  const [stage, setStage] = useState<Stage>('select');
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState(0);
  const [detail, setDetail] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [report, setReport] = useState<BookFilesUpdateReport | null>(null);
  const [options, setOptions] = useState<BookFilesUpdateOptions>(NO_OPTIONS);
  const [confirmed, setConfirmed] = useState(false);
  const abortRef = useRef<(() => void) | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setStage('select');
      setFile(null);
      setProgress(0);
      setDetail('');
      setError(null);
      setJobId(null);
      setReport(null);
      setOptions(NO_OPTIONS);
      setConfirmed(false);
    }
  }, [open]);

  const busy = stage === 'uploading' || stage === 'applying';
  const needsConfirm = !!report?.warnings.some((w) => w.code === 'folder_name_mismatch' || w.code === 'title_mismatch');

  const handleClose = () => {
    if (stage === 'applying') return;
    abortRef.current?.();
    if (stage === 'preview' && jobId) {
      void discardBookFilesUpdate(book.id, jobId, token, tokenType, appConfig.apiBaseUrl);
    }
    onClose();
  };

  const startPreview = async () => {
    if (!file) return;
    setStage('uploading');
    setError(null);
    const { promise, abort } = previewBookFilesUpdate(
      file,
      book.id,
      token,
      tokenType,
      (s) => {
        setProgress(s.progress);
        setDetail(s.detail || s.step);
      },
      appConfig.apiBaseUrl
    );
    abortRef.current = abort;
    try {
      const result = await promise;
      setJobId(result.jobId);
      setReport(result.report);
      setStage('preview');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Upload failed');
      setStage('select');
    } finally {
      abortRef.current = null;
    }
  };

  const apply = async () => {
    if (!jobId) return;
    setStage('applying');
    setError(null);
    setProgress(0);
    try {
      const final = await applyBookFilesUpdate(
        book.id,
        jobId,
        options,
        token,
        tokenType,
        (s) => {
          setProgress(s.progress);
          setDetail(s.detail || s.step);
        },
        appConfig.apiBaseUrl
      );
      setReport(final);
      setStage('done');
      onUpdated?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Update failed');
      setStage('preview');
    }
  };

  const setOption = (key: keyof BookFilesUpdateOptions, value: boolean) =>
    setOptions((prev) => {
      const next = { ...prev, [key]: value };
      // Learn only re-syncs when the version moves, so these go together.
      if (key === 'notify') next.bump_version = value;
      if (key === 'bump_version' && !value) next.notify = false;
      return next;
    });

  return (
    <Dialog open={open} onOpenChange={(o) => !o && handleClose()}>
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

        {busy && (
          <div className="space-y-2 py-2">
            <Progress value={progress} />
            <p className="text-sm text-muted-foreground flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" /> {detail}
            </p>
          </div>
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
                  {report.notified ? ' Learn was notified.' : ''}
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
                title={stage === 'done' && report.pruned.length ? 'Removed' : 'Stored files not in the ZIP (kept unless pruned)'}
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
              <Button variant="outline" onClick={handleClose}>
                Cancel
              </Button>
              <Button onClick={startPreview} disabled={!file}>
                Upload & preview
              </Button>
            </>
          )}
          {stage === 'uploading' && (
            <Button variant="outline" onClick={handleClose}>
              Cancel
            </Button>
          )}
          {stage === 'preview' && (
            <>
              <Button variant="outline" onClick={handleClose}>
                Discard
              </Button>
              <Button onClick={apply} disabled={needsConfirm && !confirmed}>
                Apply update
              </Button>
            </>
          )}
          {stage === 'applying' && (
            <Button disabled>
              <Loader2 className="h-4 w-4 animate-spin" /> Applying...
            </Button>
          )}
          {stage === 'done' && <Button onClick={onClose}>Close</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default BookFilesUpdateDialog;
