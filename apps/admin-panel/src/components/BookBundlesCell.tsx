import { useEffect, useRef, useState } from 'react';
import { Loader2, AlertCircle } from 'lucide-react';

import { Badge } from 'components/ui/badge';
import {
  createBundle,
  getBundleJobStatus,
  PLATFORM_LABELS,
  type StandalonePlatform,
} from 'lib/standaloneApps';

// Compact per-platform labels for the table column.
const SHORT: Record<StandalonePlatform, string> = {
  mac: 'mac',
  win: 'win',
  'win7-8': 'w7-8',
  linux: 'linux',
};

export interface BookBundleCoverage {
  present: string[];
  stale: string[];
}

/**
 * One table cell showing, per expected platform, whether the book has a
 * bundle:
 *   - present & current  -> subtle chip
 *   - present but stale   -> amber chip (click to rebuild)
 *   - missing             -> red "!" chip (click to build)
 * Clicking a missing/stale platform starts its bundle job and polls until done,
 * then calls onChanged() so the parent can refresh coverage.
 */
const BookBundlesCell = ({
  bookId,
  bookType,
  expected,
  coverage,
  token,
  tokenType,
  onChanged,
}: {
  bookId: number;
  bookType: 'standard' | 'pdf';
  expected: StandalonePlatform[];
  coverage: BookBundleCoverage | undefined;
  token: string | null;
  tokenType: string;
  onChanged: () => void;
}) => {
  const [building, setBuilding] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval>>();

  useEffect(() => () => clearInterval(pollRef.current), []);

  if (bookType !== 'standard') {
    return <span className="text-xs text-muted-foreground">—</span>;
  }
  if (!expected.length) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }

  const present = new Set(coverage?.present ?? []);
  const stale = new Set(coverage?.stale ?? []);

  const rebuild = (platform: StandalonePlatform) => {
    if (!token || building[platform]) return;
    setError(null);
    setBuilding((b) => ({ ...b, [platform]: true }));
    createBundle({ platform, book_id: bookId, force: true }, token, tokenType)
      .then((res) => {
        const jobId = res.job_id;
        if (!jobId) {
          setBuilding((b) => ({ ...b, [platform]: false }));
          onChanged();
          return;
        }
        clearInterval(pollRef.current);
        pollRef.current = setInterval(async () => {
          try {
            const s = await getBundleJobStatus(jobId, token, tokenType);
            if (s.status === 'completed') {
              clearInterval(pollRef.current);
              setBuilding((b) => ({ ...b, [platform]: false }));
              onChanged();
            } else if (s.status === 'failed') {
              clearInterval(pollRef.current);
              setBuilding((b) => ({ ...b, [platform]: false }));
              setError(s.error_message || `${PLATFORM_LABELS[platform]} build failed`);
            }
          } catch {
            clearInterval(pollRef.current);
            setBuilding((b) => ({ ...b, [platform]: false }));
            setError('Lost connection while building');
          }
        }, 3000);
      })
      .catch((e) => {
        setBuilding((b) => ({ ...b, [platform]: false }));
        setError(e instanceof Error ? e.message : 'Failed to start build');
      });
  };

  return (
    <div className="flex flex-wrap items-center gap-1">
      {expected.map((p) => {
        const label = SHORT[p];
        const title = PLATFORM_LABELS[p];
        if (building[p]) {
          return (
            <Badge key={p} variant="secondary" className="gap-1" title={`Building ${title}…`}>
              <Loader2 className="h-3 w-3 animate-spin" />
              {label}
            </Badge>
          );
        }
        if (!present.has(p)) {
          return (
            <button
              key={p}
              type="button"
              onClick={() => rebuild(p)}
              title={`${title}: missing — click to build`}
            >
              <Badge
                variant="destructive"
                className="gap-1 cursor-pointer hover:opacity-80"
              >
                <AlertCircle className="h-3 w-3" />
                {label}
              </Badge>
            </button>
          );
        }
        if (stale.has(p)) {
          return (
            <button
              key={p}
              type="button"
              onClick={() => rebuild(p)}
              title={`${title}: outdated — click to rebuild`}
            >
              <Badge variant="warning" className="gap-1 cursor-pointer hover:opacity-80">
                {label}
              </Badge>
            </button>
          );
        }
        return (
          <Badge key={p} variant="secondary" className="opacity-80" title={`${title}: ready`}>
            {label}
          </Badge>
        );
      })}
      {error && (
        <span className="text-xs text-destructive" title={error}>
          !
        </span>
      )}
    </div>
  );
};

export default BookBundlesCell;
