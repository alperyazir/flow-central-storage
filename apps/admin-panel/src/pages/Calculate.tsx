import { useEffect, useMemo, useState } from 'react';
import { Calculator, Download, Loader2, Search, X } from 'lucide-react';
import jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';

import { Button } from 'components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from 'components/ui/card';
import { Input } from 'components/ui/input';
import { Label } from 'components/ui/label';
import { Checkbox } from 'components/ui/checkbox';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from 'components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from 'components/ui/table';
import { useAuthStore } from 'stores/auth';
import { fetchPublishers, type Publisher } from 'lib/publishers';
import { fetchBooks, type BookRecord } from 'lib/books';
import { calculateBooks, type BookStats } from 'lib/calculate';

const CalculatePage = () => {
  const token = useAuthStore((s) => s.token);
  const tokenType = useAuthStore((s) => s.tokenType);

  const [publishers, setPublishers] = useState<Publisher[]>([]);
  const [selectedPublisherId, setSelectedPublisherId] = useState<number | null>(null);
  const [books, setBooks] = useState<BookRecord[]>([]);
  const [selectedBookIds, setSelectedBookIds] = useState<Set<number>>(new Set());
  const [stats, setStats] = useState<BookStats[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingBooks, setLoadingBooks] = useState(false);
  const [calculated, setCalculated] = useState(false);

  const [search, setSearch] = useState('');

  // Prices
  const [activityPrice, setActivityPrice] = useState<string>('80');
  const [pagePrice, setPagePrice] = useState<string>('25');
  const [gamePrice, setGamePrice] = useState<string>('200');

  // Load publishers
  useEffect(() => {
    if (!token) return;
    fetchPublishers(token, tokenType || 'Bearer')
      .then(setPublishers)
      .catch(() => {});
  }, [token, tokenType]);

  // Load books when publisher changes
  useEffect(() => {
    if (!token || !selectedPublisherId) {
      setBooks([]);
      setSelectedBookIds(new Set());
      setStats([]);
      setCalculated(false);
      return;
    }
    setLoadingBooks(true);
    fetchBooks(token, tokenType || 'Bearer')
      .then((all) => {
        const filtered = all.filter((b) => b.publisher_id === selectedPublisherId);
        setBooks(filtered);
        setSelectedBookIds(new Set());
        setStats([]);
        setCalculated(false);
      })
      .catch(() => {})
      .finally(() => setLoadingBooks(false));
  }, [token, tokenType, selectedPublisherId]);

  const filteredBooks = useMemo(() => {
    if (!search.trim()) return books;
    const q = search.toLowerCase();
    return books.filter((b) => b.book_name.toLowerCase().includes(q));
  }, [books, search]);

  const selectedBooks = useMemo(
    () => books.filter((b) => selectedBookIds.has(b.id)),
    [books, selectedBookIds]
  );

  const toggleBook = (id: number) => {
    setSelectedBookIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setCalculated(false);
  };

  const toggleAll = () => {
    if (selectedBookIds.size === books.length) {
      setSelectedBookIds(new Set());
    } else {
      setSelectedBookIds(new Set(books.map((b) => b.id)));
    }
    setCalculated(false);
  };

  const handleCalculate = async () => {
    if (!token || !selectedPublisherId || selectedBookIds.size === 0) return;
    setLoading(true);
    try {
      const result = await calculateBooks(
        selectedPublisherId,
        Array.from(selectedBookIds),
        token,
        tokenType || 'Bearer'
      );
      setStats(result);
      setCalculated(true);
    } catch {
      /* ignored */
    } finally {
      setLoading(false);
    }
  };

  // Collect all unique activity types across all books
  const allActivityTypes = useMemo(() => {
    const types = new Set<string>();
    for (const s of stats) {
      for (const t of Object.keys(s.activity_types)) {
        types.add(t);
      }
    }
    return Array.from(types).sort();
  }, [stats]);

  // Totals
  const totals = useMemo(() => {
    const t = {
      total_pages: 0,
      no_activity_pages: 0,
      total_activities: 0,
      games_count: 0,
      by_type: {} as Record<string, number>,
    };
    for (const s of stats) {
      t.total_pages += s.total_pages;
      t.no_activity_pages += s.no_activity_pages;
      t.total_activities += s.total_activities;
      t.games_count += s.games_count;
      for (const [type, count] of Object.entries(s.activity_types)) {
        t.by_type[type] = (t.by_type[type] || 0) + count;
      }
    }
    return t;
  }, [stats]);

  const actP = parseFloat(activityPrice) || 0;
  const pgP = parseFloat(pagePrice) || 0;
  const gmP = parseFloat(gamePrice) || 0;

  const fmtTL = (n: number) =>
    n.toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' TL';

  const grandTotal =
    totals.total_activities * actP +
    totals.no_activity_pages * pgP +
    totals.games_count * gmP;

  const selectedPublisher = publishers.find((p) => p.id === selectedPublisherId);

  const handleExportPdf = () => {
    const doc = new jsPDF({ orientation: 'landscape' });
    const pubName = selectedPublisher?.display_name || selectedPublisher?.name || '';

    doc.setFontSize(16);
    doc.text(`${pubName} - Book Cost Report`, 14, 18);
    doc.setFontSize(9);
    doc.text(`Generated: ${new Date().toLocaleDateString()}`, 14, 24);

    const headers = [
      'Book Name',
      'Total Pages',
      'No Activity',
      ...allActivityTypes,
      'Games',
      'Total Act.',
    ];

    const body = stats.map((s) => [
      s.book_name,
      s.total_pages,
      s.no_activity_pages,
      ...allActivityTypes.map((t) => s.activity_types[t] || 0),
      s.games_count,
      s.total_activities,
    ]);

    // Totals row
    body.push([
      'TOTAL',
      totals.total_pages,
      totals.no_activity_pages,
      ...allActivityTypes.map((t) => totals.by_type[t] || 0),
      totals.games_count,
      totals.total_activities,
    ]);

    const tableResult = autoTable(doc, {
      startY: 30,
      head: [headers],
      body,
      styles: { fontSize: 6, cellPadding: 1.5, overflow: 'linebreak' },
      headStyles: { fillColor: [94, 79, 0], fontSize: 6, cellPadding: 2 },
      columnStyles: { 0: { cellWidth: 30 } },
      foot: [],
      didParseCell: (data) => {
        if (data.row.index === body.length - 1) {
          data.cell.styles.fontStyle = 'bold';
        }
      },
    });

    const finalY = tableResult?.finalY ?? 150;
    const sy = finalY + 10;
    doc.setFontSize(10);
    doc.text('Price Summary', 14, sy);
    doc.setFontSize(9);
    doc.text(`Activity Price: ${fmtTL(actP)}`, 14, sy + 7);
    doc.text(`Empty Page Price: ${fmtTL(pgP)}`, 14, sy + 13);
    doc.text(`Game Price: ${fmtTL(gmP)}`, 14, sy + 19);
    doc.text(
      `Total: ${totals.total_activities} activities x ${fmtTL(actP)} + ${totals.no_activity_pages} pages x ${fmtTL(pgP)} + ${totals.games_count} games x ${fmtTL(gmP)} = ${fmtTL(grandTotal)}`,
      14,
      sy + 28
    );

    const now = new Date();
    const dd = String(now.getDate()).padStart(2, '0');
    const mm = String(now.getMonth() + 1).padStart(2, '0');
    const yy = String(now.getFullYear()).slice(-2);
    doc.save(`${pubName}_${mm}-${dd}-${yy}_cost_report.pdf`);
  };

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Calculate</h1>
      </div>

      {/* Publisher & Price Settings */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Publisher</CardTitle>
          </CardHeader>
          <CardContent>
            <Select
              value={selectedPublisherId ? String(selectedPublisherId) : undefined}
              onValueChange={(v) => setSelectedPublisherId(Number(v))}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select publisher..." />
              </SelectTrigger>
              <SelectContent>
                {publishers.map((p) => (
                  <SelectItem key={p.id} value={String(p.id)}>
                    {p.display_name || p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Activity Price</CardTitle>
          </CardHeader>
          <CardContent>
            <Input
              type="number"
              min="0"
              step="0.01"
              placeholder="0.00"
              value={activityPrice}
              onChange={(e) => setActivityPrice(e.target.value)}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Empty Page Price</CardTitle>
          </CardHeader>
          <CardContent>
            <Input
              type="number"
              min="0"
              step="0.01"
              placeholder="0.00"
              value={pagePrice}
              onChange={(e) => setPagePrice(e.target.value)}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">Game Price</CardTitle>
          </CardHeader>
          <CardContent>
            <Input
              type="number"
              min="0"
              step="0.01"
              placeholder="0.00"
              value={gamePrice}
              onChange={(e) => setGamePrice(e.target.value)}
            />
          </CardContent>
        </Card>
      </div>

      {/* Book Selection */}
      {selectedPublisherId && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {/* Left: book list with search */}
          <Card className="md:col-span-2">
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">Books ({books.length})</CardTitle>
                <Button variant="outline" size="sm" onClick={toggleAll}>
                  {selectedBookIds.size === books.length ? 'Deselect All' : 'Select All'}
                </Button>
              </div>
              <div className="relative mt-2">
                <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="Search books..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="pl-8"
                />
              </div>
            </CardHeader>
            <CardContent>
              {loadingBooks ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading books...
                </div>
              ) : books.length === 0 ? (
                <p className="text-sm text-muted-foreground">No books found.</p>
              ) : (
                <div className="max-h-[400px] overflow-y-auto space-y-1">
                  {filteredBooks.map((b) => (
                    <label
                      key={b.id}
                      className="flex items-center gap-2 rounded-md border p-2 text-sm cursor-pointer hover:bg-muted transition-colors"
                    >
                      <Checkbox
                        checked={selectedBookIds.has(b.id)}
                        onCheckedChange={() => toggleBook(b.id)}
                      />
                      <span className="truncate">{b.book_name}</span>
                    </label>
                  ))}
                  {filteredBooks.length === 0 && (
                    <p className="text-sm text-muted-foreground py-2">No matches.</p>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Right: selected books */}
          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">
                  Selected ({selectedBookIds.size})
                </CardTitle>
                <Button
                  size="sm"
                  onClick={handleCalculate}
                  disabled={loading || selectedBookIds.size === 0}
                >
                  {loading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Calculator className="h-4 w-4" />
                  )}
                  Calculate
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {selectedBooks.length === 0 ? (
                <p className="text-sm text-muted-foreground">No books selected.</p>
              ) : (
                <div className="max-h-[400px] overflow-y-auto space-y-1">
                  {selectedBooks.map((b) => (
                    <div
                      key={b.id}
                      className="flex items-center justify-between rounded-md border p-2 text-sm"
                    >
                      <span className="truncate">{b.book_name}</span>
                      <button
                        type="button"
                        onClick={() => toggleBook(b.id)}
                        className="text-muted-foreground hover:text-destructive shrink-0 ml-2"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* Results Table */}
      {calculated && stats.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm">Results</CardTitle>
              <Button variant="outline" size="sm" onClick={handleExportPdf}>
                <Download className="h-4 w-4" />
                Export PDF
              </Button>
            </div>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Book Name</TableHead>
                  <TableHead className="text-center">Total Pages</TableHead>
                  <TableHead className="text-center">No Activity</TableHead>
                  {allActivityTypes.map((t) => (
                    <TableHead key={t} className="text-center text-xs">
                      {t}
                    </TableHead>
                  ))}
                  <TableHead className="text-center">Games</TableHead>
                  <TableHead className="text-center">Total Act.</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {stats.map((s) => (
                  <TableRow key={s.book_id}>
                    <TableCell className="font-medium">{s.book_name}</TableCell>
                    <TableCell className="text-center">{s.total_pages}</TableCell>
                    <TableCell className="text-center">{s.no_activity_pages}</TableCell>
                    {allActivityTypes.map((t) => (
                      <TableCell key={t} className="text-center">
                        {s.activity_types[t] || 0}
                      </TableCell>
                    ))}
                    <TableCell className="text-center">{s.games_count}</TableCell>
                    <TableCell className="text-center font-medium">
                      {s.total_activities}
                    </TableCell>
                  </TableRow>
                ))}
                {/* Totals Row */}
                <TableRow className="font-bold border-t-2">
                  <TableCell>TOTAL</TableCell>
                  <TableCell className="text-center">{totals.total_pages}</TableCell>
                  <TableCell className="text-center">{totals.no_activity_pages}</TableCell>
                  {allActivityTypes.map((t) => (
                    <TableCell key={t} className="text-center">
                      {totals.by_type[t] || 0}
                    </TableCell>
                  ))}
                  <TableCell className="text-center">{totals.games_count}</TableCell>
                  <TableCell className="text-center">{totals.total_activities}</TableCell>
                </TableRow>
              </TableBody>
            </Table>

            {/* Price Summary */}
            {(actP > 0 || pgP > 0 || gmP > 0) && (
              <div className="mt-4 rounded-lg bg-muted p-4 text-sm space-y-1">
                <div className="flex justify-between">
                  <span>Activities: {totals.total_activities} x {fmtTL(actP)}</span>
                  <span className="font-medium">{fmtTL(totals.total_activities * actP)}</span>
                </div>
                <div className="flex justify-between">
                  <span>Empty Pages: {totals.no_activity_pages} x {fmtTL(pgP)}</span>
                  <span className="font-medium">{fmtTL(totals.no_activity_pages * pgP)}</span>
                </div>
                <div className="flex justify-between">
                  <span>Games: {totals.games_count} x {fmtTL(gmP)}</span>
                  <span className="font-medium">{fmtTL(totals.games_count * gmP)}</span>
                </div>
                <div className="flex justify-between border-t pt-1 text-base font-bold">
                  <span>Grand Total</span>
                  <span>{fmtTL(grandTotal)}</span>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default CalculatePage;
