import { ChangeEvent, useEffect, useState } from 'react';
import { useOperationsStore } from 'stores/operations';
import {
  Loader2,
  Upload,
  Download,
  Trash2,
  Monitor,
  Apple,
} from 'lucide-react';

import { Card, CardContent } from 'components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from 'components/ui/table';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from 'components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from 'components/ui/dialog';
import { Button } from 'components/ui/button';
import { Label } from 'components/ui/label';
import { Alert, AlertDescription } from 'components/ui/alert';
import { Progress } from 'components/ui/progress';
import { useAuthStore } from 'stores/auth';
import { ApiError } from 'lib/api';
import {
  listTemplates,
  uploadTemplate,
  deleteTemplate,
  STANDALONE_PLATFORMS,
  PLATFORM_LABELS,
  type StandalonePlatform,
  type TemplateInfo,
} from 'lib/standaloneApps';

const fmtBytes = (b: number) => {
  if (!b) return '0 B';
  const u = ['B', 'KB', 'MB', 'GB'];
  const e = Math.min(Math.floor(Math.log(b) / Math.log(1024)), u.length - 1);
  const v = b / Math.pow(1024, e);
  return `${v.toFixed(v >= 10 || e === 0 ? 0 : 1)} ${u[e]}`;
};
const fmtDate = (s: string) =>
  new Date(s).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
const platIcon = (p: string) =>
  p === 'mac' ? <Apple className="h-4 w-4" /> : <Monitor className="h-4 w-4" />;
const platLabel = (p: string) => PLATFORM_LABELS[p as StandalonePlatform] || p;

const StandaloneAppsManager = () => {
  const { token, tokenType } = useAuthStore();
  const tt = tokenType ?? 'Bearer';

  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadPlat, setUploadPlat] = useState('');
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadFb, setUploadFb] = useState<{
    type: 'success' | 'error';
    message: string;
  } | null>(null);
  const [delTarget, setDelTarget] = useState<TemplateInfo | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const r = await listTemplates(token, tt);
      setTemplates(r.templates);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [token]);

  const { addOperation, updateOperation } = useOperationsStore();

  const handleUpload = async () => {
    if (!uploadFile || !token || !uploadPlat) return;
    const opId = `upload-${Date.now()}-${uploadPlat}`;
    const opName = `App: ${uploadPlat}/${uploadFile.name}`;
    addOperation({ id: opId, type: 'upload', bookName: opName });
    updateOperation(opId, { status: 'in_progress', progress: 50, detail: 'Uploading...' });
    setUploadOpen(false);

    try {
      await uploadTemplate(uploadPlat, uploadFile, token, tt);
      updateOperation(opId, { status: 'completed', progress: 100, detail: 'Upload complete' });
      await load();
    } catch (e) {
      const msg = e instanceof ApiError
        ? String((e.body as Record<string, unknown>)?.detail || 'Upload failed')
        : e instanceof Error ? e.message : 'Upload failed';
      updateOperation(opId, { status: 'failed', error: msg });
    }
  };

  const handleDelete = async () => {
    if (!delTarget || !token) return;
    setDeleting(true);
    try {
      await deleteTemplate(delTarget.platform, token, tt);
      await load();
      setDelTarget(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div>
      <div className="flex justify-end mb-4">
        <Button
          onClick={() => {
            setUploadPlat('');
            setUploadFile(null);
            setUploadFb(null);
            setUploadOpen(true);
          }}
        >
          <Upload className="h-4 w-4" /> Upload Template
        </Button>
      </div>

      {error && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin" />
        </div>
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Platform</TableHead>
                  <TableHead>File Size</TableHead>
                  <TableHead>Uploaded</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {!templates.length ? (
                  <TableRow>
                    <TableCell
                      colSpan={4}
                      className="text-center py-12 text-muted-foreground"
                    >
                      No templates uploaded
                    </TableCell>
                  </TableRow>
                ) : (
                  templates.map((t) => (
                    <TableRow key={t.platform}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          {platIcon(t.platform)} {platLabel(t.platform)}
                        </div>
                      </TableCell>
                      <TableCell>{fmtBytes(t.file_size)}</TableCell>
                      <TableCell>{fmtDate(t.uploaded_at)}</TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            onClick={() =>
                              window.open(t.download_url, '_blank')
                            }
                          >
                            <Download className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7"
                            onClick={() => setDelTarget(t)}
                          >
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <Dialog
        open={uploadOpen}
        onOpenChange={(o) => !uploading && !o && setUploadOpen(false)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Upload App Template</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Platform</Label>
              <Select
                value={uploadPlat}
                onValueChange={setUploadPlat}
                disabled={uploading}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select platform..." />
                </SelectTrigger>
                <SelectContent>
                  {STANDALONE_PLATFORMS.map((p) => (
                    <SelectItem key={p} value={p}>
                      {PLATFORM_LABELS[p]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>ZIP File</Label>
              <input
                type="file"
                accept=".zip"
                onChange={(e: ChangeEvent<HTMLInputElement>) => {
                  setUploadFile(e.target.files?.[0] || null);
                  setUploadFb(null);
                }}
                disabled={uploading}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm file:border-0 file:bg-transparent file:text-sm file:font-medium"
              />
            </div>
            {uploading && (
              <Progress value={undefined} className="animate-pulse" />
            )}
            {uploadFb && (
              <Alert
                variant={uploadFb.type === 'error' ? 'destructive' : 'default'}
              >
                <AlertDescription>{uploadFb.message}</AlertDescription>
              </Alert>
            )}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setUploadOpen(false)}
              disabled={uploading}
            >
              Cancel
            </Button>
            <Button
              onClick={handleUpload}
              disabled={!uploadFile || !uploadPlat || uploading}
            >
              {uploading && <Loader2 className="h-4 w-4 animate-spin" />} Upload
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!delTarget}
        onOpenChange={() => !deleting && setDelTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Template?</DialogTitle>
            <DialogDescription>
              Delete the {delTarget ? platLabel(delTarget.platform) : ''}{' '}
              template? This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDelTarget(null)}
              disabled={deleting}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={deleting}
            >
              {deleting ? 'Deleting...' : 'Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default StandaloneAppsManager;
