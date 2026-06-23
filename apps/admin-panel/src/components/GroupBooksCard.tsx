import { useEffect, useState } from 'react';
import { Plus, X, Layers, Loader2, Check, Pencil, Search } from 'lucide-react';

import { Card, CardContent, CardHeader, CardTitle } from 'components/ui/card';
import { Button } from 'components/ui/button';
import { Input } from 'components/ui/input';
import { Badge } from 'components/ui/badge';
import { Alert, AlertDescription } from 'components/ui/alert';
import { fetchBooks, type BookRecord } from 'lib/books';
import {
  getBookGroup,
  createBookGroup,
  updateBookGroup,
  deleteBookGroup,
  addBooksToGroup,
  removeBookFromGroup,
} from 'lib/bookGroups';

const titleOf = (b: BookRecord) => b.book_title || b.book_name;

/** Searchable single-select list of books (filters by title / folder name). */
const BookPicker = ({
  books,
  selectedId,
  onPick,
  disabled,
}: {
  books: BookRecord[];
  selectedId?: number;
  onPick: (id: number) => void;
  disabled?: boolean;
}) => {
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  const filtered = q
    ? books.filter(
        (b) =>
          titleOf(b).toLowerCase().includes(q) ||
          b.book_name.toLowerCase().includes(q)
      )
    : books;

  return (
    <div className="space-y-2">
      <div className="relative">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          className="pl-8 h-8"
          placeholder="Search books…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          autoFocus
        />
      </div>
      <div className="max-h-52 overflow-y-auto rounded-md border divide-y">
        {filtered.length === 0 ? (
          <p className="px-3 py-3 text-sm text-muted-foreground">No matching books.</p>
        ) : (
          filtered.map((b) => (
            <button
              key={b.id}
              type="button"
              disabled={disabled}
              onClick={() => onPick(b.id)}
              className={`w-full text-left px-3 py-2 text-sm hover:bg-accent disabled:opacity-50 ${
                b.id === selectedId ? 'bg-accent' : ''
              }`}
            >
              <span className="font-medium">{titleOf(b)}</span>
              {b.book_title && b.book_title !== b.book_name && (
                <span className="block text-xs text-muted-foreground">{b.book_name}</span>
              )}
            </button>
          ))
        )}
      </div>
    </div>
  );
};

interface GroupBooksCardProps {
  book: BookRecord;
  token: string | null;
  tokenType: string;
  /** Reload the parent book detail after group changes (group_id may change). */
  onChanged: () => void;
}

