import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export type OperationType = 'upload' | 'delete' | 'update';
export type OperationStatus =
  | 'pending'
  | 'in_progress'
  | 'awaiting_review'
  | 'completed'
  | 'failed';

export interface Operation {
  id: string;
  type: OperationType;
  bookName: string;
  status: OperationStatus;
  progress: number;
  timestamp: string; // ISO string for serialization
  detail?: string;
  error?: string;
  // In-place book file updates ('update'): the server job behind the
  // operation, so it can be reviewed or re-polled after the dialog closes
  // or the page reloads.
  bookId?: number;
  jobId?: string;
  phase?: 'uploading' | 'applying';
  summary?: string[];
}

type OperationFields = 'status' | 'progress' | 'detail' | 'error' | 'jobId' | 'phase' | 'summary';

const MAX_OPERATIONS = 10;

interface OperationsState {
  operations: Operation[];
  isExpanded: boolean;
  addOperation: (
    op: Pick<Operation, 'id' | 'type' | 'bookName'> & Partial<Pick<Operation, 'bookId' | 'phase'>>
  ) => void;
  updateOperation: (id: string, updates: Partial<Pick<Operation, OperationFields>>) => void;
  removeOperation: (id: string) => void;
  clearCompleted: () => void;
  toggleExpanded: () => void;
  setExpanded: (expanded: boolean) => void;
}

export const useOperationsStore = create<OperationsState>()(
  persist(
    (set) => ({
      operations: [],
      isExpanded: true,

      addOperation: (op) =>
        set((state) => {
          const newOp: Operation = {
            ...op,
            status: 'pending',
            progress: 0,
            timestamp: new Date().toISOString(),
          };
          const updated = [newOp, ...state.operations].slice(0, MAX_OPERATIONS);
          return { operations: updated, isExpanded: true };
        }),

      updateOperation: (id, updates) =>
        set((state) => ({
          operations: state.operations.map((op) =>
            op.id === id ? { ...op, ...updates } : op
          ),
        })),

      removeOperation: (id) =>
        set((state) => ({
          operations: state.operations.filter((op) => op.id !== id),
        })),

      clearCompleted: () =>
        set((state) => ({
          operations: state.operations.filter(
            (op) =>
              op.status === 'pending' ||
              op.status === 'in_progress' ||
              op.status === 'awaiting_review'
          ),
        })),

      toggleExpanded: () =>
        set((state) => ({ isExpanded: !state.isExpanded })),

      setExpanded: (expanded) => set({ isExpanded: expanded }),
    }),
    {
      name: 'fcs-operations',
      storage: createJSONStorage(() => localStorage),
      merge: (persisted, current) => {
        const state = persisted as Partial<OperationsState> | undefined;
        if (!state?.operations) return current;
        // Mark any in_progress/pending ops as failed (interrupted by refresh).
        // An update being applied runs on the server and is re-polled instead.
        const fixedOps = state.operations.map((op) =>
          (op.status === 'pending' || op.status === 'in_progress') &&
          !(op.type === 'update' && op.phase === 'applying' && op.jobId)
            ? { ...op, status: 'failed' as OperationStatus, error: 'Interrupted' }
            : op
        );
        return { ...current, operations: fixedOps, isExpanded: state.isExpanded ?? true };
      },
    }
  )
);
