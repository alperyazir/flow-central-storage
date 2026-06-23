import { Fragment, useMemo, useState } from 'react';
import { Search, Layers, Check } from 'lucide-react';

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from 'components/ui/table';
import { Input } from 'components/ui/input';
import { Badge } from 'components/ui/badge';
import type { BookRecord } from 'lib/books';

interface Entry {
  value: string; // a book id (for a group, one member's id; the task expands it)
  label: string;
  publisher: string;
  kind: 'group' | 'book';
}

/**
 * Searchable table picker for a bundle target: ungrouped books listed
 * individually plus one row per group (grouped books bundle as a single group
 * package). Filtered by a search box and separated by publisher.
 */
const BundleTargetPicker = ({
  books,
  groupNames,
  value,
  onChange,
}: {
  books: BookRecord[];
  groupNames: Map<number, string>;
  value: string;
  onChange: (value: string) => void;
}) => {
  const [query, setQuery] = useState('');

  const entries = useMemo<Entry[]>(() => {
    const out: Entry[] = [];
    const reps = new Map<number, BookRecord>();
    for (const b of books) {
      if (b.group_id != null && !reps.has(b.group_id)) reps.set(b.group_id, b);
    }
    for (const [gid, rep] of reps) {
      out.push({
        value: String(rep.id),
        label: groupNames.get(gid) ?? 'Group',
        publisher: rep.publisher,
        kind: 'group',
      });
    }
    for (const b of books) {
      if (b.group_id == null) {
        out.push({
          value: String(b.id),
          label: b.book_title || b.book_name,
          publisher: b.publisher,
          kind: 'book',
        });
      }
    }
    return out;
  }, [books, groupNames]);

  const q = query.trim().toLowerCase();
  const filtered = q
    ? entries.filter(
        (e) =>
          e.label.toLowerCase().includes(q) ||
          e.publisher.toLowerCase().includes(q)
      )
    : entries;

  const byPublisher = useMemo(() => {
    const m = new Map<string, Entry[]>();
    for (const e of filtered) {
      const list = m.get(e.publisher);
      if (list) list.push(e);
      else m.set(e.publisher, [e]);
    }
    for (const list of m.values()) {
      list.sort((a, b) => {
        if (a.kind !== b.kind) return a.kind === 'group' ? -1 : 1;
        return a.label.localeCompare(b.label);
      });
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [filtered]);

  return (
    <div className="space-y-2">
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          className="pl-8 h-9"
          placeholder="Search books or groups…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      <div className="max-h-80 overflow-y-auto rounded-md border">
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-background">
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead className="w-28">Type</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {byPublisher.length === 0 ? (
              <TableRow>
                <TableCell colSpan={2} className="text-center py-8 text-muted-foreground">
                  No matching books or groups.
                </TableCell>
              </TableRow>
            ) : (
              byPublisher.map(([publisher, items]) => (
                <Fragment key={publisher}>
                  <TableRow className="bg-muted/50 hover:bg-muted/50">
                    <TableCell
                      colSpan={2}
                      className="py-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground"
                    >
                      {publisher}
                    </TableCell>
                  </TableRow>
                  {items.map((e) => {
                    const selected = e.value === value;
                    return (
                      <TableRow
                        key={`${e.kind}-${e.value}`}
                        onClick={() => onChange(e.value)}
                        className={`cursor-pointer ${selected ? 'bg-accent' : ''}`}
                      >
                        <TableCell className="font-medium">
                          <span className="flex items-center gap-2">
                            {e.kind === 'group' && (
                              <Layers className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                            )}
                            <span className="truncate">{e.label}</span>
                            {selected && (
                              <Check className="h-4 w-4 shrink-0 text-primary" />
                            )}
                          </span>
                        </TableCell>
                        <TableCell>
                          <Badge variant={e.kind === 'group' ? 'outline' : 'secondary'}>
                            {e.kind === 'group' ? 'Group' : 'Book'}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </Fragment>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
};

export default BundleTargetPicker;
