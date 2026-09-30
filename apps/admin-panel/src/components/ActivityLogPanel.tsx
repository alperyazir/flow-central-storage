import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronDown, ChevronUp, Upload, Trash2, Check, X, Eye, RefreshCw } from 'lucide-react';

import { Button } from 'components/ui/button';
import { Progress } from 'components/ui/progress';
import { resumeBookFilesUpdates } from 'lib/bookFilesUpdateJobs';
import { useAuthStore } from 'stores/auth';
import {
  useOperationsStore,
  type Operation,
  type OperationStatus,
} from 'stores/operations';

const TYPE_LABELS: Record<Operation['type'], string> = {
  upload: 'Uploaded',
  delete: 'Deleted',
  update: 'Update files',
};

const formatTime = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

const statusIcon = (status: OperationStatus, type: Operation['type']) => {
  if (status === 'completed')
    return <Check className="h-4 w-4 text-green-500" />;
  if (status === 'failed') return <X className="h-4 w-4 text-red-500" />;
  if (status === 'awaiting_review') return <Eye className="h-4 w-4 text-amber-500" />;
  if (type === 'update')
    return <RefreshCw className="h-3.5 w-3.5 text-muted-foreground" />;
  if (type === 'upload')
    return <Upload className="h-3.5 w-3.5 text-muted-foreground" />;
  return <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />;
};

const OperationRow = ({ op }: { op: Operation }) => {
  const navigate = useNavigate();
  const isActive = op.status === 'pending' || op.status === 'in_progress';

  return (
    <div className="px-3 py-2 border-b border-border/50 last:border-0">
      <div className="flex items-center gap-2">
        {statusIcon(op.status, op.type)}
        <span className="text-xs text-muted-foreground">
          {TYPE_LABELS[op.type]}
        </span>
        <span className="text-sm font-medium truncate flex-1">
          {op.bookName}
        </span>
        <span className="text-xs text-muted-foreground whitespace-nowrap">
          {formatTime(op.timestamp)}
        </span>
      </div>
      {isActive && (
        <div className="mt-1.5">
          <Progress value={op.progress} className="h-1.5" />
          {op.detail && (
            <p className="text-xs text-muted-foreground mt-0.5">{op.detail}</p>
          )}
        </div>
      )}
      {op.status === 'failed' && op.error && (
        <p className="text-xs text-red-500 mt-1 truncate">{op.error}</p>
      )}
      {op.status === 'awaiting_review' && op.bookId !== undefined && (
        <div className="mt-1.5 flex items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">{op.detail}</p>
          <Button
            size="sm"
            variant="outline"
            className="h-6 px-2 text-xs"
            onClick={() => navigate(`/books/${op.bookId}?reviewUpdate=${encodeURIComponent(op.id)}`)}
          >
            Review
          </Button>
        </div>
      )}
      {op.status === 'completed' && op.summary && op.summary.length > 0 && (
        <ul className="mt-1 text-xs text-muted-foreground list-disc pl-4">
          {op.summary.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </div>
  );
};

const ActivityLogPanel = () => {
  const { operations, isExpanded, toggleExpanded } = useOperationsStore();
  const { token, tokenType } = useAuthStore();

  // An update being applied runs on the server: pick its progress back up
  // after a page reload.
  useEffect(() => {
    if (token) resumeBookFilesUpdates(token, tokenType ?? 'Bearer');
  }, [token, tokenType]);

  if (operations.length === 0) return null;

  const activeCount = operations.filter(
    (op) => op.status === 'pending' || op.status === 'in_progress'
  ).length;

  return (
    <div className="fixed bottom-4 right-4 z-50 w-80 bg-background border border-border rounded-lg shadow-lg overflow-hidden">
      {/* Header */}
      <button
        onClick={toggleExpanded}
        className="w-full flex items-center justify-between px-3 py-2 bg-muted/50 hover:bg-muted/80 transition-colors"
      >
        <span className="text-sm font-medium">
          Operations
          {activeCount > 0 && (
            <span className="ml-1.5 text-xs text-muted-foreground">
              ({activeCount} active)
            </span>
          )}
        </span>
        {isExpanded ? (
          <ChevronDown className="h-4 w-4" />
        ) : (
          <ChevronUp className="h-4 w-4" />
        )}
      </button>

      {/* Body */}
      {isExpanded && (
        <div className="max-h-64 overflow-y-auto">
          {operations.map((op) => (
            <OperationRow key={op.id} op={op} />
          ))}
        </div>
      )}
    </div>
  );
};

export default ActivityLogPanel;
