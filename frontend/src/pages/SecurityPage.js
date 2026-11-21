import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import api, { formatApiError } from '@/lib/api';
import { useAuth } from '@/contexts/AuthContext';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { ShieldCheck, ShieldAlert } from 'lucide-react';

export default function SecurityPage() {
  const { user, refreshUser } = useAuth();
  const [status, setStatus] = useState(null);
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState('');
  const [recoveryCodes, setRecoveryCodes] = useState(null);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get('/auth/mfa/status');
      setStatus(data);
    } catch (err) { toast.error(formatApiError(err)); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const run = async (action) => {
    setBusy(true);
    try { await action(); } catch (err) { toast.error(formatApiError(err)); } finally { setBusy(false); }
  };

  const startSetup = () => run(async () => {
    const { data } = await api.post('/auth/mfa/setup');
    setSetup(data);
    setCode('');
  });

  const enable = (e) => {
    e.preventDefault();
    return run(async () => {
      const { data } = await api.post('/auth/mfa/enable', { code });
      setRecoveryCodes(data.recovery_codes);
      setSetup(null);
      setCode('');
      toast.success('Two-factor authentication enabled');
      await load();
      await refreshUser();
    });
  };

  const disable = (e) => {
    e.preventDefault();
    return run(async () => {
      await api.post('/auth/mfa/disable', { password, code });
      setPassword('');
      setCode('');
      toast.success('Two-factor authentication disabled');
      await load();
      await refreshUser();
    });
  };

  const enabled = Boolean(status?.enabled);

  return (
    <div className="space-y-6 max-w-2xl" data-testid="security-page">
      <div>
        <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Account security</h1>
        <p className="text-sm text-zinc-500 mt-0.5">Signed in as {user?.email}</p>
      </div>

      {user?.mfa_enrollment_required && !enabled && (
        <div role="alert" className="flex items-start gap-2 text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
          <ShieldAlert className="h-4 w-4 mt-0.5 shrink-0" />
          <span>Two-factor authentication is mandatory for platform staff. Set it up to continue using AdminCore.</span>
        </div>
      )}

      <Card className="border-zinc-200 shadow-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheck className="h-4 w-4" /> Two-factor authentication
            <Badge className={enabled ? 'bg-emerald-100 text-emerald-700 border-emerald-200' : 'bg-zinc-100 text-zinc-600 border-zinc-200'}>
              {status ? (enabled ? 'On' : 'Off') : '…'}
            </Badge>
          </CardTitle>
          <CardDescription>
            Use an authenticator app (Google Authenticator, 1Password, Authy…) to generate a code each time you sign in.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {recoveryCodes && (
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3" data-testid="recovery-codes">
              <p className="text-sm font-medium text-amber-900">Save these recovery codes now — they are shown only once.</p>
              <p className="text-xs text-amber-800 mb-2">Each code works one time if you lose your device.</p>
              <ul className="grid grid-cols-2 gap-1 font-mono text-sm">
                {recoveryCodes.map(c => <li key={c}>{c}</li>)}
              </ul>
              <Button type="button" variant="outline" size="sm" className="mt-3" onClick={() => setRecoveryCodes(null)}>I have saved them</Button>
            </div>
          )}

          {!enabled && !setup && (
            <Button onClick={startSetup} disabled={busy} className="bg-blue-600 hover:bg-blue-700" data-testid="mfa-start">Set up authenticator app</Button>
          )}

          {!enabled && setup && (
            <form onSubmit={enable} className="space-y-4" data-testid="mfa-setup-form">
              {setup.qr_data_uri && <img src={setup.qr_data_uri} alt="Authenticator QR code" className="w-44 h-44 border border-zinc-200 rounded-md" />}
              <div className="space-y-1">
                <Label>Or enter this key manually</Label>
                <p className="font-mono text-sm break-all bg-zinc-50 border border-zinc-200 rounded px-2 py-1" data-testid="mfa-secret">{setup.secret}</p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="mfa-code">6-digit code from the app</Label>
                <Input id="mfa-code" inputMode="numeric" autoComplete="one-time-code" maxLength={7} value={code} onChange={e => setCode(e.target.value)} required data-testid="mfa-code" />
              </div>
              <div className="flex gap-2">
                <Button type="submit" disabled={busy} className="bg-blue-600 hover:bg-blue-700" data-testid="mfa-enable">Verify and enable</Button>
                <Button type="button" variant="outline" onClick={() => setSetup(null)}>Cancel</Button>
              </div>
            </form>
          )}

          {enabled && !status?.required && (
            <form onSubmit={disable} className="space-y-3 border-t border-zinc-100 pt-4">
              <p className="text-sm text-zinc-600">{status.recovery_codes_remaining} recovery codes remaining.</p>
              <div className="space-y-2">
                <Label htmlFor="mfa-disable-password">Password</Label>
                <Input id="mfa-disable-password" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required />
              </div>
              <div className="space-y-2">
                <Label htmlFor="mfa-disable-code">Authenticator or recovery code</Label>
                <Input id="mfa-disable-code" value={code} onChange={e => setCode(e.target.value)} required />
              </div>
              <Button type="submit" variant="outline" disabled={busy} className="text-red-600 border-red-200">Turn off two-factor authentication</Button>
            </form>
          )}
          {enabled && status?.required && (
            <p className="text-sm text-zinc-500">Two-factor authentication is mandatory for platform staff and cannot be turned off.</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
