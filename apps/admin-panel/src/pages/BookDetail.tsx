import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  BookOpen,
  Download,
  ExternalLink,
  FileText,
  Loader2,
  Paperclip,
  Plus,
  Trash2,
} from 'lucide-react';

import { Button } from 'components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from 'components/ui/card';
import { Badge } from 'components/ui/badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from 'components/ui/table';
import { Alert, AlertDescription } from 'components/ui/alert';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from 'components/ui/dialog';
import { Checkbox } from 'components/ui/checkbox';
import { Label } from 'components/ui/label';
import { Progress } from 'components/ui/progress';

import {
  deleteBook,
  fetchBook,
  fetchBooks,
  getDeleteStatus,
  getPdfDownloadUrl,
  type BookRecord,
} from 'lib/books';
import { useAuthStore } from 'stores/auth';
import ChildBookUploadDialog from 'components/ChildBookUploadDialog';

const formatBytes = (bytes: number | undefined): string => {
  if (!bytes) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
};

const BookDetailPage = () => {
  const { id } = useParams<{ id: string }>();
  const bookId = id ? Number(id) : NaN;
  const navigate = useNavigate();
  const { token, tokenType } = useAuthStore();
  const tt = tokenType ?? 'Bearer';

  const [book, setBook] = useState<BookRecord | null>(null);
  const [parent, setParent] = useState<BookRecord | null>(null);
  const [children, setChildren] = useState<BookRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [pdfPending, setPdfPending] = useState<number | null>(null);

  const [delTarget, setDelTarget] = useState<BookRecord | null>(null);
  const [delBundles, setDelBundles] = useState(true);
  const [deleting, setDeleting] = useState(false);
  const [delProgress, setDelProgress] = useState(0);
  const [delStep, setDelStep] = useState('');
  const [delError, setDelError] = useState<string | null>(null);
  const delPollRef = useRef<ReturnType<typeof setInterval>>();

  const load = useCallback(async () => {
    if (!token || Number.isNaN(bookId)) return;
    setLoading(true);
    setError(null);
    try {
      const record = await fetchBook(bookId, token, tt);
      setBook(record);
      if (record.parent_book_id) {
        try {
          const p = await fetchBook(record.parent_book_id, token, tt);
          setParent(p);
        } catch {
          setParent(null);
        }
      } else {
        setParent(null);
      }
      const kids = await fetchBooks(token, tt, undefined, { parentBookId: record.id });
      setChildren(kids);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Failed to load book');
    } finally {
      setLoading(false);
    }
  }, [token, tt, bookId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    return () => {
      if (delPollRef.current) clearInterval(delPollRef.current);
    };
  }, []);

  const openPdf = async (child: BookRecord) => {
    if (!token) return;
    setPdfPending(child.id);
    try {
      const res = await getPdfDownloadUrl(child.id, token, tt);
      window.open(res.download_url, '_blank', 'noopener');
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : 'Could not open PDF');
    } finally {
      setPdfPending(null);
    }
  };

  const handleDelete = async () => {
    if (!token || !delTarget) return;
    setDeleting(true);
    setDelError(null);
    setDelProgress(0);
    setDelStep('Starting...');
    try {
      const { job_id } = await deleteBook(delTarget.id, token, tt, delBundles);
      delPollRef.current = setInterval(async () => {
        try {
          const s = await getDeleteStatus(job_id, token, tt);
          setDelProgress(s.progress);
          setDelStep(s.detail);
          if (s.step === 'completed') {
            clearInterval(delPollRef.current);
            setDeleting(false);
            const target = delTarget;
            setDelTarget(null);
            if (target.id === book?.id) {
              navigate(book?.parent_book_id ? `/books/${book.parent_book_id}` : '/books');
            } else {
              load();
            }
          } else if (s.step === 'error') {
            clearInterval(delPollRef.current);
            setDeleting(false);
            setDelError(s.error || 'Delete failed');
          }
        } catch {
          clearInterval(delPollRef.current);
          setDeleting(false);
          setDelError('Failed to get status');
        }
      }, 1000);
    } catch (exc) {
      setDeleting(false);
      setDelError(exc instanceof Error ? exc.message : 'Delete failed');
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    );
  }

  if (error || !book) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" onClick={() => navigate(-1)}>
          <ArrowLeft className="h-4 w-4" /> Back
        </Button>
        <Alert variant="destructive">
          <AlertDescription>{error || 'Book not found'}</AlertDescription>
        </Alert>
      </div>
    );
  }

  const typeIcon = book.book_type === 'pdf' ? <FileText className="h-4 w-4" /> : <BookOpen className="h-4 w-4" />;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <Button variant="ghost" size="sm" onClick={() => navigate(-1)} className="h-7 px-2">
            <ArrowLeft className="h-4 w-4" /> Back
          </Button>
          {parent && (
            <div className="text-xs text-muted-foreground">
              Child of{' '}
              <Link to={`/books/${parent.id}`} className="underline">
                {parent.book_title || parent.book_name}
              </Link>
            </div>
          )}
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-semibold">{book.book_title || book.book_name}</h1>
            <Badge variant="outline" className="gap-1">
              {typeIcon}
              {book.book_type.toUpperCase()}
            </Badge>
            {book.child_count !== undefined && book.child_count > 0 && (
              <Badge variant="secondary" className="gap-1">
                <Paperclip className="h-3 w-3" /> {book.child_count}
              </Badge>
            )}
          </div>
          <p className="text-sm text-muted-foreground">
            {book.publisher} · {book.language.toUpperCase()}
            {book.category ? ` · ${book.category}` : ''}
          </p>
        </div>
        <div className="flex gap-2">
          {book.book_type === 'pdf' && (
            <Button variant="outline" onClick={() => openPdf(book)}>
              <Download className="h-4 w-4" /> Open PDF
            </Button>
          )}
          <Button
            variant="destructive"
            onClick={() => {
              setDelTarget(book);
              setDelBundles(true);
              setDelError(null);
            }}
          >
            <Trash2 className="h-4 w-4" /> Delete
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Overview</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-3 text-sm">
          <div>
            <div className="text-muted-foreground text-xs">Storage name</div>
            <div className="font-mono">{book.book_name}</div>
          </div>
          <div>
            <div className="text-muted-foreground text-xs">Activities</div>
            <div>{book.activity_count ?? 0}</div>
          </div>
          <div>
            <div className="text-muted-foreground text-xs">Total size</div>
            <div>{formatBytes(book.total_size)}</div>
          </div>
        </CardContent>
      </Card>

      {book.parent_book_id === null || book.parent_book_id === undefined ? (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">Child Books</CardTitle>
            <Button size="sm" onClick={() => setUploadOpen(true)}>
              <Plus className="h-4 w-4" /> Add Resource
            </Button>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Title</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Size</TableHead>
                  <TableHead>Activities</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {children.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="text-center py-8 text-muted-foreground">
                      No child books yet. Click <em>Add Resource</em> to attach one.
                    </TableCell>
                  </TableRow>
                ) : (
                  children.map((c) => (
                    <TableRow
                      key={c.id}
                      className="cursor-pointer hover:bg-accent/40"
                      onClick={() => navigate(`/books/${c.id}`)}
                    >
                      <TableCell>
                        <div className="font-medium">{c.book_title || c.book_name}</div>
                        <div className="text-xs text-muted-foreground">{c.book_name}</div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="gap-1">
                          {c.book_type === 'pdf' ? (
                            <FileText className="h-3 w-3" />
                          ) : (
                            <BookOpen className="h-3 w-3" />
                          )}
                          {c.book_type.toUpperCase()}
                        </Badge>
                      </TableCell>
                      <TableCell>{formatBytes(c.total_size)}</TableCell>
                      <TableCell>{c.book_type === 'pdf' ? '—' : c.activity_count ?? 0}</TableCell>
                      <TableCell
                        className="text-right space-x-1"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {c.book_type === 'pdf' && (
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            onClick={() => openPdf(c)}
                            disabled={pdfPending === c.id}
                            title="Open PDF"
                          >
                            {pdfPending === c.id ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <ExternalLink className="h-4 w-4" />
                            )}
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => {
                            setDelTarget(c);
                            setDelBundles(c.book_type === 'standard');
                            setDelError(null);
                          }}
                          title="Delete"
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ) : null}

      <ChildBookUploadDialog
        open={uploadOpen}
        parent={book}
        token={token || ''}
        tokenType={tt}
        onClose={() => setUploadOpen(false)}
        onCreated={load}
      />

      <Dialog
        open={!!delTarget}
        onOpenChange={(o) => !deleting && !o && setDelTarget(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete Book</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete &quot;{delTarget?.book_title || delTarget?.book_name}
              &quot;? This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            {delTarget && (delTarget.child_count ?? 0) > 0 && (
              <Alert>
                <AlertDescription>
                  <div className="text-sm">
                    <strong>{delTarget.child_count}</strong> additional
                    resource{delTarget.child_count === 1 ? '' : 's'} will be
                    deleted with this book:
                  </div>
                  {delTarget.id === book?.id && children.length > 0 ? (
                    <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs">
                      {children.map((c) => (
                        <li key={c.id}>
                          {c.book_title || c.book_name}
                          <span className="ml-1 text-muted-foreground">
                            ({c.book_type === 'pdf' ? 'PDF' : 'Flowbook'})
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </AlertDescription>
              </Alert>
            )}
            <div className="flex items-center space-x-2">
              <Checkbox
                id="del-bundles-detail"
                checked={delBundles}
                onCheckedChange={(c) => setDelBundles(c === true)}
                disabled={deleting || delTarget?.book_type === 'pdf'}
              />
              <Label htmlFor="del-bundles-detail" className="text-sm font-normal">
                Also delete bundles (standalone app builds)
              </Label>
            </div>
            {deleting && (
              <div className="space-y-1">
                <Progress value={delProgress} />
                <p className="text-xs text-muted-foreground">{delStep}</p>
              </div>
            )}
            {delError && (
              <Alert variant="destructive">
                <AlertDescription>{delError}</AlertDescription>
              </Alert>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDelTarget(null)} disabled={deleting}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
              {deleting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="h-4 w-4" />
              )}{' '}
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default BookDetailPage;
