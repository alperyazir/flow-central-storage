import { useEffect, useState } from 'react';
import { Loader2, Server, Database, HardDrive, RefreshCw } from 'lucide-react';

import { Card, CardContent, CardHeader, CardTitle } from 'components/ui/card';
import { Badge } from 'components/ui/badge';
import { Button } from 'components/ui/button';
import { useAuthStore } from 'stores/auth';
import { buildApiUrl } from 'config/environment';
import { buildAuthHeaders } from 'lib/http';

interface VersionInfo {
  service: string;
  version: string;
}

interface HealthInfo {
  status: string;
  service: string;
  version: string;
  checks: Record<string, string>;
}

const SystemInfoPage = () => {
  const { token, tokenType } = useAuthStore();
  const tt = tokenType ?? 'Bearer';

  const [fcsVersion, setFcsVersion] = useState<VersionInfo | null>(null);
  const [fcsHealth, setFcsHealth] = useState<HealthInfo | null>(null);
  const [flVersion, setFlVersion] = useState<VersionInfo | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchAll = async () => {
    setLoading(true);
    const headers = buildAuthHeaders(token ?? '', tt);

    // FCS version
    try {
      const res = await fetch(buildApiUrl('/version'));
      if (res.ok) setFcsVersion(await res.json());
    } catch { /* ignore */ }

    // FCS health
    try {
      const res = await fetch(buildApiUrl('/health'));
      if (res.ok) setFcsHealth(await res.json());
    } catch { /* ignore */ }

    // FL version — try known FL API URL from env or relative
    const flApiUrl = import.meta.env.VITE_FL_API_URL;
    if (flApiUrl) {
      try {
        const res = await fetch(`${flApiUrl}/version`);
        if (res.ok) setFlVersion(await res.json());
      } catch { /* ignore */ }
    }

    setLoading(false);
  };

  useEffect(() => {
    fetchAll();
  }, [token]);

  const StatusBadge = ({ status }: { status: string }) => (
    <Badge variant={status === 'ok' || status === 'healthy' ? 'default' : 'destructive'}
      className={status === 'ok' || status === 'healthy' ? 'bg-green-600' : ''}>
      {status}
    </Badge>
  );

  if (loading) {
    return (
      <div className="flex justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">System Info</h1>
        <Button variant="outline" onClick={fetchAll} disabled={loading}>
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {/* FCS */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Server className="h-5 w-5" />
              Flow Central Storage
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex justify-between items-center">
              <span className="text-sm text-muted-foreground">Version</span>
              <Badge variant="outline">{fcsVersion?.version ?? 'unknown'}</Badge>
            </div>
            {fcsHealth && (
              <>
                <div className="flex justify-between items-center">
                  <span className="text-sm text-muted-foreground">Status</span>
                  <StatusBadge status={fcsHealth.status} />
                </div>
                {Object.entries(fcsHealth.checks).map(([key, status]) => (
                  <div key={key} className="flex justify-between items-center">
                    <span className="text-sm text-muted-foreground capitalize">{key}</span>
                    <StatusBadge status={status} />
                  </div>
                ))}
              </>
            )}
          </CardContent>
        </Card>

        {/* FL */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="h-5 w-5" />
              Flow Learn
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {flVersion ? (
              <div className="flex justify-between items-center">
                <span className="text-sm text-muted-foreground">Version</span>
                <Badge variant="outline">{flVersion.version}</Badge>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                Set VITE_FL_API_URL to show Flow Learn version
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default SystemInfoPage;
