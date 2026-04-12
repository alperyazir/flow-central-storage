import { ChangeEvent, useEffect, useReducer, useState } from 'react';
import { Loader2, Upload, CheckCircle, XCircle } from 'lucide-react';

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from 'components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from 'components/ui/select';
import { Button } from 'components/ui/button';
import { Input } from 'components/ui/input';
import { Label } from 'components/ui/label';
import { Alert, AlertDescription } from 'components/ui/alert';
import { Progress } from 'components/ui/progress';
import { Badge } from 'components/ui/badge';
import { Checkbox } from 'components/ui/checkbox';
import {
  fetchPublishers,
  uploadPublisherAsset,
  type Publisher,
} from 'lib/publishers';
import {
  uploadNewBookChunked,
} from 'lib/uploads';
import { ApiError } from 'lib/api';
import { useOperationsStore } from 'stores/operations';

interface PublisherUploadDialogProps {
  open: boolean;
  onClose: () => void;
  token: string | null;
  tokenType: string | null;
  onSuccess: () => void;
  initialPublisherId?: number;
}

type UploadStep =
  | 'publisher'
  | 'content-type'
  | 'files'
  | 'uploading'
  | 'results';

interface UploadResult {
  filename: string;
  success: boolean;
  error?: string;
  path?: string;
}

const CONTENT_TYPE_RULES: Record<
  string,
  { accept: string; maxSize: number; multiple: boolean; label: string }
> = {
  books: {
    accept: '.zip',
    maxSize: 2 * 1024 * 1024 * 1024,
    multiple: true,
    label: 'Books (.zip)',
  },
  materials: {
    accept: '.pdf,.docx,.pptx,.jpg,.jpeg,.png,.gif,.mp3,.mp4',
    maxSize: 100 * 1024 * 1024,
    multiple: true,
    label: 'Materials (docs, images, audio, video)',
  },
  logos: {
    accept: '.png,.jpg,.jpeg,.svg',
    maxSize: 5 * 1024 * 1024,
    multiple: false,
    label: 'Logos (.png, .jpg, .svg)',
  },
};

const PREDEFINED_TYPES = ['books', 'materials', 'logos'];

const deriveError = (e: unknown): string => {
  if (e instanceof ApiError) {
    const d = (e.body as Record<string, unknown> | null)?.detail;
    return typeof d === 'string' ? d : `Failed (${e.status})`;
  }
  return e instanceof Error ? e.message : 'Upload failed';
};

interface State {
  step: UploadStep;
  publisherId: number | null;
  contentType: string;
  customType: string;
  files: File[];
  results: UploadResult[];
  error: string | null;
}

type Action =
  | { type: 'SET_PUBLISHER'; id: number }
  | { type: 'SET_CONTENT_TYPE'; value: string }
  | { type: 'SET_CUSTOM_TYPE'; value: string }
  | { type: 'SET_FILES'; files: File[] }
  | { type: 'SET_STEP'; step: UploadStep }
  | { type: 'SET_RESULTS'; results: UploadResult[] }
  | { type: 'SET_ERROR'; error: string }
  | { type: 'RESET'; initialPublisherId?: number };

const reducer = (state: State, action: Action): State => {
  switch (action.type) {
    case 'SET_PUBLISHER':
      return { ...state, publisherId: action.id };
    case 'SET_CONTENT_TYPE':
      return { ...state, contentType: action.value, customType: '' };
    case 'SET_CUSTOM_TYPE':
      return { ...state, customType: action.value };
    case 'SET_FILES':
      return { ...state, files: action.files };
    case 'SET_STEP':
      return { ...state, step: action.step };
    case 'SET_RESULTS':
      return { ...state, step: 'results', results: action.results };
    case 'SET_ERROR':
      return { ...state, error: action.error };
    case 'RESET':
      return {
        step: action.initialPublisherId ? 'content-type' : 'publisher',
        publisherId: action.initialPublisherId ?? null,
        contentType: '',
        customType: '',
        files: [],
        results: [],
        error: null,
      };
    default:
      return state;
  }
};

