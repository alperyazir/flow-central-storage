import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Loader2,
  Play,
  RefreshCw,
  Settings,
  XCircle,
  Database,
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
import { Checkbox } from 'components/ui/checkbox';
import { Input } from 'components/ui/input';
import { Button } from 'components/ui/button';
import { Badge } from 'components/ui/badge';
import { Progress } from 'components/ui/progress';
import { Alert, AlertDescription } from 'components/ui/alert';
import { useNavigate } from 'react-router-dom';
import ProcessingDialog from 'components/ProcessingDialog';
import ProcessingSettingsDialog from 'components/ProcessingSettingsDialog';
import { useAuthStore } from 'stores/auth';
import {
  getBooksWithProcessingStatus,
  getProcessingQueue,
  bulkReprocess,
  clearProcessingError,
  getExtendedStatusLabel,
  type BookWithProcessingStatus,
  type ProcessingQueueItem,
  type ExtendedProcessingStatus,
} from 'lib/processing';

const statusVariant = (s: string) => {
  if (s === 'completed') return 'success' as const;
  if (s === 'processing' || s === 'queued') return 'default' as const;
  if (s === 'failed') return 'destructive' as const;
  if (s === 'partial') return 'warning' as const;
  return 'secondary' as const;
};

const ProcessingPage = () => {
  const { token, tokenType } = useAuthStore();
  const tt = tokenType ?? 'Bearer';
  const navigate = useNavigate();

  const [books, setBooks] = useState<BookWithProcessingStatus[]>([]);
  const [queue, setQueue] = useState<ProcessingQueueItem[]>([]);
  const [queueStats, setQueueStats] = useState({ queued: 0, processing: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [processingBook, setProcessingBook] =
    useState<BookWithProcessingStatus | null>(null);
  const [bulkProcessing, setBulkProcessing] = useState(false);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const fetchData = useCallback(async () => {
    if (!token) return;
    try {
      const [bks, q] = await Promise.allSettled([
        getBooksWithProcessingStatus(
          token,
          tt,
          statusFilter !== 'all'
            ? { status: statusFilter as ExtendedProcessingStatus }
            : {}
        ),
        getProcessingQueue(token, tt),
      ]);
      if (bks.status === 'fulfilled') {
        setBooks(bks.value.books ?? []);
      }
      if (q.status === 'fulfilled') {
        setQueue((q.value.queue ?? []).slice(0, 5));
        setQueueStats({
          queued: q.value.total_queued ?? 0,
          processing: q.value.total_processing ?? 0,
        });
      }
      if (bks.status === 'rejected' && q.status === 'rejected') {
        setError('Failed to load processing data');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    }
  }, [token, tt, statusFilter]);

  useEffect(() => {
    setLoading(true);
    fetchData().finally(() => setLoading(false));
  }, [fetchData]);

  useEffect(() => {
    if (queueStats.queued + queueStats.processing === 0) return;
    const id = setInterval(fetchData, 10000);
    return () => clearInterval(id);
  }, [queueStats.queued, queueStats.processing, fetchData]);

  const filtered = useMemo(() => {
    if (!search) return books;
    const q = search.toLowerCase();
    return books.filter(
      (b) =>
        b.book_title.toLowerCase().includes(q) ||
        b.book_name.toLowerCase().includes(q) ||
        b.publisher_name.toLowerCase().includes(q)
    );
  }, [books, search]);

  const toggleSelect = (id: number) =>
    setSelected((p) => {
      const n = new Set(p);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  const toggleAll = () =>
    setSelected((p) =>
      p.size === filtered.length
        ? new Set()
        : new Set(filtered.map((b) => b.book_id))
    );

  const handleBulk = async () => {
    if (!token || !selected.size) return;
    setBulkProcessing(true);
    setSuccessMsg(null);
    try {
      const r = await bulkReprocess({ book_ids: [...selected] }, token, tt);
      setSuccessMsg(`Triggered: ${r.triggered}, Skipped: ${r.skipped}`);
      setSelected(new Set());
      fetchData();
    } catch {
      /* ignored */
    } finally {
      setBulkProcessing(false);
    }
  };

  const handleClearError = async (bookId: number) => {
    if (!token) return;
    try {
      await clearProcessingError(bookId, token, tt);
      fetchData();
    } catch {
      /* ignored */
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
        <h1 className="text-2xl font-semibold">AI Processing</h1>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setSettingsOpen(true)}>
            <Settings className="h-4 w-4" /> Settings
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              setLoading(true);
              fetchData().finally(() => setLoading(false));
            }}
          >
            <RefreshCw className="h-4 w-4" /> Refresh
          </Button>
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {successMsg && (
        <Alert>
          <AlertDescription>{successMsg}</AlertDescription>
        </Alert>
      )}

      {queue.length > 0 && (
        <Card>
          <CardContent className="p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-sm">
                Queue ({queueStats.queued} queued, {queueStats.processing}{' '}
                processing)
              </h3>
            </div>
            {queue.map((q) => (
              <div key={q.job_id} className="flex items-center gap-3 text-sm">
                <Badge
                  variant={statusVariant(q.status)}
                  className="w-20 justify-center"
                >
                  {q.status}
                </Badge>
                <span className="flex-1 truncate font-medium">
                  {q.book_title}
                </span>
                <div className="w-24">
                  <Progress value={q.progress} />
                </div>
                <span className="text-xs text-muted-foreground w-24 truncate">
                  {q.current_step}
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder="Search..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="All Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Status</SelectItem>
            <SelectItem value="not_started">Not Started</SelectItem>
            <SelectItem value="queued">Queued</SelectItem>
            <SelectItem value="processing">Processing</SelectItem>
            <SelectItem value="completed">Completed</SelectItem>
            <SelectItem value="failed">Failed</SelectItem>
            <SelectItem value="partial">Partial</SelectItem>
          </SelectContent>
        </Select>
        {selected.size > 0 && (
          <Button onClick={handleBulk} disabled={bulkProcessing} size="sm">
            {bulkProcessing ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            Reprocess {selected.size} Selected
          </Button>
        )}
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-10">
                  <Checkbox
                    checked={
                      selected.size === filtered.length && filtered.length > 0
                    }
                    onCheckedChange={toggleAll}
                  />
                </TableHead>
                <TableHead>Book</TableHead>
                <TableHead>Publisher</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Progress</TableHead>
                <TableHead>Step</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {!filtered.length ? (
                <TableRow>
                  <TableCell
                    colSpan={7}
                    className="text-center py-8 text-muted-foreground"
                  >
                    No books found
                  </TableCell>
                </TableRow>
              ) : (
                filtered.map((b) => {
                  const hasAIData =
                    b.processing_status === 'completed' ||
                    b.processing_status === 'partial';
                  const openAIData = () =>
                    navigate(`/processing/${b.book_id}/ai-data`, {
                      state: { bookTitle: b.book_title },
                    });
                  return (
                  <TableRow
                    key={b.book_id}
                    className={hasAIData ? 'cursor-pointer' : undefined}
                    onClick={hasAIData ? openAIData : undefined}
                  >
                    <TableCell onClick={(e) => e.stopPropagation()}>
                      <Checkbox
                        checked={selected.has(b.book_id)}
                        onCheckedChange={() => toggleSelect(b.book_id)}
                      />
                    </TableCell>
                    <TableCell>
                      <div>
                        <span className="font-medium">{b.book_title}</span>
                        <span className="block text-xs text-muted-foreground">
                          {b.book_name}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>{b.publisher_name}</TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(b.processing_status)}>
                        {getExtendedStatusLabel(b.processing_status)}
                      </Badge>
                    </TableCell>
                    <TableCell className="w-24">
                      {b.processing_status === 'processing' ? (
                        <Progress value={b.progress} />
                      ) : b.processing_status === 'completed' ? (
                        '100%'
                      ) : (
                        '—'
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground max-w-[120px] truncate">
                      {b.current_step || '—'}
                    </TableCell>
                    <TableCell
                      className="text-right"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <div className="flex justify-end gap-1">
                        {(b.processing_status === 'completed' ||
                          b.processing_status === 'partial') && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            onClick={() =>
                              navigate(`/processing/${b.book_id}/ai-data`, {
                                state: { bookTitle: b.book_title },
                              })
                            }
                            title="View AI Data"
                          >
                            <Database className="h-4 w-4 text-primary" />
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => setProcessingBook(b)}
                          title="Process"
                        >
                          <Play className="h-4 w-4" />
                        </Button>
                        {b.processing_status === 'failed' && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            onClick={() => handleClearError(b.book_id)}
                            title="Clear Error"
                          >
                            <XCircle className="h-4 w-4 text-destructive" />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {processingBook && (
        <ProcessingDialog
          open={!!processingBook}
          onClose={() => {
            setProcessingBook(null);
            fetchData();
          }}
          bookId={processingBook.book_id}
          bookTitle={processingBook.book_title}
          token={token}
          tokenType={tt}
        />
      )}
      <ProcessingSettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        token={token}
        tokenType={tt}
      />
    </div>
  );
};

export default ProcessingPage;