const GroupBooksCard = ({ book, token, tokenType, onChanged }: GroupBooksCardProps) => {
  const grouped = book.group_id != null;

  const [members, setMembers] = useState<BookRecord[]>([]);
  const [groupName, setGroupName] = useState('');
  const [candidates, setCandidates] = useState<BookRecord[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Ungrouped → create-group form
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [pickId, setPickId] = useState<number | null>(null);

  // Grouped → add-book picker
  const [addingOpen, setAddingOpen] = useState(false);

  // Rename
  const [renaming, setRenaming] = useState(false);
  const [renameVal, setRenameVal] = useState('');

  const loadData = async () => {
    if (!token) return;
    setError(null);
    try {
      const pubBooks = await fetchBooks(token, tokenType, undefined, {
        publisherId: book.publisher_id,
        topLevelOnly: true,
      });
      // Candidates: other ungrouped books of the same publisher.
      setCandidates(pubBooks.filter((b) => b.id !== book.id && b.group_id == null));

      if (book.group_id != null) {
        const g = await getBookGroup(book.group_id, token, tokenType);
        setMembers(g.books);
        setGroupName(g.name);
      } else {
        setMembers([]);
        setGroupName('');
      }
    } catch {
      setError('Failed to load group');
    }
  };

  useEffect(() => {
    loadData();
    setCreating(false);
    setNewName('');
    setPickId(null);
    setAddingOpen(false);
    setRenaming(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [book.id, book.group_id]);

  const handleCreate = async () => {
    const name = newName.trim();
    if (!token || !name || pickId == null) return;
    setBusy(true);
    setError(null);
    try {
      const group = await createBookGroup(name, book.publisher_id, token, tokenType);
      await addBooksToGroup(group.id, [book.id, pickId], token, tokenType);
      onChanged();
    } catch {
      setError('Failed to create group');
    } finally {
      setBusy(false);
    }
  };

  const handleAdd = async (bookId: number) => {
    if (!token || book.group_id == null) return;
    setBusy(true);
    setError(null);
    try {
      await addBooksToGroup(book.group_id, [bookId], token, tokenType);
      setAddingOpen(false);
      await loadData();
      onChanged();
    } catch {
      setError('Failed to add book');
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async (bookId: number) => {
    if (!token || book.group_id == null) return;
    setBusy(true);
    setError(null);
    try {
      await removeBookFromGroup(book.group_id, bookId, token, tokenType);
      // A group with fewer than 2 books is pointless — dissolve it so no lone
      // book is left "grouped".
      if (members.length - 1 < 2) {
        await deleteBookGroup(book.group_id, token, tokenType);
      }
      await loadData();
      onChanged();
    } catch {
      setError('Failed to remove book');
    } finally {
      setBusy(false);
    }
  };

  const handleRename = async () => {
    const name = renameVal.trim();
    if (!token || book.group_id == null || !name || name === groupName) {
      setRenaming(false);
      return;
    }
    setBusy(true);
    try {
      await updateBookGroup(book.group_id, name, token, tokenType);
      setRenaming(false);
      await loadData();
      onChanged();
    } catch {
      setError('Failed to rename group');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base flex items-center gap-2">
          <Layers className="h-4 w-4" /> Group Books
          {grouped && !renaming && (
            <>
              <Badge variant="outline">{groupName}</Badge>
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                onClick={() => {
                  setRenameVal(groupName);
                  setRenaming(true);
                }}
                title="Rename group"
              >
                <Pencil className="h-3.5 w-3.5" />
              </Button>
            </>
          )}
          {grouped && renaming && (
            <span className="flex items-center gap-1">
              <Input
                className="h-7 w-48"
                value={renameVal}
                onChange={(e) => setRenameVal(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleRename()}
                autoFocus
              />
              <Button size="icon" className="h-7 w-7" onClick={handleRename} disabled={busy}>
                <Check className="h-4 w-4" />
              </Button>
            </span>
          )}
        </CardTitle>

        {grouped && candidates.length > 0 && (
          <Button
            size="sm"
            variant={addingOpen ? 'secondary' : 'default'}
            onClick={() => setAddingOpen((v) => !v)}
            disabled={busy}
          >
            <Plus className="h-4 w-4" /> Add book
          </Button>
        )}
      </CardHeader>

      <CardContent className="space-y-3">
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {grouped ? (
          <>
            {addingOpen && (
              <BookPicker books={candidates} onPick={handleAdd} disabled={busy} />
            )}
            <div className="rounded-md border divide-y">
              {members.map((m) => (
                <div key={m.id} className="flex items-center justify-between px-3 py-2">
                  <span className="text-sm">
                    <span className="font-medium">{titleOf(m)}</span>
                    {m.id === book.id && (
                      <Badge variant="secondary" className="ml-2">
                        this book
                      </Badge>
                    )}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => handleRemove(m.id)}
                    disabled={busy}
                    title="Remove from group"
                  >
                    {busy ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <X className="h-4 w-4 text-destructive" />
                    )}
                  </Button>
                </div>
              ))}
            </div>
          </>
        ) : creating ? (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Create a group with this book and another from the same publisher.
            </p>
            <Input
              placeholder="Group name (e.g. English File Elementary)"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
            <BookPicker
              books={candidates}
              selectedId={pickId ?? undefined}
              onPick={(id) => setPickId(id)}
            />
            <div className="flex gap-2">
              <Button onClick={handleCreate} disabled={!newName.trim() || pickId == null || busy}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                Create group
              </Button>
              <Button variant="outline" onClick={() => setCreating(false)} disabled={busy}>
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              {candidates.length === 0
                ? 'No other ungrouped books in this publisher to group with.'
                : 'This book is not in a group yet.'}
            </p>
            <Button size="sm" onClick={() => setCreating(true)} disabled={candidates.length === 0}>
              <Plus className="h-4 w-4" /> Group with…
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default GroupBooksCard;
