import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/contexts/AuthContext';
import api, { formatApiError } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Store, AlertCircle, Eye, EyeOff } from 'lucide-react';

export default function LoginPage() {
  const { login, register, verifyMfa } = useAuth();
  const navigate = useNavigate();
  const [tab, setTab] = useState('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [mfaToken, setMfaToken] = useState('');
  const [mfaCode, setMfaCode] = useState('');
  const [registrationOpen, setRegistrationOpen] = useState(true);

  useEffect(() => {
    // Hide self-registration when the server has switched it off (production default).
    api.get('/auth/config').then(({ data }) => setRegistrationOpen(Boolean(data.self_registration))).catch(() => {});
  }, []);

  const handleLogin = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const result = await login(email, password);
      if (result?.mfaRequired) {
        setMfaToken(result.mfaToken);
        setPassword('');
        return;
      }
      navigate('/');
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  };

  const handleMfa = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await verifyMfa(mfaToken, mfaCode);
      navigate('/');
    } catch (err) {
      const expired = err?.response?.status === 401 && /expired/i.test(formatApiError(err));
      if (expired) { setMfaToken(''); setMfaCode(''); }
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await register(email, password, name);
      navigate('/');
    } catch (err) {
      setError(formatApiError(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-zinc-50 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <div className="w-14 h-14 bg-blue-600 rounded-2xl flex items-center justify-center mx-auto mb-4 shadow-lg shadow-blue-600/20">
            <Store className="h-7 w-7 text-white" />
          </div>
          <h1 className="text-3xl font-heading font-semibold text-zinc-950 tracking-tight">AdminCore</h1>
          <p className="text-sm text-zinc-500 mt-1">Multi-tenant business administration</p>
        </div>

        <Card className="shadow-sm border-zinc-200">
          <CardContent className="pt-6">
            {mfaToken ? (
              <form onSubmit={handleMfa} className="space-y-4" data-testid="mfa-form">
                <div>
                  <h2 className="text-lg font-semibold text-zinc-900">Two-factor authentication</h2>
                  <p className="text-sm text-zinc-500">Enter the 6-digit code from your authenticator app, or a recovery code.</p>
                </div>
                {error && (
                  <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2" data-testid="auth-error">
                    <AlertCircle className="h-4 w-4 shrink-0" />
                    <span>{error}</span>
                  </div>
                )}
                <div className="space-y-2">
                  <Label htmlFor="mfa-login-code">Verification code</Label>
                  <Input id="mfa-login-code" autoFocus autoComplete="one-time-code" value={mfaCode} onChange={e => setMfaCode(e.target.value)} required data-testid="mfa-login-code" />
                </div>
                <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700" disabled={loading} data-testid="mfa-login-submit">
                  {loading ? 'Verifying...' : 'Verify'}
                </Button>
                <Button type="button" variant="ghost" className="w-full" onClick={() => { setMfaToken(''); setMfaCode(''); setError(''); }}>Back to sign in</Button>
              </form>
            ) : (
            <Tabs value={registrationOpen ? tab : 'login'} onValueChange={setTab}>
              <TabsList className={`grid w-full ${registrationOpen ? 'grid-cols-2' : 'grid-cols-1'} mb-6 bg-zinc-100/80`} data-testid="auth-tabs">
                <TabsTrigger value="login" data-testid="login-tab">Sign In</TabsTrigger>
                {registrationOpen && <TabsTrigger value="register" data-testid="register-tab">Register</TabsTrigger>}
              </TabsList>

              {error && (
                <div className="flex items-center gap-2 text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2 mb-4" data-testid="auth-error">
                  <AlertCircle className="h-4 w-4 shrink-0" />
                  <span>{error}</span>
                </div>
              )}

              <TabsContent value="login">
                <form onSubmit={handleLogin} className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="login-email">Email</Label>
                    <Input id="login-email" type="email" placeholder="admin@admin.com" value={email} onChange={e => setEmail(e.target.value)} required data-testid="login-email" />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="login-password">Password</Label>
                    <div className="relative">
                      <Input id="login-password" type={showPassword ? 'text' : 'password'} placeholder="Enter password" value={password} onChange={e => setPassword(e.target.value)} required data-testid="login-password" className="pr-10" />
                      <Button type="button" variant="ghost" size="icon" className="absolute right-1 top-1 h-8 w-8 text-zinc-500 hover:text-zinc-900" onClick={() => setShowPassword(value => !value)} aria-label={showPassword ? 'Hide password' : 'Show password'}>
                        {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </Button>
                    </div>
                  </div>
                  <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700" disabled={loading} data-testid="login-submit">
                    {loading ? 'Signing in...' : 'Sign In'}
                  </Button>
                </form>
              </TabsContent>

              <TabsContent value="register">
                <form onSubmit={handleRegister} className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="reg-name">Full Name</Label>
                    <Input id="reg-name" placeholder="Your name" value={name} onChange={e => setName(e.target.value)} required data-testid="register-name" />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="reg-email">Email</Label>
                    <Input id="reg-email" type="email" placeholder="you@example.com" value={email} onChange={e => setEmail(e.target.value)} required data-testid="register-email" />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="reg-password">Password</Label>
                    <div className="relative">
                      <Input id="reg-password" type={showPassword ? 'text' : 'password'} placeholder="Create password" value={password} onChange={e => setPassword(e.target.value)} required data-testid="register-password" className="pr-10" />
                      <Button type="button" variant="ghost" size="icon" className="absolute right-1 top-1 h-8 w-8 text-zinc-500 hover:text-zinc-900" onClick={() => setShowPassword(value => !value)} aria-label={showPassword ? 'Hide password' : 'Show password'}>
                        {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </Button>
                    </div>
                  </div>
                  <Button type="submit" className="w-full bg-blue-600 hover:bg-blue-700" disabled={loading} data-testid="register-submit">
                    {loading ? 'Creating account...' : 'Create Account'}
                  </Button>
                </form>
              </TabsContent>
            </Tabs>
            )}
          </CardContent>
        </Card>

        {process.env.NODE_ENV !== 'production' && (
          <p className="text-center text-xs text-zinc-400 mt-6">
            Local development — sign in with your local administrator account.
          </p>
        )}
      </div>
    </div>
  );
}
