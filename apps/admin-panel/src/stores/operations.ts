import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export type OperationType = 'upload' | 'delete';
export type OperationStatus =
  | 'pending'
  | 'in_progress'
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
}

const MAX_OPERATIONS = 10;

interface OperationsState {
  operations: Operation[];
  isExpanded: boolean;
  addOperation: (
    op: Pick<Operation, 'id' | 'type' | 'bookName'>
  ) => void;
  updateOperation: (
    id: string,
    updates: Partial<Pick<Operation, 'status' | 'progress' | 'detail' | 'error'>>
  ) => void;
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
            (op) => op.status === 'pending' || op.status === 'in_progress'
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
        // Mark any in_progress/pending ops as failed (interrupted by refresh)
        const fixedOps = state.operations.map((op) =>
          op.status === 'pending' || op.status === 'in_progress'
            ? { ...op, status: 'failed' as OperationStatus, error: 'Interrupted' }
            : op
        );
        return { ...current, operations: fixedOps, isExpanded: state.isExpanded ?? true };
      },
    }
  )
);
