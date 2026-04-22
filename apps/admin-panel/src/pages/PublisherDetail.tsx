import { useCallback, useEffect, useRef, useState, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft,
  Loader2,
  Pencil,
  ChevronRight,
  FolderOpen,
  Trash2,
  Upload,
  Paperclip,
} from 'lucide-react';

import { Card, CardContent, CardHeader, CardTitle } from 'components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from 'components/ui/table';
import { Input } from 'components/ui/input';
import { Button } from 'components/ui/button';
import { Badge } from 'components/ui/badge';
import { Checkbox } from 'components/ui/checkbox';
import { Label } from 'components/ui/label';
import { Alert, AlertDescription } from 'components/ui/alert';
import AuthenticatedImage from 'components/AuthenticatedImage';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from 'components/ui/dialog';
import PublisherFormDialog from 'components/PublisherFormDialog';
import PublisherUploadDialog from 'components/PublisherUploadDialog';
import { useAuthStore } from 'stores/auth';
import { useOperationsStore } from 'stores/operations';
import { deleteBook, fetchBooks, getDeleteStatus, type BookRecord } from 'lib/books';
import {
  fetchPublisher,
  fetchPublisherBooks,
  fetchPublisherAssets,
  fetchPublisherAssetFiles,
  type Publisher,
  type PublisherBook,
  type AssetTypeInfo,
  type AssetFileInfo,
} from 'lib/publishers';

const fmtBytes = (n?: number) => {
  if (!n) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB'];
  const e = Math.min(Math.floor(Math.log(n) / Math.log(1024)), u.length - 1);
  const v = n / Math.pow(1024, e);
  return `${v.toFixed(v >= 10 || e === 0 ? 0 : 1)} ${u[e]}`;
};

const PublisherDetailPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { token, tokenType } = useAuthStore();
  const tt = tokenType ?? 'Bearer';

  const [publisher, setPublisher] = useState<Publisher | null>(null);
  const [books, setBooks] = useState<PublisherBook[]>([]);
  const [assets, setAssets] = useState<AssetTypeInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [formOpen, setFormOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [bookSearch, setBookSearch] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<PublisherBook | null>(null);
  const [delBundles, setDelBundles] = useState(true);
  const [deleteChildren, setDeleteChildren] = useState<BookRecord[] | null>(null);

  useEffect(() => {
    if (!deleteTarget || !token || !(deleteTarget.child_count ?? 0)) {
      setDeleteChildren(null);
      return;
    }
    let cancelled = false;
    fetchBooks(token, tt, undefined, { parentBookId: deleteTarget.id })
      .then((kids) => {
        if (!cancelled) setDeleteChildren(kids);
      })
      .catch(() => {
        if (!cancelled) setDeleteChildren([]);
      });
    return () => {
      cancelled = true;
    };
  }, [deleteTarget, token, tt]);
  const [expandedAssets, setExpandedAssets] = useState<Set<string>>(new Set());
  const [assetFiles, setAssetFiles] = useState<Record<string, AssetFileInfo[]>>(
    {}
  );

  const load = async () => {
    if (!token || !id) return;
    setLoading(true);
    setError('');
    try {
      const [pub, bks, ast] = await Promise.all([
        fetchPublisher(Number(id), token, tt),
        fetchPublisherBooks(Number(id), token, tt),
        fetchPublisherAssets(Number(id), token, tt).catch(() => ({
          asset_types: [] as AssetTypeInfo[],
        })),
      ]);
      setPublisher(pub);
      setBooks(bks);
      setAssets(ast.asset_types);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [token, id]);

  const filteredBooks = useMemo(() => {
    if (!bookSearch) return books;
    const q = bookSearch.toLowerCase();
    return books.filter(
      (b) =>
        (b.book_title || b.book_name).toLowerCase().includes(q) ||
        b.book_name.toLowerCase().includes(q)
    );
  }, [books, bookSearch]);

  const toggleAsset = async (name: string) => {
    const next = new Set(expandedAssets);
    if (next.has(name)) {
      next.delete(name);
    } else {
      next.add(name);
      if (!assetFiles[name] && token && id) {
        try {
          const files = await fetchPublisherAssetFiles(
            Number(id),
            name,
            token,
            tt
          );
          setAssetFiles((p) => ({ ...p, [name]: files }));
        } catch {
          /* ignored */
        }
      }
    }
    setExpandedAssets(next);
  };

  const { addOperation, updateOperation } = useOperationsStore();
  const pollRef = useRef<ReturnType<typeof setInterval>>();

  const handleDeleteBook = useCallback(async () => {
    if (!deleteTarget || !token) return;
    const bookName = deleteTarget.book_title || deleteTarget.book_name;
    const shouldDeleteBundles = delBundles;
    setDeleteTarget(null);

    try {
      const res = await deleteBook(deleteTarget.id, token, tt, shouldDeleteBundles);
      const jobId = res.job_id;
      addOperation({ id: jobId, type: 'delete', bookName });
      updateOperation(jobId, { status: 'in_progress', progress: 5 });

      // Poll for progress
      pollRef.current = setInterval(async () => {
        try {
          const status = await getDeleteStatus(jobId, token, tt);
          if (status.error) {
            updateOperation(jobId, { status: 'failed', error: status.error, progress: 0 });
            clearInterval(pollRef.current);
            return;
          }
          updateOperation(jobId, {
            status: status.progress >= 100 ? 'completed' : 'in_progress',
            progress: status.progress,
            detail: status.detail,
          });
          if (status.progress >= 100) {
            clearInterval(pollRef.current);
            load();
          }
        } catch {
          updateOperation(jobId, { status: 'failed', error: 'Lost connection' });
          clearInterval(pollRef.current);
        }
      }, 1000);
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : 'Delete failed';
      addOperation({ id: `err-${Date.now()}`, type: 'delete', bookName });
      updateOperation(`err-${Date.now()}`, { status: 'failed', error: errMsg });
    }
  }, [deleteTarget, token, tt, delBundles, addOperation, updateOperation, load]);

  useEffect(() => () => clearInterval(pollRef.current), []);

  if (loading)
    return (
      <div className="flex justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    );
  if (!publisher)
    return (
      <Alert variant="destructive">
        <AlertDescription>{error || 'Publisher not found'}</AlertDescription>
      </Alert>
    );

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => navigate('/publishers')}
        >
          <ArrowLeft className="h-5 w-5" />
        </Button>
        <h1 className="text-2xl font-semibold flex-1">
          {publisher.display_name || publisher.name}
        </h1>
        <div className="flex gap-2">
          <Button onClick={() => setUploadOpen(true)}>
            <Upload className="h-4 w-4" /> Upload
          </Button>
          <Button variant="outline" onClick={() => setFormOpen(true)}>
            <Pencil className="h-4 w-4" /> Edit
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Publisher Info</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <AuthenticatedImage
              src={publisher.logo_url || ''}
              token={token}
              tokenType={tt}
              alt={publisher.name}
              className="h-16 w-16 rounded-lg"
            />
            <div className="space-y-1 text-sm">
              <div>
                <span className="text-muted-foreground">Name:</span>{' '}
                {publisher.name}
              </div>
              <div>
                <span className="text-muted-foreground">Display:</span>{' '}
                {publisher.display_name || '—'}
              </div>
              <div>
                <span className="text-muted-foreground">Email:</span>{' '}
                {publisher.contact_email || '—'}
              </div>
              <div>
                <span className="text-muted-foreground">Status:</span>{' '}
                <Badge
                  variant={
                    publisher.status === 'active' ? 'success' : 'secondary'
                  }
                >
                  {publisher.status}
                </Badge>
              </div>
              {publisher.description && (
                <div>
                  <span className="text-muted-foreground">Description:</span>{' '}
                  {publisher.description}
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Assets ({assets.length} types)</CardTitle>
          </CardHeader>
          <CardContent>
            {!assets.length ? (
              <p className="text-muted-foreground text-sm">No assets</p>
            ) : (
              <div className="space-y-1">
                {assets.map((a) => (
                  <div key={a.name}>
                    <button
                      className="flex w-full items-center gap-2 rounded-md p-2 text-sm hover:bg-muted transition-colors"
                      onClick={() => toggleAsset(a.name)}
                    >
                      <ChevronRight
                        className={`h-4 w-4 transition-transform ${expandedAssets.has(a.name) ? 'rotate-90' : ''}`}
                      />
                      <FolderOpen className="h-4 w-4 text-muted-foreground" />
                      <span className="flex-1 text-left font-medium">
                        {a.name}
                      </span>
                      <Badge variant="outline">{a.file_count} files</Badge>
                      <span className="text-xs text-muted-foreground">
                        {fmtBytes(a.total_size)}
                      </span>
                    </button>
                    {expandedAssets.has(a.name) && assetFiles[a.name] && (
                      <div className="ml-10 space-y-0.5">
                        {assetFiles[a.name].map((f) => (
                          <div
                            key={f.path}
                            className="flex items-center gap-2 text-xs text-muted-foreground py-0.5"
                          >
                            <span className="truncate flex-1">{f.name}</span>
                            <span>{fmtBytes(f.size)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>Books ({books.length})</CardTitle>
          <Input
            placeholder="Search books..."
            value={bookSearch}
            onChange={(e) => setBookSearch(e.target.value)}
            className="max-w-xs"
          />
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Language</TableHead>
                <TableHead>Category</TableHead>
                <TableHead className="text-center">Activities</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {!filteredBooks.length ? (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="text-center py-8 text-muted-foreground"
                  >
                    No books found
                  </TableCell>
                </TableRow>
              ) : (
                filteredBooks.map((b) => (
                  <TableRow
                    key={b.id}
                    className="cursor-pointer hover:bg-accent/40"
                    onClick={() => navigate(`/books/${b.id}`)}
                  >
                    <TableCell className="font-medium">
                      <div className="flex items-center gap-2">
                        {b.book_title || b.book_name}
                        {(b.child_count ?? 0) > 0 && (
                          <Badge variant="secondary" className="gap-1">
                            <Paperclip className="h-3 w-3" />
                            +{b.child_count}
                          </Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">
                        {b.language.toUpperCase()}
                      </Badge>
                    </TableCell>
                    <TableCell>{b.category || '—'}</TableCell>
                    <TableCell className="text-center">
                      <Badge variant="secondary">{b.activity_count ?? 0}</Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{b.status}</Badge>
                    </TableCell>
                    <TableCell
                      className="text-right"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={() => {
                          setDeleteTarget(b);
                          setDelBundles(true);
                        }}
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

      <PublisherFormDialog
        open={formOpen}
        onClose={() => setFormOpen(false)}
        onSuccess={() => {
          setFormOpen(false);
          load();
        }}
        publisher={publisher}
        token={token}
        tokenType={tt}
      />
      <PublisherUploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onSuccess={load}
        token={token}
        tokenType={tt}
        initialPublisherId={publisher.id}
      />
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Book?</DialogTitle>
            <DialogDescription>
              Permanently delete &quot;{deleteTarget?.book_title || deleteTarget?.book_name}&quot;?
              This will remove all files from storage and cannot be undone.
            </DialogDescription>
          </DialogHeader>
          {deleteTarget && (deleteTarget.child_count ?? 0) > 0 && (
            <Alert>
              <AlertDescription>
                <div className="text-sm">
                  <strong>{deleteTarget.child_count}</strong> additional
                  resource
                  {deleteTarget.child_count === 1 ? '' : 's'} will be deleted
                  with this book:
                </div>
                {deleteChildren === null ? (
                  <div className="mt-1 text-xs text-muted-foreground">
                    Loading list…
                  </div>
                ) : deleteChildren.length === 0 ? null : (
                  <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs">
                    {deleteChildren.map((c) => (
                      <li key={c.id}>
                        {c.book_title || c.book_name}
                        <span className="ml-1 text-muted-foreground">
                          ({c.book_type === 'pdf' ? 'PDF' : 'Flowbook'})
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </AlertDescription>
            </Alert>
          )}
          <div className="flex items-center space-x-2 py-2">
            <Checkbox
              id="del-bundles-pub"
              checked={delBundles}
              onCheckedChange={(c) => setDelBundles(c === true)}
            />
            <Label htmlFor="del-bundles-pub" className="text-sm font-normal">
              Also delete bundles (standalone app builds)
            </Label>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDeleteBook}>
              Delete Permanently
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default PublisherDetailPage;
