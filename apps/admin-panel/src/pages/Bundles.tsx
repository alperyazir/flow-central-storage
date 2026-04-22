import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
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
  ChevronDown,
  ChevronRight,
  Paperclip,
  BookOpen,
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
  const navigate = useNavigate();

  const [bundles, setBundles] = useState<BundleInfo[]>([]);
  const [allBooks, setAllBooks] = useState<BookRecord[]>([]);
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [bundleJobs, setBundleJobs] = useState<BundleJobStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [platformFilter, setPlatformFilter] = useState('all');
  const [publisherFilter, setPublisherFilter] = useState('all');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
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
      const [b, t, j, bk] = await Promise.all([
        listBundles(token, tt),
        listTemplates(token, tt),
        listBundleJobs(token, tt),
        fetchBooks(token, tt, undefined, { topLevelOnly: false }),
      ]);
      setBundles(b.bundles);
      setTemplates(t.templates);
      setBundleJobs(j.jobs);
      setAllBooks(bk);
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

  // Build a name→book index and group bundles by parent book so child
  // books' bundles nest under their parent.
  const { groups, autoExpandKeys } = useMemo(() => {
    const byName = new Map<string, BookRecord>();
    for (const b of allBooks) byName.set(b.book_name, b);

    interface Group {
      key: string; // parent book_name
      parent: BookRecord | null;
      parentBundles: BundleInfo[];
      children: Map<string, { book: BookRecord | null; bundles: BundleInfo[] }>;
    }

    const map = new Map<string, Group>();
    const getGroup = (key: string, parent: BookRecord | null): Group => {
      let g = map.get(key);
      if (!g) {
        g = { key, parent, parentBundles: [], children: new Map() };
        map.set(key, g);
      }
      return g;
    };

    for (const bundle of filtered) {
      const book = byName.get(bundle.book_name) ?? null;
      if (book && book.parent_book_id) {
        const parent = allBooks.find((b) => b.id === book.parent_book_id) ?? null;
        const key = parent?.book_name ?? bundle.book_name;
        const grp = getGroup(key, parent);
        let childEntry = grp.children.get(bundle.book_name);
        if (!childEntry) {
          childEntry = { book, bundles: [] };
          grp.children.set(bundle.book_name, childEntry);
        }
        childEntry.bundles.push(bundle);
      } else {
        const key = bundle.book_name;
        const grp = getGroup(key, book);
        grp.parentBundles.push(bundle);
      }
    }

    // Sort groups alphabetically by key and bundles within by platform
    const sorted = [...map.values()].sort((a, b) => a.key.localeCompare(b.key));
    for (const g of sorted) {
      g.parentBundles.sort((a, b) => a.platform.localeCompare(b.platform));
      for (const c of g.children.values()) {
        c.bundles.sort((a, b) => a.platform.localeCompare(b.platform));
      }
    }

    // Auto-expand when searching or any filter narrows the view
    const shouldAutoExpand = search.length > 0 || platformFilter !== 'all' || publisherFilter !== 'all';
    const keys = shouldAutoExpand ? sorted.map((g) => g.key) : [];
    return { groups: sorted, autoExpandKeys: keys };
  }, [filtered, allBooks, search, platformFilter, publisherFilter]);

  useEffect(() => {
    if (autoExpandKeys.length === 0) return;
    setExpanded((prev) => {
      const next = { ...prev };
      for (const k of autoExpandKeys) next[k] = true;
      return next;
    });
  }, [autoExpandKeys]);

  const isExpanded = (key: string) => expanded[key] ?? false; // default collapsed
  const toggleExpanded = (key: string) =>
    setExpanded((prev) => ({ ...prev, [key]: !isExpanded(key) }));

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
                <TableHead className="w-[36%]">Book / Resource</TableHead>
                <TableHead>Publisher</TableHead>
                <TableHead>Platforms</TableHead>
                <TableHead>Latest</TableHead>
                <TableHead className="text-right w-[80px]"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {!groups.length ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">
                    No bundles match your filters
                  </TableCell>
                </TableRow>
              ) : (
                groups.flatMap((g) => {
                  const rows: JSX.Element[] = [];
                  const childrenArr = [...g.children.values()];
                  const childCount = childrenArr.length;
                  const open = isExpanded(g.key);
                  const canToggle = childCount > 0;

                  const renderBookRow = (
                    key: string,
                    book: BookRecord | null,
                    bundles: BundleInfo[],
                    opts: { isChild?: boolean; isHeader?: boolean } = {}
                  ) => {
                    const latest = bundles.reduce<string | null>((acc, b) => {
                      if (!acc) return b.created_at;
                      return new Date(b.created_at) > new Date(acc) ? b.created_at : acc;
                    }, null);
                    const publisher = bundles[0]?.publisher_name ?? book?.publisher ?? '—';
                    const bookTitle = book?.book_title || book?.book_name || key;
                    const bookTypeBadge = book?.book_type === 'pdf' ? 'PDF' : null;
                    return (
                      <TableRow
                        key={`row-${key}`}
                        className={opts.isHeader ? 'bg-accent/20' : undefined}
                      >
                        <TableCell>
                          <div className="flex items-center gap-2">
                            {opts.isHeader ? (
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-6 w-6"
                                disabled={!canToggle}
                                onClick={() => canToggle && toggleExpanded(g.key)}
                                title={canToggle ? 'Toggle resources' : ''}
                              >
                                {!canToggle ? (
                                  <BookOpen className="h-4 w-4 text-muted-foreground" />
                                ) : open ? (
                                  <ChevronDown className="h-4 w-4" />
                                ) : (
                                  <ChevronRight className="h-4 w-4" />
                                )}
                              </Button>
                            ) : (
                              <span className="pl-8 flex items-center text-muted-foreground">
                                <Paperclip className="h-3 w-3" />
                              </span>
                            )}
                            <button
                              type="button"
                              className="font-medium hover:underline text-left"
                              onClick={() => book && navigate(`/books/${book.id}`)}
                            >
                              {bookTitle}
                            </button>
                            {bookTypeBadge && (
                              <Badge variant="outline" className="text-[10px]">
                                {bookTypeBadge}
                              </Badge>
                            )}
                            {opts.isHeader && childCount > 0 && (
                              <Badge variant="secondary" className="gap-1">
                                <Paperclip className="h-3 w-3" />
                                {childCount}
                              </Badge>
                            )}
                          </div>
                        </TableCell>
                        <TableCell className="text-sm">{publisher}</TableCell>
                        <TableCell>
                          {bundles.length === 0 ? (
                            <span className="text-xs text-muted-foreground">
                              {book?.book_type === 'pdf' ? 'PDF (no bundles)' : 'No bundles'}
                            </span>
                          ) : (
                            <div className="flex flex-wrap gap-1.5">
                              {bundles.map((b) => (
                                <div
                                  key={b.object_name}
                                  title={`${b.file_name} — ${fmtBytes(b.file_size)}`}
                                  className="group/chip flex items-center gap-1 rounded-md border bg-background px-2 py-1 text-xs min-w-[150px] hover:bg-accent/30"
                                >
                                  {b.platform === 'mac' ? (
                                    <Apple className="h-3 w-3" />
                                  ) : (
                                    <Monitor className="h-3 w-3" />
                                  )}
                                  <span className="font-medium">
                                    {PLATFORM_LABELS[b.platform as StandalonePlatform] || b.platform}
                                  </span>
                                  <span className="ml-auto text-muted-foreground tabular-nums">
                                    {fmtBytes(b.file_size)}
                                  </span>
                                  {b.download_url && (
                                    <button
                                      type="button"
                                      className="ml-1 text-muted-foreground hover:text-foreground"
                                      onClick={() => window.open(b.download_url!, '_blank')}
                                      title={`Download ${b.file_name}`}
                                    >
                                      <Download className="h-3 w-3" />
                                    </button>
                                  )}
                                  <button
                                    type="button"
                                    className="opacity-0 group-hover/chip:opacity-100 transition-opacity text-muted-foreground hover:text-destructive"
                                    onClick={() => setDelTarget(b)}
                                    title="Delete bundle"
                                  >
                                    <Trash2 className="h-3 w-3" />
                                  </button>
                                </div>
                              ))}
                            </div>
                          )}
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {latest ? fmtDate(latest) : '—'}
                        </TableCell>
                        <TableCell className="text-right">
                          {opts.isHeader && (
                            <span className="text-xs text-muted-foreground">
                              {g.parentBundles.length + childrenArr.reduce((n, c) => n + c.bundles.length, 0)} total
                            </span>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  };

                  rows.push(
                    renderBookRow(g.key, g.parent, g.parentBundles, { isHeader: true })
                  );

                  if (open) {
                    childrenArr.forEach((c) => {
                      rows.push(
                        renderBookRow(
                          `child-${g.key}-${c.book?.book_name ?? 'x'}`,
                          c.book,
                          c.bundles,
                          { isChild: true }
                        )
                      );
                    });
                  }

                  return rows;
                })
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
          {delTarget && (() => {
            const b = allBooks.find((x) => x.book_name === delTarget.book_name);
            if (!b) return null;
            const parent = b.parent_book_id
              ? allBooks.find((x) => x.id === b.parent_book_id)
              : null;
            return (
              <Alert>
                <AlertDescription className="text-xs">
                  {parent ? (
                    <>
                      Attached to child resource{' '}
                      <strong>{b.book_title || b.book_name}</strong> under{' '}
                      <strong>{parent.book_title || parent.book_name}</strong>.
                      Only this one platform bundle will be removed.
                    </>
                  ) : (
                    <>
                      Belongs to <strong>{b.book_title || b.book_name}</strong>.
                      Only this one platform bundle will be removed.
                    </>
                  )}
                </AlertDescription>
              </Alert>
            );
          })()}
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
