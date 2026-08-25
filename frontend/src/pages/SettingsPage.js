import { useState, useEffect } from 'react';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api, { formatApiError } from '@/lib/api';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Switch } from '@/components/ui/switch';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Building2, Settings, Save } from 'lucide-react';

const TIMEZONE_OPTIONS = ['UTC', 'America/New_York', 'America/Chicago', 'America/Los_Angeles', 'Europe/London', 'Europe/Berlin', 'Asia/Tokyo', 'Asia/Dubai', 'Asia/Kolkata'];
const CURRENCY_OPTIONS = ['USD', 'EUR', 'GBP', 'JPY', 'AED', 'INR', 'CAD', 'AUD'];
const LANGUAGE_OPTIONS = ['en', 'es', 'fr', 'de', 'ar', 'ja', 'zh', 'hi'];
const DATE_FORMAT_OPTIONS = ['MM/DD/YYYY', 'DD/MM/YYYY', 'YYYY-MM-DD'];

export default function SettingsPage() {
  const { selectedBusiness } = useBusiness();
  const [settings, setSettings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const fetchSettings = async () => {
    if (!selectedBusiness) { setLoading(false); return; }
    try {
      const { data } = await api.get(`/settings/business/${selectedBusiness.id}`);
      setSettings(data);
    } catch { toast.error('Failed to load settings'); }
    finally { setLoading(false); }
  };

  useEffect(() => { setLoading(true); fetchSettings(); }, [selectedBusiness]);

  const getValue = (key) => settings.find(s => s.key === key)?.value || '';
  const setValue = (key, value) => setSettings(prev => prev.map(s => s.key === key ? { ...s, value } : s));

  const saveSetting = async (key) => {
    setSaving(true);
    try {
      const val = getValue(key);
      await api.put(`/settings/business/${selectedBusiness.id}/${key}`, { value: val });
      toast.success('Setting saved');
    } catch (err) { toast.error(formatApiError(err)); }
    finally { setSaving(false); }
  };

  const saveAll = async (category) => {
    setSaving(true);
    try {
      const cats = settings.filter(s => s.category === category);
      for (const s of cats) {
        await api.put(`/settings/business/${selectedBusiness.id}/${s.key}`, { value: s.value });
      }
      toast.success(`${category} settings saved`);
    } catch (err) { toast.error(formatApiError(err)); }
    finally { setSaving(false); }
  };

  if (!selectedBusiness) {
    return (
      <div className="text-center py-20" data-testid="settings-page">
        <Building2 className="h-12 w-12 mx-auto text-zinc-300 mb-4" />
        <h2 className="text-lg font-medium text-zinc-900 font-heading">Select a Business</h2>
        <p className="text-sm text-zinc-500 mt-1">Choose a business to configure settings</p>
      </div>
    );
  }

  const renderSelect = (key, options) => (
    <Select value={getValue(key)} onValueChange={v => setValue(key, v)}>
      <SelectTrigger data-testid={`setting-${key}`}><SelectValue /></SelectTrigger>
      <SelectContent>{options.map(o => <SelectItem key={o} value={o}>{o}</SelectItem>)}</SelectContent>
    </Select>
  );

  const renderBoolean = (key, label) => (
    <div className="flex items-center justify-between py-3">
      <div>
        <p className="text-sm font-medium text-zinc-900">{label || key}</p>
        <p className="text-xs text-zinc-500">{settings.find(s => s.key === key)?.description || ''}</p>
      </div>
      <Switch
        checked={getValue(key) === 'true'}
        onCheckedChange={v => { setValue(key, String(v)); saveSetting(key); }}
        data-testid={`setting-toggle-${key}`}
      />
    </div>
  );

  return (
    <div className="space-y-6" data-testid="settings-page">
      <div>
        <h1 className="text-2xl font-heading font-semibold text-zinc-950 tracking-tight">Settings</h1>
        <p className="text-sm text-zinc-500 mt-0.5">Configuration for {selectedBusiness.name}</p>
      </div>

      {loading ? (
        <div className="space-y-4">{[1,2,3].map(i => <div key={i} className="h-32 bg-zinc-200 rounded-lg animate-pulse" />)}</div>
      ) : (
        <Tabs defaultValue="general" className="space-y-6">
          <TabsList className="bg-zinc-100/80 p-1 h-auto" data-testid="settings-tabs">
            <TabsTrigger value="general" className="data-[state=active]:bg-white data-[state=active]:shadow-sm text-sm py-1.5 px-3">General</TabsTrigger>
            <TabsTrigger value="notifications" className="data-[state=active]:bg-white data-[state=active]:shadow-sm text-sm py-1.5 px-3">Notifications</TabsTrigger>
            <TabsTrigger value="branding" className="data-[state=active]:bg-white data-[state=active]:shadow-sm text-sm py-1.5 px-3">Branding</TabsTrigger>
          </TabsList>

          <TabsContent value="general">
            <Card className="shadow-sm border-zinc-200">
              <CardHeader className="px-6 py-4 border-b border-zinc-100">
                <CardTitle className="text-base font-medium flex items-center gap-2"><Settings className="h-4 w-4 text-zinc-500" />General Settings</CardTitle>
              </CardHeader>
              <CardContent className="p-6 space-y-5">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  <div className="space-y-2"><Label className="text-xs font-semibold uppercase tracking-widest text-zinc-500">Timezone</Label>{renderSelect('timezone', TIMEZONE_OPTIONS)}</div>
                  <div className="space-y-2"><Label className="text-xs font-semibold uppercase tracking-widest text-zinc-500">Currency</Label>{renderSelect('currency', CURRENCY_OPTIONS)}</div>
                  <div className="space-y-2"><Label className="text-xs font-semibold uppercase tracking-widest text-zinc-500">Language</Label>{renderSelect('language', LANGUAGE_OPTIONS)}</div>
                  <div className="space-y-2"><Label className="text-xs font-semibold uppercase tracking-widest text-zinc-500">Date Format</Label>{renderSelect('date_format', DATE_FORMAT_OPTIONS)}</div>
                </div>
                <div className="pt-2"><Button onClick={() => saveAll('general')} disabled={saving} className="bg-blue-600 hover:bg-blue-700 gap-2" data-testid="save-general-btn"><Save className="h-4 w-4" />{saving ? 'Saving...' : 'Save Changes'}</Button></div>
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="notifications">
            <Card className="shadow-sm border-zinc-200">
              <CardHeader className="px-6 py-4 border-b border-zinc-100">
                <CardTitle className="text-base font-medium">Notification Preferences</CardTitle>
              </CardHeader>
              <CardContent className="p-6 divide-y divide-zinc-100">
                {renderBoolean('email_notifications', 'Email Notifications')}
                {renderBoolean('sms_notifications', 'SMS Notifications')}
                {renderBoolean('push_notifications', 'Push Notifications')}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="branding">
            <Card className="shadow-sm border-zinc-200">
              <CardHeader className="px-6 py-4 border-b border-zinc-100">
                <CardTitle className="text-base font-medium">Branding</CardTitle>
              </CardHeader>
              <CardContent className="p-6 space-y-5">
                <div className="space-y-2">
                  <Label className="text-xs font-semibold uppercase tracking-widest text-zinc-500">Primary Color</Label>
                  <div className="flex items-center gap-3">
                    <input type="color" value={getValue('primary_color') || '#0055FF'} onChange={e => setValue('primary_color', e.target.value)} className="w-10 h-10 rounded border border-zinc-200 cursor-pointer" data-testid="setting-primary-color" />
                    <Input value={getValue('primary_color') || '#0055FF'} onChange={e => setValue('primary_color', e.target.value)} className="max-w-[140px] font-mono text-sm" />
                  </div>
                </div>
                <div className="space-y-2">
                  <Label className="text-xs font-semibold uppercase tracking-widest text-zinc-500">Business Tagline</Label>
                  <Input value={getValue('business_tagline')} onChange={e => setValue('business_tagline', e.target.value)} placeholder="Your catchy tagline" data-testid="setting-tagline" />
                </div>
                <div className="pt-2"><Button onClick={() => saveAll('branding')} disabled={saving} className="bg-blue-600 hover:bg-blue-700 gap-2" data-testid="save-branding-btn"><Save className="h-4 w-4" />{saving ? 'Saving...' : 'Save Changes'}</Button></div>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
