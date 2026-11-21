import { useEffect, useState } from 'react';
import api, { formatApiError } from '@/lib/api';

const labels = { TABLE: 'Table', TOKEN: 'Token', PICKUP: 'Pickup', DELIVERY: 'Delivery' };
export default function FulfillmentModeControls({ business }) {
  const businessId = business?.id;
  const [policy, setPolicy] = useState(null);
  const [draft, setDraft] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setPolicy(null); setDraft(null); setError(''); setBusy(false);
    if (businessId) api.get(`/businesses/${businessId}/fulfillment-modes`, { signal: controller.signal })
      .then(({ data }) => { if (!controller.signal.aborted) { setPolicy(data); setDraft(data.modes); } })
      .catch(err => { if (!controller.signal.aborted) setError(formatApiError(err)); });
    return () => controller.abort();
  }, [businessId, reload]);
  const save = async () => {
    setBusy(true); setError('');
    try {
      const { data } = await api.put(`/businesses/${business.id}/fulfillment-modes`, { modes: draft, version: policy.version });
      setPolicy(data); setDraft(data.modes);
    } catch (err) { setError(formatApiError(err)); }
    finally { setBusy(false); }
  };
  if (!business) return <section className="rounded-lg border bg-white p-5 space-y-3" aria-label="POS billing modes">
    <h2 className="font-semibold">POS billing modes</h2>
    <p className="text-sm text-zinc-500">Choose a business from the selector at the top of the page to manage its billing modes.</p>
    <div className="flex flex-wrap gap-5">{Object.entries(labels).map(([key, label]) => <label className="flex gap-2 items-center" key={key}>
      <input type="checkbox" disabled checked={false} />{label}
    </label>)}</div>
    <p className="text-sm" role="status">No business selected. Choose a business to load its saved settings.</p>
  </section>;
  return <section className="rounded-lg border bg-white p-5 space-y-3" aria-label="POS billing modes" key={business.id}>
    <h2 className="font-semibold">POS billing modes</h2>
    <p className="text-sm text-zinc-500">Control new billing for {business.name}. Existing bills and history remain available.</p>
    {error && <p role="alert">{error}</p>}
    {!policy && !error && <p>Loading billing controls…</p>}
    {draft && <>
      <div className="flex flex-wrap gap-5">{Object.entries(labels).map(([key, label]) => <label className="flex gap-2 items-center" key={key}>
        <input type="checkbox" checked={draft[key]} disabled={busy || !policy.connected} onChange={event => setDraft(current => ({ ...current, [key]: event.target.checked }))} />{label}
      </label>)}</div>
      {!Object.values(draft).some(Boolean) && <p role="status">All four modes are disabled. POS cannot create new bills.</p>}
      <p className="text-sm" role="status">{!policy.connected ? 'POS connection setup required.' : policy.sync_status === 'confirmed' ? 'Saved settings confirmed by POS.' : policy.sync_status === 'default' ? 'Default: all four modes enabled. Save to send an explicit policy to POS.' : 'Saved in AdminCore; POS confirmation is pending. Check POS Bridge if delivery fails.'}</p>
      <button className="border rounded px-3 py-2" type="button" disabled={busy || !policy.connected || (policy.sync_status !== 'default' && JSON.stringify(draft) === JSON.stringify(policy.modes))} onClick={save}>{busy ? 'Saving…' : 'Save billing modes'}</button>
    </>}
    <button className="border rounded px-3 py-2 ml-2" type="button" disabled={busy} onClick={() => setReload(n => n + 1)}>Reload saved policy / status</button>
  </section>;
}
