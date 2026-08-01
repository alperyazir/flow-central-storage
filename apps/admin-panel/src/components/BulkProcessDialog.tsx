import { useEffect, useState } from 'react';
import { Loader2, Play } from 'lucide-react';

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
import { Label } from 'components/ui/label';
import { Alert, AlertDescription } from 'components/ui/alert';
import {
  bulkReprocess,
  type BulkReprocessFilters,
  type BulkReprocessResponse,
  type JobPriority,
  type ProcessingJobType,
} from 'lib/processing';

interface BulkProcessDialogProps {
  open: boolean;
  onClose: () => void;
  /** Hand-picked book ids; ignored when `filters` is set. */
  bookIds: number[];
  /** When set, the server queues every book matching the filter. */
  filters?: BulkReprocessFilters;
  /** How many books this will queue — the selection size or the filter total. */
  count: number;
  token: string | null;
  tokenType: string | null;
  onDone: (result: BulkReprocessResponse) => void;
}

export function BulkProcessDialog({
  open,
  onClose,
  bookIds,
  filters,
  count,
  token,
  tokenType,
  onDone,
}: BulkProcessDialogProps) {
  // Matches the per-book dialog's default: the chunked pipeline with audio.
  const [jobType, setJobType] = useState<ProcessingJobType>('full');
  const [priority, setPriority] = useState<JobPriority>('normal');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setJobType('full');
      setPriority('normal');
      setError(null);
    }
  }, [open]);

  const handleConfirm = async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const result = await bulkReprocess(
        {
          ...(filters ? { filters } : { book_ids: bookIds }),
          job_type: jobType,
          priority,
        },
        token,
        tokenType ?? 'Bearer'
      );
      onDone(result);
      onClose();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Failed to start bulk processing'
      );
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            Process {count} book{count === 1 ? '' : 's'}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            {filters
              ? 'Every book matching the current filters will be queued.'
              : 'The selected books will be queued.'}{' '}
            Books with a job already running are skipped. The worker processes
            a few books at a time in the order they are queued, so this can take
            a while.
          </p>

          <div className="space-y-2">
            <Label>Processing Type</Label>
            <Select
              value={jobType}
              onValueChange={(v) => setJobType(v as ProcessingJobType)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="full">
                  Full Process (Text + AI + Audio)
                </SelectItem>
                <SelectItem value="text_only">Text Extraction Only</SelectItem>
                <SelectItem value="llm_only">AI Analysis Only</SelectItem>
                <SelectItem value="audio_only">
                  Audio Generation Only
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label>Priority</Label>
            <Select
              value={priority}
              onValueChange={(v) => setPriority(v as JobPriority)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="high">High — jump the queue</SelectItem>
                <SelectItem value="normal">Normal</SelectItem>
                <SelectItem value="low">
                  Low — slower, keeps the normal queue free
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={loading}>
            Cancel
          </Button>
          <Button onClick={handleConfirm} disabled={loading || !count}>
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            Start Processing
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default BulkProcessDialog;
