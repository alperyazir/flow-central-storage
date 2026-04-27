import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import {
  ArrowLeft,
  Loader2,
  CheckCircle,
  XCircle,
  Clock,
  Play,
  Square,
  Trash2,
  Inbox,
} from 'lucide-react';

import { Card, CardContent, CardHeader, CardTitle } from 'components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from 'components/ui/tabs';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from 'components/ui/table';
import { Button } from 'components/ui/button';
import { Badge } from 'components/ui/badge';
import { Checkbox } from 'components/ui/checkbox';
import { Alert, AlertDescription } from 'components/ui/alert';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from 'components/ui/dialog';
import ActivityRenderer from 'components/ActivityRenderer';
import {
  getAIMetadata,
  getAIModules,
  getAIModuleDetail,
  getAIVocabulary,
  type AIMetadata,
  type ModuleSummary,
  type ModuleDetail,
  type VocabularyWord,
} from 'lib/processing';
import {
  listAIContent,
  getAIContent,
  deleteAIContent,
  type ManifestRead,
  type AIContentRead,
} from 'lib/aiContent';
import { fetchBook } from 'lib/books';
import { useAuthStore } from 'stores/auth';

const fmtDate = (s: string | null) =>
  s
    ? new Date(s).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '—';

const stageIcon = (s: string) => {
  if (s === 'completed')
    return <CheckCircle className="h-4 w-4 text-green-600" />;
  if (s === 'failed') return <XCircle className="h-4 w-4 text-destructive" />;
  if (s === 'processing')
    return <Loader2 className="h-4 w-4 animate-spin text-primary" />;
  return <Clock className="h-4 w-4 text-muted-foreground" />;
};

const difficultyColor = (d: string | null) => {
  if (!d) return 'secondary' as const;
  if (d.toLowerCase() === 'easy') return 'success' as const;
  if (d.toLowerCase() === 'hard') return 'destructive' as const;
  return 'warning' as const;
};

