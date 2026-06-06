import { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';

import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from 'components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from 'components/ui/select';
import { Button } from 'components/ui/button';
import { Input } from 'components/ui/input';
import { Label } from 'components/ui/label';
import { Textarea } from 'components/ui/textarea';

import { Separator } from 'components/ui/separator';
import { Alert, AlertDescription } from 'components/ui/alert';
import {
  createPublisher,
  fetchPublishers,
  updatePublisher,
  type Publisher,
} from 'lib/publishers';
import {
  getPublisherProcessingSettings,
  updatePublisherProcessingSettings,
} from 'lib/processing';

const AUDIO_LANGUAGES = ['en', 'tr', 'de', 'fr', 'es'];
const EMAIL_REGEX = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

interface PublisherFormDialogProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
  publisher?: Publisher | null;
  token: string | null;
  tokenType: string | null;
}

export function PublisherFormDialog({
  open,
  onClose,
  onSuccess,
  publisher,
  token,
  tokenType,
}: PublisherFormDialogProps) {
  const isEdit = !!publisher;

  const [name, setName] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [description, setDescription] = useState('');
  const [contactEmail, setContactEmail] = useState('');
  const [status, setStatus] = useState<string>('active');
  // Umbrella hierarchy: parent ("şemsiye") publisher. 'none' = top-level.
  const [parentPublisherId, setParentPublisherId] = useState<string>('none');
  const [parentOptions, setParentOptions] = useState<Publisher[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [emailError, setEmailError] = useState('');

  // AI settings
  const [aiAutoProcess, setAiAutoProcess] = useState<boolean | null>(null);
  const [aiPriority, setAiPriority] = useState<string>('');
  const [aiAudioLanguages, setAiAudioLanguages] = useState<string[]>([]);
  const [aiSettingsLoading, setAiSettingsLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    // Load publishers to populate the parent dropdown.
    if (token && tokenType) {
      fetchPublishers(token, tokenType)
        .then(setParentOptions)
        .catch(() => setParentOptions([]));
    }
    if (publisher) {
      setName(publisher.name);
      setDisplayName(publisher.display_name || '');
      setDescription(publisher.description || '');
      setContactEmail(publisher.contact_email || '');
      setStatus(publisher.status);
      setParentPublisherId(
        publisher.parent_publisher_id != null
          ? String(publisher.parent_publisher_id)
          : 'none'
      );
      // Load AI settings
      if (token && tokenType) {
        setAiSettingsLoading(true);
        getPublisherProcessingSettings(publisher.id, token, tokenType)
          .then((settings) => {
            setAiAutoProcess(settings.ai_auto_process_enabled);
            setAiPriority(settings.ai_processing_priority || '');
            setAiAudioLanguages(
              settings.ai_audio_languages?.split(',').filter(Boolean) || []
            );
          })
          .catch(() => {})
          .finally(() => setAiSettingsLoading(false));
      }
    } else {
      setName('');
      setDisplayName('');
      setDescription('');
      setContactEmail('');
      setStatus('active');
      setParentPublisherId('none');
      setAiAutoProcess(null);
      setAiPriority('');
      setAiAudioLanguages([]);
    }
    setError('');
    setEmailError('');
  }, [open, publisher, token, tokenType]);

  const validateEmail = (value: string) => {
    if (value && !EMAIL_REGEX.test(value)) {
      setEmailError('Invalid email address');
      return false;
    }
    setEmailError('');
    return true;
  };

  const handleSubmit = async () => {
    if (!name.trim()) {
      setError('Name is required');
      return;
    }
    if (!validateEmail(contactEmail)) return;
    if (!token || !tokenType) return;

    setSubmitting(true);
    setError('');

    try {
      const data = {
        name: name.trim(),
        display_name: displayName.trim() || undefined,
        description: description.trim() || undefined,
        contact_email: contactEmail.trim() || undefined,
        status,
        parent_publisher_id:
          parentPublisherId === 'none' ? null : Number(parentPublisherId),
      };

      let publisherId: number;
      if (isEdit && publisher) {
        await updatePublisher(publisher.id, data, token, tokenType);
        publisherId = publisher.id;
      } else {
        const created = await createPublisher(data, token, tokenType);
        publisherId = created.id;
      }

      // Save AI settings
      await updatePublisherProcessingSettings(
        publisherId,
        {
          ai_auto_process_enabled: aiAutoProcess,
          ai_processing_priority: aiPriority
            ? (aiPriority as 'high' | 'normal' | 'low')
            : null,
          ai_audio_languages: aiAudioLanguages.length
            ? aiAudioLanguages.join(',')
            : null,
        },
        token,
        tokenType
      ).catch(() => {});

      onSuccess();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save publisher');
    } finally {
      setSubmitting(false);
    }
  };

  const toggleLanguage = (lang: string) => {
    setAiAudioLanguages((prev) =>
      prev.includes(lang) ? prev.filter((l) => l !== lang) : [...prev, lang]
    );
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? 'Edit Publisher' : 'New Publisher'}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="pub-name">Name *</Label>
            <Input
              id="pub-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g., noor-publishing"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="pub-display">Display Name</Label>
            <Input
              id="pub-display"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="e.g., Noor Publishing"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="pub-desc">Description</Label>
            <Textarea
              id="pub-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="pub-email">Contact Email</Label>
            <Input
              id="pub-email"
              type="email"
              value={contactEmail}
              onChange={(e) => {
                setContactEmail(e.target.value);
                validateEmail(e.target.value);
              }}
            />
            {emailError && (
              <p className="text-xs text-destructive">{emailError}</p>
            )}
          </div>
          <div className="space-y-2">
            <Label>Status</Label>
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="active">Active</SelectItem>
                <SelectItem value="inactive">Inactive</SelectItem>
                <SelectItem value="suspended">Suspended</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>Parent Publisher</Label>
            <Select
              value={parentPublisherId}
              onValueChange={setParentPublisherId}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">None (top-level)</SelectItem>
                {parentOptions
                  .filter((p) => !publisher || p.id !== publisher.id)
                  .map((p) => (
                    <SelectItem key={p.id} value={String(p.id)}>
                      {p.display_name || p.name}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Group this publisher under an umbrella publisher. Leave as
              "None" for a standalone / top-level publisher.
            </p>
          </div>

          <Separator />

          <div className="space-y-4">
            <h4 className="text-sm font-medium">AI Processing Settings</h4>
            {aiSettingsLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading settings...
              </div>
            ) : (
              <>
                <div className="space-y-2">
                  <Label>Auto-Process on Upload</Label>
                  <Select
                    value={
                      aiAutoProcess === null
                        ? 'default'
                        : aiAutoProcess
                          ? 'enabled'
                          : 'disabled'
                    }
                    onValueChange={(v) =>
                      setAiAutoProcess(v === 'default' ? null : v === 'enabled')
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="default">
                        Use Global Default
                      </SelectItem>
                      <SelectItem value="enabled">Enabled</SelectItem>
                      <SelectItem value="disabled">Disabled</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Processing Priority</Label>
                  <Select
                    value={aiPriority || 'default'}
                    onValueChange={(v) =>
                      setAiPriority(v === 'default' ? '' : v)
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="default">
                        Use Global Default
                      </SelectItem>
                      <SelectItem value="high">High</SelectItem>
                      <SelectItem value="normal">Normal</SelectItem>
                      <SelectItem value="low">Low</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Audio Languages</Label>
                  <div className="flex flex-wrap gap-2">
                    {AUDIO_LANGUAGES.map((lang) => (
                      <Button
                        key={lang}
                        type="button"
                        variant={
                          aiAudioLanguages.includes(lang)
                            ? 'default'
                            : 'outline'
                        }
                        size="sm"
                        onClick={() => toggleLanguage(lang)}
                      >
                        {lang.toUpperCase()}
                      </Button>
                    ))}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={submitting || !name.trim()}>
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {isEdit ? 'Save Changes' : 'Create Publisher'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default PublisherFormDialog;
