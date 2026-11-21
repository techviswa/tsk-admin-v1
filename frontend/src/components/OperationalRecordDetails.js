import { displayValue, fieldLabel, recordCurrency, sourceValue } from '@/lib/posOperations';

const privateKey = key => /token|secret|password|session_key|^code$|bank|pin/i.test(key);

export default function OperationalRecordDetails({ record, config }) {
  const currency = recordCurrency(record);
  const configured = new Set(config.columns.map(([, key]) => key));
  const additional = Object.entries({ ...record, ...(record.metadata || {}) }).filter(([key]) => !configured.has(key) && !['metadata', 'id', 'status', 'pos_external_id'].includes(key) && !privateKey(key));
  return <div className="mt-6 space-y-6">
    <dl className="grid gap-4 sm:grid-cols-2">
      {config.columns.map(([label, key, format]) => <div key={key} className="min-w-0"><dt className="text-sm text-zinc-500">{label}</dt><dd className="mt-1 break-words text-sm font-medium">{displayValue(sourceValue(record, key), format, currency)}</dd></div>)}
      <div><dt className="text-sm text-zinc-500">Status</dt><dd className="mt-1 text-sm">{record.status ? fieldLabel(record.status) : 'Not provided'}</dd></div>
    </dl>
    {additional.length > 0 && <details className="rounded border p-3"><summary className="cursor-pointer text-sm font-medium">Additional record information ({additional.length})</summary><dl className="mt-4 space-y-4">{additional.map(([key, value]) => <div key={key}><dt className="text-sm text-zinc-500">{fieldLabel(key)}</dt><dd className="mt-1 break-words text-sm">{displayValue(value)}</dd></div>)}</dl></details>}
    <details className="rounded border p-3"><summary className="cursor-pointer text-sm">Record reference</summary><p className="mt-2 break-all text-xs text-zinc-500">{record.pos_external_id || record.id}</p></details>
  </div>;
}
