import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Loader2,
  Pencil,
  Plus,
  Trash2,
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
// Tabs removed — no more trash tab
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
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from 'components/ui/dialog';
import PublisherFormDialog from 'components/PublisherFormDialog';
import AuthenticatedImage from 'components/AuthenticatedImage';
import { useAuthStore } from 'stores/auth';
import {
  fetchPublishers,
  deletePublisher,
  fetchPublisherBooks,
  fetchPublisherAssetFiles,
  type Publisher,
} from 'lib/publishers';

type SortField = 'name' | 'display_name' | 'status' | 'created_at';
type SortDir = 'asc' | 'desc';

const statusVariant = (s: string) => {
  if (s === 'active') return 'success' as const;
  if (s === 'suspended') return 'destructive' as const;
  return 'secondary' as const;
};

const PublishersPage = () => {
  const navigate = useNavigate();
  const { token, tokenType } = useAuthStore();
  const tt = tokenType ?? 'Bearer';

  const [publishers, setPublishers] = useState<Publisher[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [sort, setSort] = useState<{ f: SortField; d: SortDir }>({
    f: 'name',
    d: 'asc',
  });
  const [deleteTarget, setDeleteTarget] = useState<Publisher | null>(null);
  const [deleteBookCount, setDeleteBookCount] = useState(0);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Publisher | null>(null);
  const [bookCounts, setBookCounts] = useState<Record<number, number>>({});
  const [logoFiles, setLogoFiles] = useState<Record<number, string | null>>({});

  const load = async () => {
    if (!token) return;
    setLoading(true);
    setError('');
    try {
      const active = await fetchPublishers(token, tt);
      setPublishers(active);
      const counts: Record<number, number> = {};
      const logos: Record<number, string | null> = {};
      await Promise.all(
        active.map(async (p) => {
          try {
            const bks = await fetchPublisherBooks(p.id, token, tt);
            counts[p.id] = bks.length;
          } catch {
            /* ignored */ counts[p.id] = 0;
          }
          try {
            const files = await fetchPublisherAssetFiles(
              p.id,
              'logos',
              token,
              tt
            );
            logos[p.id] = files[0]?.name || null;
          } catch {
            /* ignored */ logos[p.id] = null;
          }
        })
      );
      setBookCounts(counts);
      setLogoFiles(logos);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [token]);

  const filtered = useMemo(() => {
    let d = publishers;
    if (search) {
      const q = search.toLowerCase();
      d = d.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          p.display_name?.toLowerCase().includes(q) ||
          p.description?.toLowerCase().includes(q)
      );
    }
    if (statusFilter !== 'all') d = d.filter((p) => p.status === statusFilter);
    const dir = sort.d === 'asc' ? 1 : -1;
    return [...d].sort((a, b) => {
      const av = // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (a as any)[sort.f] ?? '';
      const bv = // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (b as any)[sort.f] ?? '';
      return String(av).localeCompare(String(bv)) * dir;
    });
  }, [publishers, search, statusFilter, sort]);

  const toggleSort = (f: SortField) =>
    setSort((c) => ({ f, d: c.f === f && c.d === 'asc' ? 'desc' : 'asc' }));

  const handleDelete = async () => {
    if (!deleteTarget || !token) return;
    try {
      await deletePublisher(deleteTarget.id, token, tt);
      setDeleteTarget(null);
      load();
    } catch {
      /* ignored */
    }
  };
  const promptDelete = async (p: Publisher) => {
    if (token) {
      try {
        const bks = await fetchPublisherBooks(p.id, token, tt);
        setDeleteBookCount(bks.length);
      } catch {
        /* ignored */ setDeleteBookCount(0);
      }
    }
    setDeleteTarget(p);
  };

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
        <h1 className="text-2xl font-semibold">Publishers</h1>
        <Button
          onClick={() => {
            setEditing(null);
            setFormOpen(true);
          }}
        >
          <Plus className="h-4 w-4" /> Add Publisher
        </Button>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="flex items-center gap-3">
        <Input
          placeholder="Search..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
        <Select value={statusFilter} onValueChange={setStatusFilter}>
          <SelectTrigger className="w-36">
            <SelectValue placeholder="All Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Status</SelectItem>
            <SelectItem value="active">Active</SelectItem>
            <SelectItem value="inactive">Inactive</SelectItem>
            <SelectItem value="suspended">Suspended</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-16">Logo</TableHead>
                    <SortHead field="name" label="Name" />
                    <SortHead field="display_name" label="Display Name" />
                    <TableHead className="text-center">Books</TableHead>
                    <SortHead field="status" label="Status" />
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
                        No publishers found
                      </TableCell>
                    </TableRow>
                  ) : (
                    filtered.map((p) => (
                      <TableRow
                        key={p.id}
                        className="cursor-pointer"
                        onClick={() => navigate(`/publishers/${p.id}`)}
                      >
                        <TableCell onClick={(e) => e.stopPropagation()}>
                          {logoFiles[p.id] ? (
                            <AuthenticatedImage
                              src={`/publishers/${p.id}/assets/logos/${encodeURIComponent(logoFiles[p.id]!)}`}
                              token={token}
                              tokenType={tt}
                              alt={p.name}
                              className="h-10 w-10 rounded-full"
                            />
                          ) : (
                            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-muted text-muted-foreground text-sm">
                              {p.name[0]?.toUpperCase()}
                            </div>
                          )}
                        </TableCell>
                        <TableCell className="font-medium">{p.name}</TableCell>
                        <TableCell>{p.display_name || '—'}</TableCell>
                        <TableCell className="text-center">
                          <Badge variant="outline">
                            {bookCounts[p.id] ?? 0}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          <Badge variant={statusVariant(p.status)}>
                            {p.status}
                          </Badge>
                        </TableCell>
                        <TableCell
                          className="text-right"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <div className="flex justify-end gap-1">
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7"
                                onClick={() => {
                                  setEditing(p);
                                  setFormOpen(true);
                                }}
                              >
                                <Pencil className="h-4 w-4" />
                              </Button>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7"
                                onClick={() => promptDelete(p)}
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

      <PublisherFormDialog
        open={formOpen}
        onClose={() => setFormOpen(false)}
        onSuccess={() => {
          setFormOpen(false);
          load();
        }}
        publisher={editing}
        token={token}
        tokenType={tt}
      />
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Publisher?</DialogTitle>
            <DialogDescription>
              Permanently delete &quot;{deleteTarget?.name}&quot;?{' '}
              {deleteBookCount > 0 &&
                `This will also delete ${deleteBookCount} book(s).`}{' '}
              This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default PublishersPage;