export function PublisherUploadDialog({
  open,
  onClose,
  token,
  tokenType,
  onSuccess,
  initialPublisherId,
}: PublisherUploadDialogProps) {
  const [state, dispatch] = useReducer(reducer, {
    step: initialPublisherId ? 'content-type' : 'publisher',
    publisherId: initialPublisherId ?? null,
    contentType: '',
    customType: '',
    files: [],
    results: [],
    error: null,
  });

  const [publishers, setPublishers] = useState<Publisher[]>([]);
  const [loadingPubs, setLoadingPubs] = useState(false);
  const [fileError, setFileError] = useState('');
  const [overrideExisting, setOverrideExisting] = useState(false);
  const [autoBundle, setAutoBundle] = useState(true);

  useEffect(() => {
    if (open && token) {
      setLoadingPubs(true);
      fetchPublishers(token, tokenType || 'Bearer')
        .then(setPublishers)
        .catch(() => {})
        .finally(() => setLoadingPubs(false));
    }
  }, [open, token, tokenType]);

  useEffect(() => {
    if (!open) {
      dispatch({ type: 'RESET', initialPublisherId });
      setFileError('');
      setOverrideExisting(false);
      setAutoBundle(true);
    }
  }, [open, initialPublisherId]);

  const selectedPub = publishers.find((p) => p.id === state.publisherId);
  const effectiveType =
    state.contentType === 'custom'
      ? state.customType.toLowerCase()
      : state.contentType;
  const rules = CONTENT_TYPE_RULES[effectiveType] || {
    accept: '*',
    maxSize: 100 * 1024 * 1024,
    multiple: true,
    label: 'Files',
  };

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    setFileError('');
    if (!rules.multiple && files.length > 1) {
      setFileError('Only one file allowed');
      return;
    }
    for (const f of files) {
      if (f.size > rules.maxSize) {
        setFileError(
          `"${f.name}" exceeds max size (${Math.round(rules.maxSize / 1024 / 1024)}MB)`
        );
        return;
      }
    }
    dispatch({ type: 'SET_FILES', files });
    e.target.value = '';
  };

  const { addOperation, updateOperation } = useOperationsStore();

  const handleUpload = async () => {
    if (!token || !state.publisherId || !state.files.length) return;

    const tt = tokenType || 'Bearer';
    const apiBase = import.meta.env?.VITE_API_BASE_URL || '';

    if (effectiveType === 'books') {
      // Close dialog immediately — progress tracked in activity log panel
      onClose();

      for (const file of state.files) {
        const opId = `upload-${Date.now()}-${file.name}`;
        const bookName = file.name.replace(/\.zip$/i, '');
        addOperation({ id: opId, type: 'upload', bookName });
        updateOperation(opId, { status: 'in_progress', progress: 0, detail: 'Uploading...' });

        try {
          const { promise } = uploadNewBookChunked(
            file,
            token,
            tt,
            (p) => {
              updateOperation(opId, {
                status: p.error ? 'failed' : p.progress >= 100 ? 'completed' : 'in_progress',
                progress: p.progress,
                detail: p.detail || p.step?.replace(/_/g, ' ') || '',
                error: p.error || undefined,
              });
            },
            { publisherId: state.publisherId!, override: overrideExisting, autoBundle },
            apiBase
          );
          await promise;
          updateOperation(opId, { status: 'completed', progress: 100, detail: 'Upload complete' });
        } catch (e) {
          updateOperation(opId, { status: 'failed', error: deriveError(e) });
        }
      }
      onSuccess();
    } else {
      // Non-book uploads: close dialog, track in activity log
      onClose();

      for (const file of state.files) {
        const opId = `upload-${Date.now()}-${file.name}`;
        addOperation({ id: opId, type: 'upload', bookName: `${effectiveType}/${file.name}` });
        updateOperation(opId, { status: 'in_progress', progress: 50, detail: 'Uploading...' });

        try {
          await uploadPublisherAsset(
            state.publisherId!,
            effectiveType,
            file,
            token,
            tt
          );
          updateOperation(opId, { status: 'completed', progress: 100, detail: 'Upload complete' });
        } catch (e) {
          updateOperation(opId, { status: 'failed', error: deriveError(e) });
        }
      }
      onSuccess();
    }
  };

  const canProceed = () => {
    if (state.step === 'publisher') return !!state.publisherId;
    if (state.step === 'content-type')
      return (
        !!state.contentType &&
        (state.contentType !== 'custom' || state.customType.trim().length > 0)
      );
    if (state.step === 'files') return state.files.length > 0;
    return false;
  };

  const nextStep = () => {
    const order: UploadStep[] = initialPublisherId
      ? ['content-type', 'files']
      : ['publisher', 'content-type', 'files'];
    const i = order.indexOf(state.step);
    if (i < order.length - 1)
      dispatch({ type: 'SET_STEP', step: order[i + 1] });
    else handleUpload();
  };

  const prevStep = () => {
    const order: UploadStep[] = initialPublisherId
      ? ['content-type', 'files']
      : ['publisher', 'content-type', 'files'];
    const i = order.indexOf(state.step);
    if (i > 0) dispatch({ type: 'SET_STEP', step: order[i - 1] });
  };

  const steps = initialPublisherId
    ? ['Content Type', 'Files']
    : ['Publisher', 'Content Type', 'Files'];
  const stepIndex = (() => {
    const order: UploadStep[] = initialPublisherId
      ? ['content-type', 'files']
      : ['publisher', 'content-type', 'files'];
    const i = order.indexOf(state.step);
    return i >= 0 ? i : steps.length;
  })();

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => state.step !== 'uploading' && !o && onClose()}
    >
      <DialogContent className="sm:max-w-md max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            Upload to{' '}
            {selectedPub?.display_name || selectedPub?.name || 'Publisher'}
          </DialogTitle>
        </DialogHeader>

        {/* Step indicator */}
        <div className="flex items-center gap-2 text-xs">
          {steps.map((s, i) => (
            <div key={s} className="flex items-center gap-1">
              <div
                className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-medium ${i <= stepIndex ? 'bg-primary text-white' : 'bg-muted text-muted-foreground'}`}
              >
                {i + 1}
              </div>
              <span
                className={
                  i <= stepIndex ? 'font-medium' : 'text-muted-foreground'
                }
              >
                {s}
              </span>
              {i < steps.length - 1 && (
                <div className="mx-1 h-px w-4 bg-border" />
              )}
            </div>
          ))}
        </div>

        <div className="space-y-4 min-h-[120px]">
          {state.step === 'publisher' && (
            <div className="space-y-2">
              <Label>Select Publisher</Label>
              {loadingPubs ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading...
                </div>
              ) : (
                <Select
                  value={
                    state.publisherId ? String(state.publisherId) : undefined
                  }
                  onValueChange={(v) =>
                    dispatch({ type: 'SET_PUBLISHER', id: Number(v) })
                  }
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Choose publisher..." />
                  </SelectTrigger>
                  <SelectContent>
                    {publishers.map((p) => (
                      <SelectItem key={p.id} value={String(p.id)}>
                        {p.display_name || p.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>
          )}

          {state.step === 'content-type' && (
            <div className="space-y-3">
              <Label>Content Type</Label>
              {PREDEFINED_TYPES.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() =>
                    dispatch({ type: 'SET_CONTENT_TYPE', value: t })
                  }
                  className={`flex w-full items-center gap-3 rounded-lg border p-3 text-left text-sm transition-colors ${state.contentType === t ? 'border-primary bg-primary/5' : 'hover:bg-muted'}`}
                >
                  <div
                    className={`h-2 w-2 rounded-full ${state.contentType === t ? 'bg-primary' : 'bg-muted-foreground/30'}`}
                  />
                  <div>
                    <div className="font-medium capitalize">{t}</div>
                    <div className="text-xs text-muted-foreground">
                      {CONTENT_TYPE_RULES[t].label}
                    </div>
                  </div>
                </button>
              ))}
              <button
                type="button"
                onClick={() =>
                  dispatch({ type: 'SET_CONTENT_TYPE', value: 'custom' })
                }
                className={`flex w-full items-center gap-3 rounded-lg border p-3 text-left text-sm transition-colors ${state.contentType === 'custom' ? 'border-primary bg-primary/5' : 'hover:bg-muted'}`}
              >
                <div
                  className={`h-2 w-2 rounded-full ${state.contentType === 'custom' ? 'bg-primary' : 'bg-muted-foreground/30'}`}
                />
                <span className="font-medium">Custom Type</span>
              </button>
              {state.contentType === 'custom' && (
                <Input
                  placeholder="e.g., worksheets"
                  value={state.customType}
                  onChange={(e) =>
                    dispatch({ type: 'SET_CUSTOM_TYPE', value: e.target.value })
                  }
                />
              )}
            </div>
          )}

          {state.step === 'files' && (
            <div className="space-y-3">
              <Label>Select Files</Label>
              <div
                className="flex min-h-[100px] cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-4 transition-colors hover:border-primary/50"
                onClick={() =>
                  document.getElementById('pub-upload-input')?.click()
                }
              >
                <Upload className="mb-2 h-6 w-6 text-muted-foreground" />
                <p className="text-sm text-muted-foreground">
                  {state.files.length
                    ? `${state.files.length} file(s) selected`
                    : 'Click to select files'}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Max: {Math.round(rules.maxSize / 1024 / 1024)}MB per file
                </p>
                <input
                  id="pub-upload-input"
                  type="file"
                  accept={rules.accept}
                  multiple={rules.multiple}
                  onChange={handleFileChange}
                  className="hidden"
                />
              </div>
              {state.files.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {state.files.map((f, i) => (
                    <Badge key={i} variant="secondary">
                      {f.name}
                    </Badge>
                  ))}
                </div>
              )}
              {fileError && (
                <p className="text-xs text-destructive">{fileError}</p>
              )}
              {effectiveType === 'books' && (
                <>
                  <div className="flex items-center gap-2">
                    <Checkbox
                      id="override"
                      checked={overrideExisting}
                      onCheckedChange={(v) => setOverrideExisting(v === true)}
                    />
                    <Label htmlFor="override" className="text-sm font-normal">
                      Override if book already exists
                    </Label>
                  </div>
                  <div className="flex items-center gap-2">
                    <Checkbox
                      id="auto-bundle"
                      checked={autoBundle}
                      onCheckedChange={(v) => setAutoBundle(v === true)}
                    />
                    <Label htmlFor="auto-bundle" className="text-sm font-normal">
                      Auto-create bundles after upload
                    </Label>
                  </div>
                </>
              )}
            </div>
          )}

          {state.step === 'uploading' && (
            <div className="flex flex-col items-center gap-3 py-4">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
              <p className="text-sm text-muted-foreground">
                Uploading {state.files.length} file(s)...
              </p>
              <Progress value={undefined} className="animate-pulse" />
            </div>
          )}

          {state.step === 'results' && (
            <div className="space-y-2">
              {state.results.map((r, i) => (
                <div key={i} className="flex items-center gap-2 text-sm">
                  {r.success ? (
                    <CheckCircle className="h-4 w-4 text-green-600 shrink-0" />
                  ) : (
                    <XCircle className="h-4 w-4 text-destructive shrink-0" />
                  )}
                  <span className="truncate flex-1">{r.filename}</span>
                  {r.error && (
                    <span className="text-xs text-destructive truncate">
                      {r.error}
                    </span>
                  )}
                </div>
              ))}
              {state.results.every((r) => r.success) && (
                <Alert>
                  <AlertDescription>All uploads completed!</AlertDescription>
                </Alert>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          {(state.step === 'content-type' || state.step === 'files') && (
            <Button variant="outline" onClick={prevStep} className="mr-auto">
              Back
            </Button>
          )}
          {state.step === 'results' ? (
            <Button onClick={onClose}>Close</Button>
          ) : (
            state.step !== 'uploading' && (
              <Button onClick={nextStep} disabled={!canProceed()}>
                {state.step === 'files' ? 'Upload' : 'Next'}
              </Button>
            )
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default PublisherUploadDialog;
