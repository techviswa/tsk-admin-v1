import { displayValue } from '@/lib/posOperations';

export default function RecordValueSummary({ summary, monetary, label, pageCount }) {
  const currencies = summary?.amount_by_currency || [];
  return <>
    <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">{monetary ? label : 'Records on this page'}</p>
    <p className="text-2xl font-semibold text-zinc-900 mt-1">{!monetary ? pageCount : summary?.invalid_amounts ? 'Unavailable' : !summary?.total ? 'No recorded amounts' : currencies.length > 1 ? 'Multiple currencies' : displayValue(summary.amount_total, 'money', currencies[0]?.currency)}</p>
    {monetary && currencies.length > 1 && currencies.map(row => <p key={row.currency} className="mt-1 text-sm text-zinc-500">{displayValue(row.amount, 'money', row.currency)}</p>)}
    {Boolean(summary?.invalid_amounts) && monetary && <p role="alert" className="text-xs text-red-600">Some source amounts are invalid; totals are unavailable.</p>}
    {!monetary && <p className="mt-1 text-xs text-zinc-500">Inspect each record for its quantity, unit or rate.</p>}
  </>;
}
