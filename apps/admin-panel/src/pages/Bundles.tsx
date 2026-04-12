import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Loader2,
  Download,
  Trash2,
  Plus,
  Monitor,
  Apple,
  RefreshCw,
  XCircle,
  Eraser,
} from 'lucide-react';

import { Card, CardContent } from 'components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from 'components/ui/table';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from 'components/ui/select';
import { Input } from 'components/ui/input';
import { Checkbox } from 'components/ui/checkbox';
import { Label } from 'components/ui/label';
import { Button } from 'components/ui/button';
import { Badge } from 'components/ui/badge';
import { Progress } from 'components/ui/progress';
import { Alert, AlertDescription } from 'components/ui/alert';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from 'components/ui/dialog';
import { useAuthStore } from 'stores/auth';
import { fetchBooks, type BookRecord } from 'lib/books';
import {
  listBundles,
  listTemplates,
  listBundleJobs,
  createBundleAsync,
  getBundleJobStatus,
  deleteBundle,
  cancelBundleJob,
  deleteBundleJob,
  clearBundleJobs,
  PLATFORM_LABELS,
  type StandalonePlatform,
  type BundleInfo,
  type TemplateInfo,
  type BundleJobResult,
  type BundleJobStatus,
} from 'lib/standaloneApps';

const fmtBytes = (n: number) => {
  if (!n) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB'];
  const e = Math.min(Math.floor(Math.log(n) / Math.log(1024)), u.length - 1);
  const v = n / Math.pow(1024, e);
  return `${v.toFixed(v >= 10 || e === 0 ? 0 : 1)} ${u[e]}`;
};
const fmtDate = (s: string) =>
  new Date(s).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });

