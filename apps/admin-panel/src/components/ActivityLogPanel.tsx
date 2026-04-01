import { ChevronDown, ChevronUp, Upload, Trash2, Check, X } from 'lucide-react';

import { Progress } from 'components/ui/progress';
import { Button } from 'components/ui/button';
import {
  useOperationsStore,
  type Operation,
  type OperationStatus,
} from 'stores/operations';

const formatTime = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

const statusIcon = (status: OperationStatus, type: Operation['type']) => {
  if (status === 'completed')
    return <Check className="h-4 w-4 text-green-500" />;
  if (status === 'failed') return <X className="h-4 w-4 text-red-500" />;
  if (type === 'upload')
    return <Upload className="h-3.5 w-3.5 text-muted-foreground" />;
  return <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />;
};

const OperationRow = ({ op }: { op: Operation }) => {
  const isActive = op.status === 'pending' || op.status === 'in_progress';

  return (
    <div className="px-3 py-2 border-b border-border/50 last:border-0">
      <div className="flex items-center gap-2">
        {statusIcon(op.status, op.type)}
        <span className="text-xs text-muted-foreground">
          {op.type === 'upload' ? 'Uploaded' : 'Deleted'}
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
    </div>
  );
};

const ActivityLogPanel = () => {
  const { operations, isExpanded, toggleExpanded } = useOperationsStore();

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
