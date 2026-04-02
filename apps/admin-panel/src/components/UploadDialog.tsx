import { ChangeEvent, useEffect, useMemo, useState } from 'react';
import { Loader2, Upload } from 'lucide-react';
import { useOperationsStore } from 'stores/operations';

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from 'components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from 'components/ui/tabs';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from 'components/ui/select';
import { Button } from 'components/ui/button';
import { Label } from 'components/ui/label';
import { Alert, AlertDescription } from 'components/ui/alert';
import { Progress } from 'components/ui/progress';
import { ApiError } from 'lib/api';
import {
  uploadBookArchive,
  uploadNewBookArchive,
  uploadAppArchive,
} from 'lib/uploads';
import { SUPPORTED_APP_PLATFORMS } from 'lib/platforms';

type UploadMode = 'book' | 'app';
type BookUploadFlow = 'new' | 'update';

interface UploadBookOption {
  id: number;
  title: string;
  publisher: string;
}

interface FeedbackState {
  type: 'success' | 'error';
  message: string;
}

interface OverrideContext {
  mode: UploadMode;
  bookId?: number;
  platform?: string;
  file: File;
}

interface UploadDialogProps {
  open: boolean;
  onClose: () => void;
  books: UploadBookOption[];
  platforms: readonly string[];
  token: string | null;
  tokenType: string | null;
  onSuccess: () => void;
}

const deriveErrorMessage = (error: unknown): string => {
  if (error instanceof ApiError) {
    const detail = (error.body as { detail?: unknown } | null)?.detail;
    if (typeof detail === 'string') return detail;
  }
  if (error instanceof Error) return error.message;
  return 'Upload failed. Please try again.';
};

export function UploadDialog({
  open,
  onClose,
  books,
  token,
  tokenType,
  onSuccess,
}: UploadDialogProps) {
  const [mode, setMode] = useState<UploadMode>('book');
  const [selectedBookId, setSelectedBookId] = useState<string>('');
  const [selectedPlatform, setSelectedPlatform] = useState<string>('');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<FeedbackState | null>(null);
  const [bookUploadFlow, setBookUploadFlow] = useState<BookUploadFlow>('new');
  const [overrideContext, setOverrideContext] =
    useState<OverrideContext | null>(null);

  const sortedBooks = useMemo(
    () => [...books].sort((a, b) => a.title.localeCompare(b.title)),
    [books]
  );

  useEffect(() => {
    if (!open) {
      setMode('book');
      setSelectedBookId('');
      setSelectedPlatform('');
      setSelectedFile(null);
      setIsSubmitting(false);
      setFeedback(null);
      setBookUploadFlow('new');
      setOverrideContext(null);
    }
  }, [open]);

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    setSelectedFile(e.target.files?.[0] || null);
    setFeedback(null);
  };

  const { addOperation, updateOperation } = useOperationsStore();

  const handleSubmit = async (override = false) => {
    if (!selectedFile || !token || !tokenType) return;

    const opId = `upload-${Date.now()}-${selectedFile.name}`;
    const opName = mode === 'book'
      ? selectedFile.name.replace(/\.zip$/i, '')
      : `App: ${selectedPlatform}/${selectedFile.name}`;

    addOperation({ id: opId, type: 'upload', bookName: opName });
    updateOperation(opId, { status: 'in_progress', progress: 50, detail: 'Uploading...' });
    onClose();

    try {
      if (mode === 'book') {
        if (bookUploadFlow === 'update' && selectedBookId) {
          await uploadBookArchive(
            Number(selectedBookId),
            selectedFile,
            token,
            tokenType,
            undefined,
            { override }
          );
        } else {
          await uploadNewBookArchive(
            selectedFile,
            token,
            tokenType,
            undefined,
            { override }
          );
        }
      } else {
        if (!selectedPlatform) return;
        await uploadAppArchive(
          selectedPlatform,
          selectedFile,
          token,
          tokenType,
          undefined,
          { override }
        );
      }
      updateOperation(opId, { status: 'completed', progress: 100, detail: 'Upload complete' });
      onSuccess();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        updateOperation(opId, { status: 'failed', error: 'Version conflict — use override' });
        return;
      }
      updateOperation(opId, { status: 'failed', error: deriveErrorMessage(error) });
    }
  };

  const handleOverride = () => {
    setOverrideContext(null);
    handleSubmit(true);
  };

  const canSubmit =
    selectedFile &&
    !isSubmitting &&
    (mode === 'app'
      ? !!selectedPlatform
      : bookUploadFlow === 'new' || !!selectedBookId);

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Upload Archive</DialogTitle>
        </DialogHeader>

        {overrideContext ? (
          <div className="space-y-4">
            <Alert variant="destructive">
              <AlertDescription>
                This content already exists. Do you want to override it?
              </AlertDescription>
            </Alert>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setOverrideContext(null)}
              >
                Cancel
              </Button>
              <Button variant="destructive" onClick={handleOverride}>
                Override
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <>
            <Tabs value={mode} onValueChange={(v) => setMode(v as UploadMode)}>
              <TabsList className="w-full">
                <TabsTrigger value="book" className="flex-1">
                  Book
                </TabsTrigger>
                <TabsTrigger value="app" className="flex-1">
                  Application
                </TabsTrigger>
              </TabsList>

              <TabsContent value="book" className="space-y-4">
                <div className="space-y-2">
                  <Label>Upload Type</Label>
                  <Select
                    value={bookUploadFlow}
                    onValueChange={(v) =>
                      setBookUploadFlow(v as BookUploadFlow)
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="new">New Book</SelectItem>
                      <SelectItem value="update">Update Existing</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {bookUploadFlow === 'update' && (
                  <div className="space-y-2">
                    <Label>Select Book</Label>
                    <Select
                      value={selectedBookId}
                      onValueChange={setSelectedBookId}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Choose a book..." />
                      </SelectTrigger>
                      <SelectContent>
                        {sortedBooks.map((b) => (
                          <SelectItem key={b.id} value={String(b.id)}>
                            {b.title} ({b.publisher})
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                )}
              </TabsContent>

              <TabsContent value="app" className="space-y-4">
                <div className="space-y-2">
                  <Label>Platform</Label>
                  <Select
                    value={selectedPlatform}
                    onValueChange={setSelectedPlatform}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Choose platform..." />
                    </SelectTrigger>
                    <SelectContent>
                      {SUPPORTED_APP_PLATFORMS.map((p) => (
                        <SelectItem key={p} value={p}>
                          {p}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </TabsContent>
            </Tabs>

            <div className="space-y-2">
              <Label>Archive File (.zip)</Label>
              <input
                type="file"
                accept=".zip"
                onChange={handleFileChange}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm file:border-0 file:bg-transparent file:text-sm file:font-medium"
              />
              {selectedFile && (
                <p className="text-xs text-muted-foreground">
                  {selectedFile.name}
                </p>
              )}
            </div>

            {isSubmitting && (
              <Progress value={undefined} className="animate-pulse" />
            )}

            {feedback && (
              <Alert
                variant={feedback.type === 'error' ? 'destructive' : 'default'}
              >
                <AlertDescription>{feedback.message}</AlertDescription>
              </Alert>
            )}

            <DialogFooter>
              <Button
                variant="outline"
                onClick={onClose}
                disabled={isSubmitting}
              >
                Cancel
              </Button>
              <Button onClick={() => handleSubmit()} disabled={!canSubmit}>
                {isSubmitting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Upload className="h-4 w-4" />
                )}
                Upload
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default UploadDialog;
