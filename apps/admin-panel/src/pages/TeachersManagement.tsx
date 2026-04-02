import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2, Pencil, Plus, Trash2 } from 'lucide-react';

import { Card, CardContent } from 'components/ui/card';
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
import { Alert, AlertDescription } from 'components/ui/alert';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from 'components/ui/dialog';
import TeacherFormDialog from 'components/TeacherFormDialog';
import { useAuthStore } from 'stores/auth';
import {
  fetchTeachers,
  deleteTeacher,
  formatBytes,
  type TeacherListItem,
} from 'lib/teacherManagement';

type SortField =
  | 'teacher_id'
  | 'display_name'
  | 'material_count'
  | 'total_storage_size'
  | 'status';
type SortDir = 'asc' | 'desc';

const statusVariant = (s: string) => {
  if (s === 'active') return 'success' as const;
  if (s === 'inactive') return 'warning' as const;
  return 'destructive' as const;
};

const TeachersManagementPage = () => {
  const navigate = useNavigate();
  const { token, tokenType } = useAuthStore();
  const tt = tokenType ?? 'Bearer';

  const [teachers, setTeachers] = useState<TeacherListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<{ f: SortField; d: SortDir }>({
    f: 'teacher_id',
    d: 'asc',
  });
  const [deleteTarget, setDeleteTarget] = useState<TeacherListItem | null>(
    null
  );
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<TeacherListItem | null>(null);

  const load = async () => {
    if (!token) return;
    setLoading(true);
    setError('');
    try {
      const active = await fetchTeachers(token, tt);
      setTeachers(active);
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
    let d = teachers;
    if (search) {
      const q = search.toLowerCase();
      d = d.filter(
        (t) =>
          t.teacher_id.toLowerCase().includes(q) ||
          t.display_name?.toLowerCase().includes(q) ||
          t.email?.toLowerCase().includes(q)
      );
    }
    const dir = sort.d === 'asc' ? 1 : -1;
    return [...d].sort((a, b) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const av = (a as any)[sort.f] ?? '';
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const bv = (b as any)[sort.f] ?? '';
      if (typeof av === 'number') return (av - (bv as number)) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
  }, [teachers, search, sort]);

  const toggleSort = (f: SortField) =>
    setSort((c) => ({ f, d: c.f === f && c.d === 'asc' ? 'desc' : 'asc' }));

  const handleDelete = async () => {
    if (!deleteTarget || !token) return;
    try {
      await deleteTeacher(deleteTarget.id, token, tt);
      setDeleteTarget(null);
      load();
    } catch {
      /* ignored */
    }
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
        <h1 className="text-2xl font-semibold">Teachers</h1>
        <Button
          onClick={() => {
            setEditing(null);
            setFormOpen(true);
          }}
        >
          <Plus className="h-4 w-4" /> Add Teacher
        </Button>
      </div>
      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <Input
        placeholder="Search by ID, name, email..."
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        className="max-w-xs"
      />

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <SortHead field="teacher_id" label="Teacher ID" />
                <SortHead field="display_name" label="Name" />
                <TableHead>Email</TableHead>
                <SortHead field="material_count" label="Materials" />
                <SortHead field="total_storage_size" label="Storage" />
                <SortHead field="status" label="Status" />
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
                    No teachers found
                  </TableCell>
                </TableRow>
              ) : (
                filtered.map((t) => (
                  <TableRow
                    key={t.id}
                    className="cursor-pointer"
                    onClick={() => navigate(`/teachers/${t.id}`)}
                  >
                    <TableCell className="font-medium">
                      {t.teacher_id}
                    </TableCell>
                    <TableCell>{t.display_name || '—'}</TableCell>
                    <TableCell>{t.email || '—'}</TableCell>
                    <TableCell className="text-center">
                      <Badge variant="outline">{t.material_count}</Badge>
                    </TableCell>
                    <TableCell>
                      {formatBytes(t.total_storage_size)}
                    </TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(t.status)}>
                        {t.status}
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
                            setEditing(t);
                            setFormOpen(true);
                          }}
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => setDeleteTarget(t)}
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

      <TeacherFormDialog
        open={formOpen}
        onClose={() => setFormOpen(false)}
        onSuccess={() => {
          setFormOpen(false);
          load();
        }}
        teacher={editing}
        token={token}
        tokenType={tt}
      />
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Teacher?</DialogTitle>
            <DialogDescription>
              Permanently delete teacher &quot;{deleteTarget?.teacher_id}&quot;
              and all their materials? This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete}>
              Delete Permanently
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default TeachersManagementPage;
