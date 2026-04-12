declare module 'jspdf-autotable' {
  import { jsPDF } from 'jspdf';

  interface AutoTableOptions {
    startY?: number;
    head?: unknown[][];
    body?: unknown[][];
    foot?: unknown[][];
    styles?: Record<string, unknown>;
    headStyles?: Record<string, unknown>;
    columnStyles?: Record<number, Record<string, unknown>>;
    didParseCell?: (data: { row: { index: number }; cell: { styles: Record<string, unknown> } }) => void;
  }

  interface AutoTableResult {
    finalY: number;
  }

  export default function autoTable(doc: jsPDF, options: AutoTableOptions): AutoTableResult;
}
