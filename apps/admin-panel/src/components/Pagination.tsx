import { ChevronLeft, ChevronRight } from 'lucide-react';

import { Button } from 'components/ui/button';

interface PaginationProps {
  /** 1-based current page. */
  page: number;
  /** Total number of pages (>= 1). */
  pageCount: number;
  onPageChange: (page: number) => void;
}

/** Build a compact page list with ellipses, e.g. 1 … 4 5 6 … 12. */
const buildPages = (page: number, pageCount: number): (number | '…')[] => {
  if (pageCount <= 7)
    return Array.from({ length: pageCount }, (_, i) => i + 1);
  const pages: (number | '…')[] = [1];
  const start = Math.max(2, page - 1);
  const end = Math.min(pageCount - 1, page + 1);
  if (start > 2) pages.push('…');
  for (let p = start; p <= end; p += 1) pages.push(p);
  if (end < pageCount - 1) pages.push('…');
  pages.push(pageCount);
  return pages;
};

const Pagination = ({ page, pageCount, onPageChange }: PaginationProps) => {
  if (pageCount <= 1) return null;
  const pages = buildPages(page, pageCount);

  return (
    <div className="flex items-center gap-1">
      <Button
        variant="outline"
        size="icon"
        className="h-8 w-8"
        onClick={() => onPageChange(page - 1)}
        disabled={page <= 1}
        aria-label="Previous page"
      >
        <ChevronLeft className="h-4 w-4" />
      </Button>
      {pages.map((p, i) =>
        p === '…' ? (
          <span
            key={`gap-${i}`}
            className="px-2 text-sm text-muted-foreground select-none"
          >
            …
          </span>
        ) : (
          <Button
            key={p}
            variant={p === page ? 'default' : 'outline'}
            size="icon"
            className="h-8 w-8"
            onClick={() => onPageChange(p)}
          >
            {p}
          </Button>
        )
      )}
      <Button
        variant="outline"
        size="icon"
        className="h-8 w-8"
        onClick={() => onPageChange(page + 1)}
        disabled={page >= pageCount}
        aria-label="Next page"
      >
        <ChevronRight className="h-4 w-4" />
      </Button>
    </div>
  );
};

export default Pagination;
