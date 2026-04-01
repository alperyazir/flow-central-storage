import { useEffect, useMemo, useState } from 'react';
import { Loader2, Cpu, RefreshCw } from 'lucide-react';

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
import { Button } from 'components/ui/button';
import { Badge } from 'components/ui/badge';
import { Alert, AlertDescription } from 'components/ui/alert';
import ProcessingDialog from 'components/ProcessingDialog';
import { useAuthStore } from 'stores/auth';
import { fetchBooks, syncBooksWithR2, type SyncR2Response } from 'lib/books';

type SortField =
  | 'bookTitle'
  | 'publisher'
  | 'language'
  | 'category'
  | 'activityCount';
type SortDir = 'asc' | 'desc';

interface BookRow {
  id: number;
  bookName: string;
  bookTitle: string;
  publisher: string;
  publisherId: number;
  language: string;
  category: string;
  activityCount: number;
  status: string;
}

const BooksPage = () => {
  const { token, tokenType } = useAuthStore();
  const tt = tokenType ?? 'Bearer';

  const [books, setBooks] = useState<BookRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [pubFilter, setPubFilter] = useState('all');
  const [catFilter, setCatFilter] = useState('all');
  const [sort, setSort] = useState<{ f: SortField; d: SortDir }>({
    f: 'bookTitle',
    d: 'asc',
  });
  const [processingBook, setProcessingBook] = useState<BookRow | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState<SyncR2Response | null>(null);

  const handleSync = async () => {
    if (!token) return;
    setSyncing(true);
    setSyncResult(null);
    try {
      const result = await syncBooksWithR2(token, tt);
      setSyncResult(result);
      if (result.created.length > 0 || result.removed.length > 0) load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sync failed');
    } finally {
      setSyncing(false);
    }
  };

  const load = async () => {
    if (!token) return;
    setLoading(true);
    setError('');
    try {
      const recs = await fetchBooks(token, tt);
      setBooks(
        recs.map((r) => ({
          id: r.id,
          bookName: r.book_name,
          bookTitle: r.book_title || r.book_name,
          publisher: r.publisher,
          publisherId: r.publisher_id,
          language: r.language,
          category: r.category || '',
          activityCount: r.activity_count || 0,
          status: r.status,
        }))
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [token]);

  const pubs = useMemo(
    () => [...new Set(books.map((b) => b.publisher))].sort(),
    [books]
  );
  const cats = useMemo(
    () => [...new Set(books.map((b) => b.category).filter(Boolean))].sort(),
    [books]
  );

  const filtered = useMemo(() => {
    let d = books;
    if (search) {
      const q = search.toLowerCase();
      d = d.filter(
        (b) =>
          b.bookTitle.toLowerCase().includes(q) ||
          b.bookName.toLowerCase().includes(q) ||
          b.publisher.toLowerCase().includes(q)
      );
    }
    if (pubFilter !== 'all') d = d.filter((b) => b.publisher === pubFilter);
    if (catFilter !== 'all') d = d.filter((b) => b.category === catFilter);
    const dir = sort.d === 'asc' ? 1 : -1;
    return [...d].sort((a, b) => {
      if (sort.f === 'activityCount')
        return (a.activityCount - b.activityCount) * dir;
      return (
        String(// eslint-disable-next-line @typescript-eslint/no-explicit-any
      (a as any)[sort.f] ?? '').localeCompare(
          String(// eslint-disable-next-line @typescript-eslint/no-explicit-any
      (b as any)[sort.f] ?? '')
        ) * dir
      );
    });
  }, [books, search, pubFilter, catFilter, sort]);

  const toggleSort = (f: SortField) =>
    setSort((c) => ({ f, d: c.f === f && c.d === 'asc' ? 'desc' : 'asc' }));

  const SortHead = ({ field, label }: { field: SortField; label: string }) => (
    <TableHead
      className="cursor-pointer select-none"
      onClick={() => toggleSort(field)}
    >
      {label} {sort.f === field && (sort.d === 'asc' ? '↑' : '↓')}
    </TableHead>
  );

  if (loading)
    return (
      <div className="flex justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">All Books</h1>
        <Button variant="outline" onClick={handleSync} disabled={syncing}>
          <RefreshCw className={`h-4 w-4 ${syncing ? 'animate-spin' : ''}`} />
          {syncing ? 'Syncing...' : 'Sync R2'}
        </Button>
      </div>
      {syncResult && (
        <Alert>
          <AlertDescription>
            R2: {syncResult.r2_count} books, DB: {syncResult.db_count} books.
            {syncResult.created.length > 0 && ` Created ${syncResult.created.length} record(s).`}
            {syncResult.removed.length > 0 && ` Removed ${syncResult.removed.length} orphan(s).`}
            {syncResult.created.length === 0 && syncResult.removed.length === 0 && ' Already in sync.'}
          </AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Input
          placeholder="Search title, name, publisher..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
        <Select value={pubFilter} onValueChange={setPubFilter}>
          <SelectTrigger className="w-44">
            <SelectValue placeholder="All Publishers" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Publishers</SelectItem>
            {pubs.map((p) => (
              <SelectItem key={p} value={p}>
                {p}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={catFilter} onValueChange={setCatFilter}>
          <SelectTrigger className="w-40">
            <SelectValue placeholder="All Categories" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Categories</SelectItem>
            {cats.map((c) => (
              <SelectItem key={c} value={c}>
                {c}
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
                <SortHead field="bookTitle" label="Title" />
                <SortHead field="publisher" label="Publisher" />
                <SortHead field="language" label="Lang" />
                <SortHead field="category" label="Category" />
                <SortHead field="activityCount" label="Activities" />
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
                    No books found
                  </TableCell>
                </TableRow>
              ) : (
                filtered.map((b) => (
                  <TableRow key={b.id}>
                    <TableCell>
                      <div>
                        <span className="font-medium">{b.bookTitle}</span>
                        {b.bookTitle !== b.bookName && (
                          <span className="block text-xs text-muted-foreground">
                            {b.bookName}
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>{b.publisher}</TableCell>
                    <TableCell>
                      <Badge variant="outline">
                        {b.language.toUpperCase()}
                      </Badge>
                    </TableCell>
                    <TableCell>{b.category || '—'}</TableCell>
                    <TableCell>
                      <Badge variant="secondary">{b.activityCount}</Badge>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={() => setProcessingBook(b)}
                      >
                        <Cpu className="h-4 w-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {processingBook && (
        <ProcessingDialog
          open={!!processingBook}
          onClose={() => setProcessingBook(null)}
          bookId={processingBook.id}
          bookTitle={processingBook.bookTitle}
          token={token}
          tokenType={tt}
        />
      )}
    </div>
  );
};

export default BooksPage;