const AIDataPage = () => {
  const { bookId: bookIdParam } = useParams<{ bookId: string }>();
  const bookId = bookIdParam ? Number(bookIdParam) : NaN;
  const navigate = useNavigate();
  const location = useLocation();
  const { token, tokenType } = useAuthStore();
  const tt = tokenType ?? 'Bearer';

  const initialTitle = (location.state as { bookTitle?: string } | null)
    ?.bookTitle;
  const [bookTitle, setBookTitle] = useState<string>(initialTitle ?? '');

  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState('metadata');
  const [metadata, setMetadata] = useState<AIMetadata | null>(null);
  const [modules, setModules] = useState<ModuleSummary[]>([]);
  const [expandedModule, setExpandedModule] = useState<number | null>(null);
  const [moduleDetails, setModuleDetails] = useState<
    Record<number, ModuleDetail>
  >({});
  const [vocabulary, setVocabulary] = useState<VocabularyWord[]>([]);
  const [playingWord, setPlayingWord] = useState<string | null>(null);
  const [audioEl, setAudioEl] = useState<HTMLAudioElement | null>(null);
  const [activities, setActivities] = useState<ManifestRead[]>([]);
  const [selectedActivity, setSelectedActivity] = useState<string | null>(null);
  const [activityDetails, setActivityDetails] = useState<
    Record<string, AIContentRead>
  >({});
  const [activityLoading, setActivityLoading] = useState(false);
  const [bulkSelected, setBulkSelected] = useState<Set<string>>(new Set());
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    if (!token || !bookId || Number.isNaN(bookId)) return;
    setLoading(true);
    try {
      const [meta, mods, vocab, acts] = await Promise.all([
        getAIMetadata(bookId, token, tt).catch(() => null),
        getAIModules(bookId, token, tt).catch(() => ({
          modules: [] as ModuleSummary[],
        })),
        getAIVocabulary(bookId, token, tt).catch(() => ({
          words: [] as VocabularyWord[],
        })),
        listAIContent(bookId, token, tt).catch(() => [] as ManifestRead[]),
      ]);
      setMetadata(meta);
      setModules(mods.modules);
      setVocabulary(vocab.words);
      setActivities(acts);
    } finally {
      setLoading(false);
    }
  }, [bookId, token, tt]);

  useEffect(() => {
    loadData();
    return () => {
      if (audioEl) audioEl.pause();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadData]);

  useEffect(() => {
    if (bookTitle || !token || !bookId || Number.isNaN(bookId)) return;
    fetchBook(bookId, token, tt)
      .then((b) => setBookTitle(b.book_title || b.book_name))
      .catch(() => {});
  }, [bookId, bookTitle, token, tt]);

  const toggleModule = async (moduleId: number) => {
    if (expandedModule === moduleId) {
      setExpandedModule(null);
      return;
    }
    setExpandedModule(moduleId);
    if (!moduleDetails[moduleId] && token) {
      try {
        const d = await getAIModuleDetail(bookId, moduleId, token, tt);
        setModuleDetails((p) => ({ ...p, [moduleId]: d }));
      } catch {
        /* ignored */
      }
    }
  };

  const playAudio = (word: string, src: string | null) => {
    if (audioEl) {
      audioEl.pause();
      setAudioEl(null);
    }
    if (playingWord === word || !src) {
      setPlayingWord(null);
      return;
    }
    const audio = new Audio(src);
    audio.onended = () => {
      setPlayingWord(null);
      setAudioEl(null);
    };
    audio.play().catch(() => {});
    setPlayingWord(word);
    setAudioEl(audio);
  };

  const selectActivity = async (contentId: string) => {
    setSelectedActivity(contentId);
    if (!activityDetails[contentId] && token) {
      setActivityLoading(true);
      try {
        const d = await getAIContent(bookId, contentId, token, tt);
        setActivityDetails((p) => ({ ...p, [contentId]: d }));
      } catch {
        /* ignored */
      } finally {
        setActivityLoading(false);
      }
    }
  };

  const toggleBulk = (contentId: string) => {
    setBulkSelected((prev) => {
      const next = new Set(prev);
      if (next.has(contentId)) next.delete(contentId);
      else next.add(contentId);
      return next;
    });
  };

  const toggleBulkAll = () => {
    if (bulkSelected.size === activities.length) {
      setBulkSelected(new Set());
    } else {
      setBulkSelected(new Set(activities.map((a) => a.content_id)));
    }
  };

  const handleBulkDelete = async () => {
    if (!token || bulkSelected.size === 0) return;
    setDeleting(true);
    setDeleteError(null);
    const ids = Array.from(bulkSelected);
    try {
      const results = await Promise.allSettled(
        ids.map((id) => deleteAIContent(bookId, id, token, tt))
      );
      const succeeded = new Set<string>();
      const failures: string[] = [];
      results.forEach((r, i) => {
        if (r.status === 'fulfilled') {
          succeeded.add(ids[i]);
        } else {
          const reason = r.reason as { message?: string; status?: number };
          failures.push(reason?.message || `Failed to delete ${ids[i]}`);
        }
      });
      if (succeeded.size > 0) {
        setActivities((p) => p.filter((a) => !succeeded.has(a.content_id)));
        if (selectedActivity && succeeded.has(selectedActivity)) {
          setSelectedActivity(null);
        }
      }
      setBulkSelected(
        new Set(Array.from(bulkSelected).filter((id) => !succeeded.has(id)))
      );
      if (failures.length > 0) {
        setDeleteError(
          `${failures.length} of ${ids.length} failed: ${failures[0]}`
        );
      } else {
        setBulkConfirmOpen(false);
      }
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : 'Delete failed');
    } finally {
      setDeleting(false);
    }
  };

  if (Number.isNaN(bookId)) {
    return (
      <div className="p-6">
        <Alert variant="destructive">
          <AlertDescription>Invalid book id.</AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="space-y-6 p-2 md:p-4">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => navigate('/processing')}
            title="Back"
          >
            <ArrowLeft className="h-5 w-5" />
          </Button>
          <div>
            <p className="text-xs uppercase tracking-wide text-muted-foreground">
              AI Data
            </p>
            <h1 className="text-2xl font-semibold">
              {bookTitle || `Book #${bookId}`}
            </h1>
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={loadData} disabled={loading}>
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            'Refresh'
          )}
        </Button>
      </div>

      {loading ? (
        <div className="flex justify-center py-16">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
        </div>
      ) : (
        <Tabs value={tab} onValueChange={setTab} className="space-y-4">
          <TabsList>
            <TabsTrigger value="metadata">Metadata</TabsTrigger>
            <TabsTrigger value="modules">
              Modules ({modules.length})
            </TabsTrigger>
            <TabsTrigger value="vocabulary">
              Vocabulary ({vocabulary.length})
            </TabsTrigger>
            <TabsTrigger value="activities">
              Activities ({activities.length})
            </TabsTrigger>
          </TabsList>

          <TabsContent value="metadata">
            {!metadata ? (
              <p className="py-4 text-center text-sm text-muted-foreground">
                No metadata available
              </p>
            ) : (
              <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Overview</CardTitle>
                  </CardHeader>
                  <CardContent className="grid grid-cols-2 gap-4 text-sm md:grid-cols-3">
                    <div>
                      <span className="text-muted-foreground">Status:</span>{' '}
                      <Badge>{metadata.processing_status}</Badge>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Pages:</span>{' '}
                      {metadata.total_pages}
                    </div>
                    <div>
                      <span className="text-muted-foreground">Modules:</span>{' '}
                      {metadata.total_modules}
                    </div>
                    <div>
                      <span className="text-muted-foreground">Vocabulary:</span>{' '}
                      {metadata.total_vocabulary}
                    </div>
                    <div>
                      <span className="text-muted-foreground">Audio:</span>{' '}
                      {metadata.total_audio_files}
                    </div>
                    <div>
                      <span className="text-muted-foreground">Language:</span>{' '}
                      {metadata.primary_language}
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">
                      Processing Stages
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="p-0">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Stage</TableHead>
                          <TableHead>Status</TableHead>
                          <TableHead>Completed</TableHead>
                          <TableHead>Error</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {Object.entries(metadata.stages).map(
                          ([name, stage]) => (
                            <TableRow key={name}>
                              <TableCell className="font-medium capitalize text-xs">
                                {name.replace(/_/g, ' ')}
                              </TableCell>
                              <TableCell>
                                <div className="flex items-center gap-1.5 text-xs">
                                  {stageIcon(stage.status)} {stage.status}
                                </div>
                              </TableCell>
                              <TableCell className="text-xs">
                                {fmtDate(stage.completed_at)}
                              </TableCell>
                              <TableCell className="text-xs text-destructive max-w-[200px] truncate">
                                {stage.error_message || '—'}
                              </TableCell>
                            </TableRow>
                          )
                        )}
                      </TableBody>
                    </Table>
                  </CardContent>
                </Card>
              </div>
            )}
          </TabsContent>

          <TabsContent value="modules">
            {!modules.length ? (
              <p className="py-4 text-center text-sm text-muted-foreground">
                No modules
              </p>
            ) : (
              <Card>
                <CardContent className="space-y-1 p-2">
                  {modules.map((m) => (
                    <div key={m.module_id}>
                      <button
                        className="flex w-full items-center justify-between rounded-md p-3 text-sm hover:bg-muted transition-colors text-left"
                        onClick={() => toggleModule(m.module_id)}
                      >
                        <div>
                          <span className="font-medium">{m.title}</span>
                          <span className="ml-2 text-xs text-muted-foreground">
                            Pages: {m.pages.join(', ')} | Words: {m.word_count}
                          </span>
                        </div>
                        <span className="text-xs">
                          {expandedModule === m.module_id ? '▼' : '▶'}
                        </span>
                      </button>
                      {expandedModule === m.module_id &&
                        moduleDetails[m.module_id] && (
                          <div className="ml-4 space-y-2 border-l pl-4 pb-2 text-sm">
                            {moduleDetails[m.module_id].topics.length > 0 && (
                              <div className="flex flex-wrap gap-1">
                                {moduleDetails[m.module_id].topics.map((t) => (
                                  <Badge
                                    key={t}
                                    variant="outline"
                                    className="text-xs"
                                  >
                                    {t}
                                  </Badge>
                                ))}
                              </div>
                            )}
                            {moduleDetails[m.module_id].grammar_points.length >
                              0 && (
                              <div className="flex flex-wrap gap-1">
                                {moduleDetails[m.module_id].grammar_points.map(
                                  (g) => (
                                    <Badge
                                      key={g}
                                      variant="secondary"
                                      className="text-xs"
                                    >
                                      {g}
                                    </Badge>
                                  )
                                )}
                              </div>
                            )}
                            {moduleDetails[m.module_id].summary && (
                              <p className="text-xs text-muted-foreground">
                                {moduleDetails[m.module_id].summary}
                              </p>
                            )}
                            {moduleDetails[m.module_id].text && (
                              <pre className="max-h-[200px] overflow-auto rounded-md bg-muted p-2 text-xs whitespace-pre-wrap">
                                {moduleDetails[m.module_id].text.slice(0, 1000)}
                              </pre>
                            )}
                          </div>
                        )}
                    </div>
                  ))}
                </CardContent>
              </Card>
            )}
          </TabsContent>

          <TabsContent value="vocabulary">
            {!vocabulary.length ? (
              <p className="py-4 text-center text-sm text-muted-foreground">
                No vocabulary
              </p>
            ) : (
              <Card>
                <CardContent className="p-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Word</TableHead>
                        <TableHead>Translation</TableHead>
                        <TableHead>POS</TableHead>
                        <TableHead>Level</TableHead>
                        <TableHead>Audio</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {vocabulary.map((w) => (
                        <TableRow key={w.id}>
                          <TableCell className="font-medium text-sm">
                            {w.word}
                          </TableCell>
                          <TableCell className="text-sm">
                            {w.translation}
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline" className="text-xs">
                              {w.part_of_speech}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <Badge variant="default" className="text-xs">
                              {w.level}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <div className="flex gap-1">
                              {w.audio?.word && (
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-7 w-7"
                                  onClick={() =>
                                    playAudio(`word-${w.id}`, w.audio!.word)
                                  }
                                >
                                  {playingWord === `word-${w.id}` ? (
                                    <Square className="h-3 w-3" />
                                  ) : (
                                    <Play className="h-3 w-3" />
                                  )}
                                </Button>
                              )}
                              {w.audio?.translation && (
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-7 w-7"
                                  onClick={() =>
                                    playAudio(
                                      `trans-${w.id}`,
                                      w.audio!.translation
                                    )
                                  }
                                >
                                  {playingWord === `trans-${w.id}` ? (
                                    <Square className="h-3 w-3 text-secondary" />
                                  ) : (
                                    <Play className="h-3 w-3 text-secondary" />
                                  )}
                                </Button>
                              )}
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            )}
          </TabsContent>

          <TabsContent value="activities">
            {!activities.length ? (
              <p className="py-4 text-center text-sm text-muted-foreground">
                No activities
              </p>
            ) : (
              <div className="grid gap-4 lg:grid-cols-[minmax(320px,400px)_1fr]">
                <Card className="flex flex-col">
                  <CardHeader className="border-b py-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <Checkbox
                          checked={
                            bulkSelected.size === activities.length &&
                            activities.length > 0
                          }
                          onCheckedChange={toggleBulkAll}
                          aria-label="Select all"
                        />
                        <CardTitle className="text-sm">
                          {bulkSelected.size > 0
                            ? `${bulkSelected.size} selected`
                            : `Activities (${activities.length})`}
                        </CardTitle>
                      </div>
                      {bulkSelected.size > 0 && (
                        <Button
                          variant="destructive"
                          size="sm"
                          onClick={() => setBulkConfirmOpen(true)}
                          disabled={deleting}
                        >
                          <Trash2 className="h-3.5 w-3.5 mr-1" />
                          Delete ({bulkSelected.size})
                        </Button>
                      )}
                    </div>
                  </CardHeader>
                  <CardContent className="p-0 max-h-[70vh] overflow-y-auto">
                    {activities.map((a) => {
                      const isSelected = selectedActivity === a.content_id;
                      const isChecked = bulkSelected.has(a.content_id);
                      return (
                        <div
                          key={a.content_id}
                          className={`flex items-start gap-2 border-b p-3 cursor-pointer transition-colors ${
                            isSelected ? 'bg-muted' : 'hover:bg-muted/50'
                          }`}
                          onClick={() => selectActivity(a.content_id)}
                        >
                          <div
                            onClick={(e) => e.stopPropagation()}
                            className="pt-0.5"
                          >
                            <Checkbox
                              checked={isChecked}
                              onCheckedChange={() => toggleBulk(a.content_id)}
                              aria-label={`Select ${a.title}`}
                            />
                          </div>
                          <div className="flex-1 min-w-0 space-y-1">
                            <div className="font-medium text-sm truncate">
                              {a.title}
                            </div>
                            <div className="flex flex-wrap items-center gap-1">
                              <Badge variant="outline" className="text-xs">
                                {a.activity_type}
                              </Badge>
                              {a.difficulty && (
                                <Badge
                                  variant={difficultyColor(a.difficulty)}
                                  className="text-xs"
                                >
                                  {a.difficulty}
                                </Badge>
                              )}
                              <span className="text-xs text-muted-foreground">
                                {a.item_count} items
                              </span>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </CardContent>
                </Card>

                <Card className="flex flex-col min-h-[400px]">
                  {!selectedActivity ? (
                    <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center text-muted-foreground">
                      <Inbox className="h-10 w-10" />
                      <p className="text-sm">
                        Select an activity from the list to view its content
                      </p>
                    </div>
                  ) : (
                    (() => {
                      const a = activities.find(
                        (x) => x.content_id === selectedActivity
                      );
                      if (!a) return null;
                      return (
                        <>
                          <CardHeader className="border-b">
                            <div className="flex items-start justify-between gap-3">
                              <div className="space-y-2 min-w-0">
                                <CardTitle className="text-base truncate">
                                  {a.title}
                                </CardTitle>
                                <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                                  <Badge variant="outline" className="text-xs">
                                    {a.activity_type.replace(/[_-]/g, ' ')}
                                  </Badge>
                                  {a.difficulty && (
                                    <Badge
                                      variant={difficultyColor(a.difficulty)}
                                      className="text-xs"
                                    >
                                      {a.difficulty}
                                    </Badge>
                                  )}
                                  <span>Language: {a.language}</span>
                                  {a.has_audio && (
                                    <Badge variant="outline" className="text-xs">
                                      Audio
                                    </Badge>
                                  )}
                                  {a.has_passage && (
                                    <Badge variant="outline" className="text-xs">
                                      Passage
                                    </Badge>
                                  )}
                                  <span>{a.item_count} items</span>
                                  {a.created_at && (
                                    <span>{fmtDate(a.created_at)}</span>
                                  )}
                                </div>
                              </div>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8 shrink-0"
                                onClick={() => {
                                  setBulkSelected(new Set([a.content_id]));
                                  setBulkConfirmOpen(true);
                                }}
                                title="Delete"
                              >
                                <Trash2 className="h-4 w-4 text-destructive" />
                              </Button>
                            </div>
                          </CardHeader>
                          <CardContent className="p-4">
                            {activityLoading && !activityDetails[a.content_id] ? (
                              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                <Loader2 className="h-4 w-4 animate-spin" />
                                Loading...
                              </div>
                            ) : activityDetails[a.content_id] ? (
                              <ActivityRenderer
                                content={activityDetails[a.content_id]}
                                bookId={bookId}
                                token={token!}
                                tokenType={tt}
                              />
                            ) : null}
                          </CardContent>
                        </>
                      );
                    })()
                  )}
                </Card>
              </div>
            )}
          </TabsContent>
        </Tabs>
      )}

      <Dialog
        open={bulkConfirmOpen}
        onOpenChange={(o) => {
          if (!o && !deleting) {
            setBulkConfirmOpen(false);
            setDeleteError(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete activities?</DialogTitle>
            <DialogDescription>
              Delete {bulkSelected.size}{' '}
              {bulkSelected.size === 1 ? 'activity' : 'activities'}? This cannot
              be undone.
            </DialogDescription>
          </DialogHeader>
          {deleteError && (
            <Alert variant="destructive">
              <AlertDescription className="text-xs">
                {deleteError}
              </AlertDescription>
            </Alert>
          )}
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setBulkConfirmOpen(false);
                setDeleteError(null);
              }}
              disabled={deleting}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleBulkDelete}
              disabled={deleting}
            >
              {deleting ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Deleting...
                </>
              ) : (
                'Delete'
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default AIDataPage;
