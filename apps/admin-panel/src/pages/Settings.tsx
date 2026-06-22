import { useEffect, useState } from 'react';
import { Loader2, Check } from 'lucide-react';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from 'components/ui/card';
import { Button } from 'components/ui/button';
import { Switch } from 'components/ui/switch';
import { Label } from 'components/ui/label';
import { Alert, AlertDescription } from 'components/ui/alert';
import { useAuthStore } from 'stores/auth';
import { useSettingsStore } from 'stores/settings';

const SettingsPage = () => {
  const { token, tokenType } = useAuthStore();
  const tt = tokenType ?? 'Bearer';
  const { settings, loaded, load, save } = useSettingsStore();

  // Local draft so the toggle is responsive before saving.
  const [autoBundle, setAutoBundle] = useState(settings.default_auto_bundle);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(false);
  const [error, setError] = useState('');

  // Load settings once on mount, then sync local draft to the loaded value.
  useEffect(() => {
    if (token && !loaded) {
      load(token, tt);
    }
  }, [token, tt, loaded, load]);

  useEffect(() => {
    setAutoBundle(settings.default_auto_bundle);
  }, [settings.default_auto_bundle]);

  const dirty = autoBundle !== settings.default_auto_bundle;

  const handleSave = async () => {
    if (!token) return;
    setSaving(true);
    setError('');
    setSavedAt(false);
    try {
      await save({ default_auto_bundle: autoBundle }, token, tt);
      setSavedAt(true);
    } catch {
      setError('Failed to save settings. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6 py-2">
      <div>
        <h1 className="text-2xl font-semibold">Settings</h1>
        <p className="text-muted-foreground">Configure default behavior for the storage panel.</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Bundling</CardTitle>
          <CardDescription>
            Defaults applied when uploading books. These set the initial value of
            per-upload options — you can still override them for each upload.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between gap-4 rounded-lg border p-4">
            <div className="space-y-0.5">
              <Label htmlFor="default-auto-bundle" className="text-sm font-medium">
                Auto-create bundles after upload
              </Label>
              <p className="text-sm text-muted-foreground">
                When enabled, the "Auto-create bundles" checkbox is checked by default
                on the book upload dialog.
              </p>
            </div>
            <Switch
              id="default-auto-bundle"
              checked={autoBundle}
              onCheckedChange={(v) => {
                setAutoBundle(v);
                setSavedAt(false);
              }}
            />
          </div>

          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}

          <div className="flex items-center gap-3">
            <Button onClick={handleSave} disabled={!dirty || saving}>
              {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Save changes
            </Button>
            {savedAt && !dirty && (
              <span className="flex items-center gap-1 text-sm text-green-600">
                <Check className="h-4 w-4" /> Saved
              </span>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default SettingsPage;
