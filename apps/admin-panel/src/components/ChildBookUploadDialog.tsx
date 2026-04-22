import { useMemo, useRef, useState } from 'react';
import { FileUp, Loader2, FileText, BookOpen } from 'lucide-react';

import { Button } from 'components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from 'components/ui/dialog';
import { Input } from 'components/ui/input';
import { Label } from 'components/ui/label';
import { Alert, AlertDescription } from 'components/ui/alert';
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from 'components/ui/tabs';

import {
  createChildBook,
  type BookRecord,
  type BookType,
} from 'lib/books';
import { uploadChildBookChunked } from 'lib/uploads';
import { useOperationsStore } from 'stores/operations';
import { appConfig } from 'config/environment';

interface ChildBookUploadDialogProps {
  open: boolean;
  parent: BookRecord | null;
  token: string;
  tokenType: string;
  onClose: () => void;
  onCreated?: () => void;
}

const slugifyBookName = (input: string): string =>
  input
    .trim()
    .replace(/\.[^.]+$/, '')
    .replace(/[^a-zA-Z0-9]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_|_$/g, '');

const ChildBookUploadDialog = ({
  open,
  parent,
  token,
  tokenType,
  onClose,
  onCreated,
}: ChildBookUploadDialogProps) => {
  const [bookType, setBookType] = useState<BookType>('standard');
  const [file, setFile] = useState<File | null>(null);
  const [displayName, setDisplayName] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const addOperation = useOperationsStore((s) => s.addOperation);
  const updateOperation = useOperationsStore((s) => s.updateOperation);

  const acceptSuffix = bookType === 'pdf' ? '.pdf' : '.zip';

  const resolvedBookName = useMemo(() => {
    if (displayName.trim()) return slugifyBookName(displayName);
    if (file) return slugifyBookName(file.name);
    return '';
  }, [displayName, file]);

  const reset = () => {
    setFile(null);
    setDisplayName('');
    setError(null);
    setSubmitting(false);
  };

  const handleClose = () => {
    if (submitting) return;
    reset();
    onClose();
  };

  const handleFilePick = (picked: File | null) => {
    setFile(picked);
    if (picked && !displayName) {
      setDisplayName(picked.name.replace(/\.[^.]+$/, ''));
    }
  };

  const handleSubmit = async () => {
    if (!parent || !file) return;
    const bookName = resolvedBookName;
    if (!bookName) {
      setError('Please provide a book name');
      return;
    }
    const fileExt = file.name.toLowerCase();
    if (bookType === 'pdf' && !fileExt.endsWith('.pdf')) {
      setError('PDF resources require a .pdf file');
      return;
    }
    if (bookType === 'standard' && !fileExt.endsWith('.zip')) {
      setError('Flowbook resources require a .zip archive');
      return;
    }

    const opId = `child-upload-${parent.id}-${Date.now()}`;
    addOperation({ id: opId, type: 'upload', bookName });
    setSubmitting(true);
    setError(null);
    updateOperation(opId, { status: 'in_progress', progress: 5, detail: 'Creating...' });

    try {
      const created = await createChildBook(
        {
          parent_book_id: parent.id,
          book_name: bookName,
          book_title: displayName.trim() || bookName,
          book_type: bookType,
          language: parent.language || 'en',
          publisher: parent.publisher,
          publisher_id: parent.publisher_id,
        },
        token,
        tokenType
      );

      updateOperation(opId, { progress: 10, detail: 'Uploading...' });

      // Fire the chunked upload but do NOT await it here — we want the
      // dialog to close immediately and let the Operations panel track
      // the rest. The inner callbacks update the operations store so
      // progress is visible even after the component unmounts.
      const { promise } = uploadChildBookChunked(
        file,
        created.id,
        token,
        tokenType,
        (s) => {
          updateOperation(opId, {
            status: 'in_progress',
            progress: s.progress,
            detail: s.detail || s.step,
          });
        },
        { autoBundle: bookType === 'standard' },
        appConfig.apiBaseUrl
      );
      promise
        .then(() => {
          updateOperation(opId, {
            status: 'completed',
            progress: 100,
            detail: 'Uploaded',
          });
          // Second refresh: pick up server-side fields written after
          // upload completes (total_size, activity_count for flowbooks).
          onCreated?.();
        })
        .catch((exc) => {
          const msg = exc instanceof Error ? exc.message : 'Upload failed';
          updateOperation(opId, { status: 'failed', error: msg });
        });

      // First refresh: surface the new child row in the parent list
      // immediately while the upload continues in the Operations panel.
      onCreated?.();
      handleClose();
    } catch (exc) {
      const msg = exc instanceof Error ? exc.message : 'Upload failed';
      setError(msg);
      updateOperation(opId, { status: 'failed', error: msg });
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && handleClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add Additional Resource</DialogTitle>
          <DialogDescription>
            Attach a child book to{' '}
            <strong>{parent?.book_title || parent?.book_name}</strong>.
          </DialogDescription>
        </DialogHeader>

        <Tabs
          value={bookType}
          onValueChange={(v) => {
            setBookType(v as BookType);
            setFile(null);
          }}
        >
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="standard" className="gap-2">
              <BookOpen className="h-4 w-4" /> Flowbook (ZIP)
            </TabsTrigger>
            <TabsTrigger value="pdf" className="gap-2">
              <FileText className="h-4 w-4" /> PDF
            </TabsTrigger>
          </TabsList>
          <TabsContent value="standard" className="text-xs text-muted-foreground pt-2">
            A full flowbook archive. Activities and bundles are auto-generated.
          </TabsContent>
          <TabsContent value="pdf" className="text-xs text-muted-foreground pt-2">
            A single raw PDF. No activities, no bundles — download only.
          </TabsContent>
        </Tabs>

        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="child-book-name">Display name</Label>
            <Input
              id="child-book-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="e.g. Teacher Guide"
              disabled={submitting}
            />
            {resolvedBookName && (
              <p className="text-xs text-muted-foreground">
                Stored as <code>{resolvedBookName}</code>
              </p>
            )}
          </div>

          <div className="space-y-1">
            <Label>File ({acceptSuffix})</Label>
            <input
              ref={inputRef}
              type="file"
              accept={acceptSuffix}
              className="hidden"
              onChange={(e) => handleFilePick(e.target.files?.[0] ?? null)}
            />
            <Button
              type="button"
              variant="outline"
              className="w-full justify-start"
              onClick={() => inputRef.current?.click()}
              disabled={submitting}
            >
              <FileUp className="h-4 w-4" />
              {file ? file.name : `Choose ${acceptSuffix.toUpperCase()} file`}
            </Button>
          </div>

          {submitting && (
            <p className="text-xs text-muted-foreground">
              Starting upload… you can close this; progress appears in the
              Operations panel.
            </p>
          )}

          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={submitting || !file}>
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            Upload
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default ChildBookUploadDialog;