const BundlesPage = () => {
  const { token, tokenType } = useAuthStore();
  const tt = tokenType ?? 'Bearer';

  const [bundles, setBundles] = useState<BundleInfo[]>([]);
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [bundleJobs, setBundleJobs] = useState<BundleJobStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [platformFilter, setPlatformFilter] = useState('all');
  const [publisherFilter, setPublisherFilter] = useState('all');
  const [createOpen, setCreateOpen] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [platform, setPlatform] = useState('');
  const [books, setBooks] = useState<BookRecord[]>([]);
  const [selectedBookId, setSelectedBookId] = useState('');
  const [force, setForce] = useState(false);
  const [creating, setCreating] = useState(false);
  const [jobProgress, setJobProgress] = useState(0);
  const [jobStep, setJobStep] = useState('');
  const [jobResult, setJobResult] = useState<BundleJobResult | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [delTarget, setDelTarget] = useState<BundleInfo | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [b, t, j] = await Promise.all([
        listBundles(token, tt),
        listTemplates(token, tt),
        listBundleJobs(token, tt),
      ]);
      setBundles(b.bundles);
      setTemplates(t.templates);
      setBundleJobs(j.jobs);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  };

  const fetchJobs = useCallback(async () => {
    if (!token) return;
    try {
      const j = await listBundleJobs(token, tt);
      setBundleJobs(j.jobs);
      // If all active jobs finished, reload bundles list too
      const hasActive = j.jobs.some(
        (job) => job.status === 'queued' || job.status === 'processing'
      );
      if (!hasActive && pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
        // Refresh bundles since new ones may have been created
        const b = await listBundles(token, tt);
        setBundles(b.bundles);
      }
    } catch {
      /* ignore polling errors */
    }
  }, [token, tt]);

  useEffect(() => {
    load();
  }, [token]);

  // Auto-poll when there are active jobs
  useEffect(() => {
    const hasActive = bundleJobs.some(
      (j) => j.status === 'queued' || j.status === 'processing'
    );
    if (hasActive && !pollRef.current) {
      pollRef.current = setInterval(fetchJobs, 5000);
    }
    if (!hasActive && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [bundleJobs, fetchJobs]);

  const platforms = useMemo(
    () => [...new Set(bundles.map((b) => b.platform))].sort(),
    [bundles]
  );
  const publishers = useMemo(
    () => [...new Set(bundles.map((b) => b.publisher_name))].sort(),
    [bundles]
  );

  const filtered = useMemo(() => {
    let d = bundles;
    if (search) {
      const q = search.toLowerCase();
      d = d.filter(
        (b) =>
          b.book_name.toLowerCase().includes(q) ||
          b.file_name.toLowerCase().includes(q) ||
          b.publisher_name.toLowerCase().includes(q)
      );
    }
    if (platformFilter !== 'all')
      d = d.filter((b) => b.platform === platformFilter);
    if (publisherFilter !== 'all')
      d = d.filter((b) => b.publisher_name === publisherFilter);
    return d;
  }, [bundles, search, platformFilter, publisherFilter]);

  const openCreate = async () => {
    setPlatform('');
    setSelectedBookId('');
    setForce(false);
    setJobResult(null);
    setJobError(null);
    setJobProgress(0);
    setJobStep('');
    setCreateOpen(true);
    if (token) {
      try {
        const bks = await fetchBooks(token, tt);
        setBooks(
          bks.filter((b) => b.status === 'published' || b.status === 'active')
        );
      } catch {
        /* ignored */
      }
    }
  };

  const handleCreate = async () => {
    if (!token || !platform || !selectedBookId) return;
    setCreating(true);
    setJobError(null);
    setJobResult(null);
    try {
      const { job_id } = await createBundleAsync(
        {
          platform: platform as 'mac' | 'win' | 'win7-8' | 'linux',
          book_id: Number(selectedBookId),
          force,
        },
        token,
        tt
      );
      let polls = 0;
      const poll = async (): Promise<void> => {
        if (polls++ > 600) {
          setJobError('Timeout');
          setCreating(false);
          return;
        }
        const r = await getBundleJobStatus(job_id, token, tt);
        setJobProgress(r.progress);
        setJobStep(r.current_step);
        if (r.status === 'completed') {
          setJobResult(r);
          setCreating(false);
          load();
          return;
        }
        if (r.status === 'failed') {
          setJobError(r.error_message || 'Build failed');
          setCreating(false);
          return;
        }
        await new Promise((res) => setTimeout(res, 1000));
        return poll();
      };
      await poll();
    } catch (e) {
      setJobError(e instanceof Error ? e.message : 'Failed');
      setCreating(false);
    }
  };

  const handleDelete = async () => {
    if (!delTarget || !token) return;
    setDeleting(true);
    try {
      await deleteBundle(delTarget.object_name, token, tt);
      setDelTarget(null);
      load();
    } catch {
      /* ignored */
    } finally {
      setDeleting(false);
    }
  };

  if (loading)
    return (
      <div className="flex justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Bundles</h1>
        <div className="flex gap-2">
          <Button
            variant="outline"
            onClick={() => {
              setLoading(true);
              load().finally(() => setLoading(false));
            }}
          >
            <RefreshCw className="h-4 w-4" /> Refresh
          </Button>
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4" /> Create Bundle
          </Button>
        </div>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {bundleJobs.length > 0 && (
        <Card>
          <CardContent className="p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-sm">
                Bundle Queue (
                {bundleJobs.filter((j) => j.status === 'queued').length} queued,{' '}
                {bundleJobs.filter((j) => j.status === 'processing').length}{' '}
                processing,{' '}
                {bundleJobs.filter((j) => j.status === 'failed').length} failed,{' '}
                {bundleJobs.filter((j) => j.status === 'completed').length}{' '}
                completed)
              </h3>
              <Button
                variant="outline"
                size="sm"
                onClick={async () => {
                  if (!token) return;
                  await clearBundleJobs(token, tt);
                  fetchJobs();
                }}
              >
                <Eraser className="h-3 w-3" /> Clear All
              </Button>
            </div>
            {bundleJobs.map((j) => (
              <div key={j.job_id} className="flex items-center gap-3 text-sm">
                <Badge
                  variant={
                    j.status === 'completed'
                      ? 'success'
                      : j.status === 'failed'
                        ? 'destructive'
                        : j.status === 'cancelled'
                          ? 'secondary'
                          : 'default'
                  }
                  className="w-20 justify-center"
                >
                  {j.status}
                </Badge>
                <span className="flex-1 truncate font-medium">
                  {j.book_name || j.book_id || j.job_id}
                </span>
                {j.platform && (
                  <div className="flex items-center gap-1">
                    {j.platform === 'mac' ? (
                      <Apple className="h-3 w-3" />
                    ) : (
                      <Monitor className="h-3 w-3" />
                    )}
                    <span className="text-xs">
                      {PLATFORM_LABELS[j.platform as StandalonePlatform] ||
                        j.platform}
                    </span>
                  </div>
                )}
                <div className="w-24">
                  <Progress value={j.progress} />
                </div>
                <span className="text-xs text-muted-foreground w-28 truncate">
                  {j.status === 'failed'
                    ? j.error_message || 'Failed'
                    : `${j.progress}% — ${j.current_step}`}
                </span>
                <div className="flex gap-1">
                  {(j.status === 'queued' || j.status === 'processing') && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      title="Cancel"
                      onClick={async () => {
                        if (!token) return;
                        await cancelBundleJob(j.job_id, token, tt);
                        fetchJobs();
                      }}
                    >
                      <XCircle className="h-3 w-3 text-destructive" />
                    </Button>
                  )}
                  {(j.status === 'failed' ||
                    j.status === 'completed' ||
                    j.status === 'cancelled') && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      title="Remove"
                      onClick={async () => {
                        if (!token) return;
                        await deleteBundleJob(j.job_id, token, tt);
                        fetchJobs();
                      }}
                    >
                      <Trash2 className="h-3 w-3 text-muted-foreground" />
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder="Search book, file, publisher..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
        <Select value={platformFilter} onValueChange={setPlatformFilter}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="All Platforms" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Platforms</SelectItem>
            {platforms.map((p) => (
              <SelectItem key={p} value={p}>
                {PLATFORM_LABELS[p as StandalonePlatform] || p}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={publisherFilter} onValueChange={setPublisherFilter}>
          <SelectTrigger className="w-44">
            <SelectValue placeholder="All Publishers" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Publishers</SelectItem>
            {publishers.map((p) => (
              <SelectItem key={p} value={p}>
                {p}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Book</TableHead>
                <TableHead>Publisher</TableHead>
                <TableHead>Platform</TableHead>
                <TableHead>Size</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {!filtered.length ? (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="text-center py-8 text-muted-foreground"
                  >
                    No bundles created
                  </TableCell>
                </TableRow>
              ) : (
                filtered.map((b) => (
                  <TableRow key={b.object_name}>
                    <TableCell>
                      <div>
                        <span className="font-medium">{b.book_name}</span>
                        <span className="block text-xs text-muted-foreground">
                          {b.file_name}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>{b.publisher_name}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        {b.platform === 'mac' ? (
                          <Apple className="h-4 w-4" />
                        ) : (
                          <Monitor className="h-4 w-4" />
                        )}
                        <Badge variant="outline">
                          {PLATFORM_LABELS[b.platform as StandalonePlatform] ||
                            b.platform}
                        </Badge>
                      </div>
                    </TableCell>
                    <TableCell>{fmtBytes(b.file_size)}</TableCell>
                    <TableCell>{fmtDate(b.created_at)}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        {b.download_url && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            onClick={() =>
                              window.open(b.download_url!, '_blank')
                            }
                          >
                            <Download className="h-4 w-4" />
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => setDelTarget(b)}
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog
        open={createOpen}
        onOpenChange={(o) => !creating && !o && setCreateOpen(false)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Create Bundle</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Platform</Label>
              <Select
                value={platform}
                onValueChange={setPlatform}
                disabled={creating}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select..." />
                </SelectTrigger>
                <SelectContent>
                  {templates.map((t) => (
                    <SelectItem key={t.platform} value={t.platform}>
                      {PLATFORM_LABELS[t.platform as StandalonePlatform] ||
                        t.platform}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Book</Label>
              <Select
                value={selectedBookId}
                onValueChange={setSelectedBookId}
                disabled={creating}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select book..." />
                </SelectTrigger>
                <SelectContent>
                  {books.map((b) => (
                    <SelectItem key={b.id} value={String(b.id)}>
                      {b.book_title || b.book_name} ({b.publisher})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-center space-x-2">
              <Checkbox
                id="force"
                checked={force}
                onCheckedChange={(c) => setForce(c === true)}
                disabled={creating}
              />
              <Label htmlFor="force" className="text-sm font-normal">
                Force recreate (bypass cache)
              </Label>
            </div>
            {creating && (
              <div className="space-y-1">
                <Progress value={jobProgress} />
                <p className="text-xs text-muted-foreground">
                  {jobProgress}% — {jobStep}
                </p>
              </div>
            )}
            {jobResult?.download_url && (
              <Alert>
                <AlertDescription>
                  Bundle ready!{' '}
                  <a
                    href={jobResult.download_url}
                    target="_blank"
                    rel="noreferrer"
                    className="underline text-primary"
                  >
                    Download
                  </a>
                </AlertDescription>
              </Alert>
            )}
            {jobError && (
              <Alert variant="destructive">
                <AlertDescription>{jobError}</AlertDescription>
              </Alert>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setCreateOpen(false)}
              disabled={creating}
            >
              Close
            </Button>
            <Button
              onClick={handleCreate}
              disabled={creating || !platform || !selectedBookId}
            >
              {creating ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Plus className="h-4 w-4" />
              )}{' '}
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!delTarget}
        onOpenChange={() => !deleting && setDelTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Bundle?</DialogTitle>
            <DialogDescription>
              Delete bundle &quot;{delTarget?.file_name}&quot;? This cannot be
              undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDelTarget(null)}
              disabled={deleting}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={deleting}
            >
              {deleting ? 'Deleting...' : 'Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default BundlesPage;
