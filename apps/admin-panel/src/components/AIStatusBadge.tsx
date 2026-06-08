import { Loader2, CheckCircle, XCircle, Clock, AlertTriangle } from 'lucide-react';

import { Badge } from 'components/ui/badge';
import type { AIProcessingStatus } from 'lib/books';

type Status = AIProcessingStatus | null | undefined;

const fmt = (s?: string | null) =>
  s
    ? new Date(s).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : undefined;

/**
 * Compact badge for a book's AI processing state. Mirrors the persistent
 * `ai_processing_status` from the Book row (queued/processing/completed/
 * partial/failed); renders nothing when the book has never been processed.
 */
const AIStatusBadge = ({
  status,
  processedAt,
  className,
}: {
  status: Status;
  processedAt?: string | null;
  className?: string;
}) => {
  if (!status) return null;

  const when = fmt(processedAt);

  switch (status) {
    case 'queued':
      return (
        <Badge variant="secondary" className={className} title="AI processing queued">
          <Clock className="h-3 w-3" /> Queued
        </Badge>
      );
    case 'processing':
      return (
        <Badge variant="secondary" className={className} title="AI processing running">
          <Loader2 className="h-3 w-3 animate-spin" /> Processing
        </Badge>
      );
    case 'completed':
      return (
        <Badge
          variant="success"
          className={className}
          title={when ? `AI processed ${when}` : 'AI processing completed'}
        >
          <CheckCircle className="h-3 w-3" /> AI ready
        </Badge>
      );
    case 'partial':
      return (
        <Badge
          variant="warning"
          className={className}
          title={when ? `Partly processed ${when}` : 'Some AI stages failed'}
        >
          <AlertTriangle className="h-3 w-3" /> Partial
        </Badge>
      );
    case 'failed':
      return (
        <Badge variant="destructive" className={className} title="AI processing failed">
          <XCircle className="h-3 w-3" /> Failed
        </Badge>
      );
    default:
      return null;
  }
};

export default AIStatusBadge;
