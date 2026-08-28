import { useCallback, useEffect, useState } from 'react';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api, { formatApiError } from '@/lib/api';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ScrollArea } from '@/components/ui/scroll-area';
import { AlertCircle, CheckCircle2, Clock3, DatabaseZap, Eye, Plug, RefreshCw, RotateCw, XCircle } from 'lucide-react';

const MODE_LABELS = {
  core: 'Core model',
  pos_admin: 'POS admin records',
  qr_codes: 'QR codes',
};

const SYNC_STATUS_CLASSES = {
  success: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  partial: 'bg-amber-50 text-amber-700 border-amber-200',
  failed: 'bg-red-50 text-red-700 border-red-200',
};

const POS_BRIDGE_TIMEOUT_MS = 60000;

function formatSyncTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString();
}

function errorText(error) {
  if (!error) return '';
  if (typeof error === 'string') return error;
  if (error.reason) return error.reason;
  if (error.message) return error.message;
  if (error.detail) return typeof error.detail === 'object' ? JSON.stringify(error.detail) : String(error.detail);
  return JSON.stringify(error);
}

export default function POSBridgePage() {
  const { selectedBusiness } = useBusiness();
  const [config, setConfig] = useState(null);
  const [resources, setResources] = useState([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState('');
  const [lastResult, setLastResult] = useState(null);
  const [previewing, setPreviewing] = useState('');
  const [livePreview, setLivePreview] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = selectedBusiness ? { business_id: selectedBusiness.id } : {};
      const [{ data: bridgeConfig }, { data: bridgeResources }] = await Promise.all([
        api.get('/pos-bridge/config'),
        api.get('/pos-bridge/resources', { params }),
      ]);
      setConfig(bridgeConfig);
      setResources(bridgeResources);
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setLoading(false);
    }
  }, [selectedBusiness]);

  useEffect(() => { load(); }, [load]);

  const syncResource = async (resource) => {
    setSyncing(resource);
    try {
      const params = selectedBusiness ? { business_id: selectedBusiness.id } : {};
      const { data } = await api.post(`/pos-bridge/sync/${resource}`, null, { params, timeout: POS_BRIDGE_TIMEOUT_MS });
      setLastResult(data);
      toast.success(`${data.count || 0} ${resource} synced`);
      load();
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSyncing('');
    }
  };

  const syncAll = async () => {
    setSyncing('all');
    const results = {};
    setLastResult({ results });
    try {
      const params = selectedBusiness ? { business_id: selectedBusiness.id } : {};
      for (const resource of resources) {
        setSyncing(`all:${resource.key}`);
        try {
          const { data } = await api.post(`/pos-bridge/sync/${resource.key}`, null, { params, timeout: POS_BRIDGE_TIMEOUT_MS });
          results[resource.key] = data;
        } catch (err) {
          results[resource.key] = {
            resource: resource.key,
            status: 'failed',
            count: 0,
            error_count: 1,
            errors: [{ reason: formatApiError(err) }],
          };
        }
        setLastResult({ results: { ...results } });
      }
      toast.success('POS bridge sync completed');
      await load();
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setSyncing('');
    }
  };

  const viewLiveResource = async (resource) => {
    setPreviewing(resource.key);
    try {
      const params = selectedBusiness ? { business_id: selectedBusiness.id } : {};
      const { data } = await api.get(`/pos-bridge/proxy/${resource.key}`, { params, timeout: POS_BRIDGE_TIMEOUT_MS });
      setLivePreview({
        resource,
        rows: data.rows || [],
        raw: data.raw,
      });
    } catch (err) {
      toast.error(formatApiError(err));
    } finally {
      setPreviewing('');
    }
  };

  const previewRows = livePreview?.rows || [];
  const previewColumns = Array.from(new Set(previewRows.slice(0, 20).flatMap(row => Object.keys(row || {}))))
    .filter(key => !['passwordHash', 'password_hash'].includes(key))
    .slice(0, 8);
  const lastResultRows = lastResult?.results
    ? Object.entries(lastResult.results).map(([key, value]) => ({ key, ...value }))
    : lastResult
      ? [{ key: lastResult.resource || 'sync', ...lastResult }]
      : [];

  return (
    <div className="space-y-6" data-testid="pos-bridge-page">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Plug className="h-6 w-6 text-blue-600" />
          <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">POS Bridge</h1>
          </div>
          <p className="text-sm text-zinc-500 mt-1">
            Connected bridge for syncing the CashFlow Lite POS project into this admin panel.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={load} disabled={loading} className="gap-1.5">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />Refresh
          </Button>
          <Button size="sm" onClick={syncAll} disabled={!config?.configured || Boolean(syncing)} className="gap-1.5 bg-blue-600 hover:bg-blue-700">
            <RotateCw className={`h-3.5 w-3.5 ${syncing.startsWith('all') ? 'animate-spin' : ''}`} />Sync All
          </Button>
        </div>
      </div>

      <Card className={`shadow-sm ${config?.configured ? 'border-emerald-200 bg-emerald-50/30' : 'border-amber-200 bg-amber-50/30'}`}>
        <CardContent className="p-5 flex items-start gap-3">
          {config?.configured ? <CheckCircle2 className="h-5 w-5 text-emerald-600 mt-0.5" /> : <AlertCircle className="h-5 w-5 text-amber-600 mt-0.5" />}
          <div className="min-w-0 flex-1">
            <p className={`text-sm font-medium ${config?.configured ? 'text-emerald-900' : 'text-amber-900'}`}>
              {config?.configured ? 'POS bridge configured' : 'POS bridge not connected yet'}
            </p>
            <p className={`text-sm mt-1 break-all ${config?.configured ? 'text-emerald-800' : 'text-amber-800'}`}>
              {config?.configured
                ? `Using ${config.base_url}`
                : `Set ${config?.env?.base_url || 'POS_CORE_API_BASE_URL'} on the admin backend when ready.`}
            </p>
          </div>
          <div className="flex flex-col gap-1">
            <Badge variant="outline" className="text-[11px]">{config?.owner_login_configured ? 'Owner login set' : 'No owner login'}</Badge>
            <Badge variant="outline" className="text-[11px]">{config?.api_key_configured ? 'API key set' : 'No API key'}</Badge>
          </div>
        </CardContent>
      </Card>

      <Card className="shadow-sm border-zinc-200">
        <CardHeader className="px-5 py-4 border-b border-zinc-100">
          <CardTitle className="text-base font-medium text-zinc-900 flex items-center gap-2">
            <DatabaseZap className="h-4 w-4 text-zinc-500" />Syncable POS Resources
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="bg-zinc-50 hover:bg-zinc-50">
                <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Resource</TableHead>
                <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">POS Endpoint</TableHead>
                <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Admin Storage</TableHead>
                <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Mode</TableHead>
                <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Local Count</TableHead>
                <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Last Sync</TableHead>
                <TableHead className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500">Health</TableHead>
                <TableHead className="w-56" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow><TableCell colSpan={8} className="text-center py-12 text-zinc-400">Loading...</TableCell></TableRow>
              ) : resources.length === 0 ? (
                <TableRow><TableCell colSpan={8} className="text-center py-12 text-zinc-400">No resources configured</TableCell></TableRow>
              ) : resources.map(resource => (
                <TableRow key={resource.key} className="hover:bg-zinc-50/50">
                  <TableCell className="py-3 font-medium text-zinc-900">{resource.label}</TableCell>
                  <TableCell className="py-3 text-sm font-mono text-zinc-500">/api/{(resource.endpoint_candidates || [resource.endpoint]).join(' | /api/')}</TableCell>
                  <TableCell className="py-3 text-sm font-mono text-zinc-500">{resource.collection}</TableCell>
                  <TableCell className="py-3"><Badge variant="outline" className="text-[11px]">{MODE_LABELS[resource.mode] || resource.mode}</Badge></TableCell>
                  <TableCell className="py-3 text-sm text-zinc-600">{resource.local_count}</TableCell>
                  <TableCell className="py-3 text-sm text-zinc-600">
                    {formatSyncTime(resource.last_sync?.finished_at)}
                    {resource.last_sync?.synced_count !== undefined && (
                      <div className="text-xs text-zinc-400 mt-0.5">
                        {resource.last_sync.synced_count} synced, {resource.last_sync.error_count || 0} errors
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="py-3">
                    {resource.last_sync ? (
                      <div className="space-y-1">
                        <Badge variant="outline" className={`text-[11px] ${SYNC_STATUS_CLASSES[resource.last_sync.status] || 'bg-zinc-50 text-zinc-600 border-zinc-200'}`}>
                          {resource.last_sync.status === 'success' && <CheckCircle2 className="h-3 w-3 mr-1" />}
                          {resource.last_sync.status === 'partial' && <AlertCircle className="h-3 w-3 mr-1" />}
                          {resource.last_sync.status === 'failed' && <XCircle className="h-3 w-3 mr-1" />}
                          {resource.last_sync.status}
                        </Badge>
                        {(resource.last_sync.errors || []).length > 0 && (
                          <p className="max-w-72 truncate text-xs text-red-600" title={errorText(resource.last_sync.errors[0])}>
                            {errorText(resource.last_sync.errors[0])}
                          </p>
                        )}
                      </div>
                    ) : (
                      <Badge variant="outline" className="text-[11px] bg-zinc-50 text-zinc-500 border-zinc-200">
                        <Clock3 className="h-3 w-3 mr-1" />Never synced
                      </Badge>
                    )}
                  </TableCell>
                  <TableCell className="py-3">
                    <div className="flex justify-end gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-8 gap-1.5 border-zinc-200"
                        disabled={!config?.configured || Boolean(previewing)}
                        onClick={() => viewLiveResource(resource)}
                      >
                        <Eye className={`h-3.5 w-3.5 ${previewing === resource.key ? 'animate-pulse' : ''}`} />Live
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-8 gap-1.5 border-zinc-200"
                        disabled={!config?.configured || Boolean(syncing)}
                        onClick={() => syncResource(resource.key)}
                      >
                        <RotateCw className={`h-3.5 w-3.5 ${syncing === resource.key || syncing === `all:${resource.key}` ? 'animate-spin' : ''}`} />Sync
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {lastResult && (
        <Card className="shadow-sm border-zinc-200">
          <CardHeader className="px-5 py-4 border-b border-zinc-100">
            <CardTitle className="text-base font-medium text-zinc-900">Last Sync Result</CardTitle>
          </CardHeader>
          <CardContent className="p-5 space-y-4">
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {lastResultRows.map(row => (
                <div key={row.key} className="rounded-md border border-zinc-200 bg-white p-3">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium text-zinc-900">{resources.find(resource => resource.key === row.key)?.label || row.key}</p>
                    <Badge variant="outline" className={`text-[11px] ${SYNC_STATUS_CLASSES[row.status] || 'bg-zinc-50 text-zinc-600 border-zinc-200'}`}>
                      {row.status || (row.error_count ? 'failed' : 'success')}
                    </Badge>
                  </div>
                  <div className="mt-2 text-xs text-zinc-500">
                    {row.count || 0} synced, {row.error_count || 0} errors
                  </div>
                  {(row.errors || []).length > 0 && (
                    <p className="mt-2 text-xs text-red-600 line-clamp-2" title={errorText(row.errors[0])}>
                      {errorText(row.errors[0])}
                    </p>
                  )}
                </div>
              ))}
            </div>
            <pre className="text-xs whitespace-pre-wrap break-words bg-zinc-50 border border-zinc-200 rounded-md p-3 max-h-72 overflow-auto">
              {JSON.stringify(lastResult, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}

      <Dialog open={Boolean(livePreview)} onOpenChange={(open) => !open && setLivePreview(null)}>
        <DialogContent className="max-w-6xl max-h-[86vh] p-0 overflow-hidden">
          <DialogHeader className="px-5 py-4 border-b border-zinc-100">
            <DialogTitle className="font-heading text-lg">
              Live POS Data: {livePreview?.resource?.label}
            </DialogTitle>
            <p className="text-sm text-zinc-500">
              Direct read from {config?.base_url}/api/{livePreview?.resource?.endpoint}. Nothing here is saved to AdminCore until you click Sync.
            </p>
          </DialogHeader>
          <div className="px-5 py-3 border-b border-zinc-100 flex items-center justify-between">
            <div className="text-sm text-zinc-600">
              Rows returned: <span className="font-semibold text-zinc-900">{previewRows.length}</span>
            </div>
            {livePreview?.resource && (
              <Button size="sm" className="bg-blue-600 hover:bg-blue-700 gap-1.5" disabled={Boolean(syncing)} onClick={() => syncResource(livePreview.resource.key)}>
                <RotateCw className={`h-3.5 w-3.5 ${syncing === livePreview.resource.key ? 'animate-spin' : ''}`} />Sync This Resource
              </Button>
            )}
          </div>
          <ScrollArea className="max-h-[62vh]">
            {previewRows.length === 0 ? (
              <div className="px-5 py-12 text-center text-sm text-zinc-400">No live POS rows returned</div>
            ) : (
              <div className="min-w-full overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-zinc-50 hover:bg-zinc-50">
                      {previewColumns.map(column => (
                        <TableHead key={column} className="text-[11px] font-semibold uppercase tracking-widest text-zinc-500 whitespace-nowrap">{column}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {previewRows.slice(0, 100).map((row, idx) => (
                      <TableRow key={row?.id || row?._id || idx}>
                        {previewColumns.map(column => (
                          <TableCell key={column} className="max-w-64 truncate py-3 text-sm text-zinc-700">
                            {typeof row?.[column] === 'object' ? JSON.stringify(row[column]) : String(row?.[column] ?? '')}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                {previewRows.length > 100 && (
                  <div className="px-5 py-3 text-xs text-zinc-500 border-t border-zinc-100">
                    Showing first 100 rows. Sync imports the full POS response.
                  </div>
                )}
              </div>
            )}
            <div className="border-t border-zinc-100 p-5">
              <p className="text-xs font-semibold uppercase tracking-widest text-zinc-400 mb-2">Raw response</p>
              <pre className="text-xs whitespace-pre-wrap break-words bg-zinc-50 border border-zinc-200 rounded-md p-3">
                {JSON.stringify(livePreview?.raw, null, 2)}
              </pre>
            </div>
          </ScrollArea>
        </DialogContent>
      </Dialog>
    </div>
  );
}
