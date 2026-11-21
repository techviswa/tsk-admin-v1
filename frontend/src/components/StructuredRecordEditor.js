import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { fieldLabel } from '@/lib/posOperations';
import { useEffect, useState } from 'react';

const dangerousKey = key => ['__proto__', 'prototype', 'constructor'].includes(key);

function ValueEditor({ value, onChange, label, fields = {}, template = {}, keyOptions = [], map = false }) {
  const [newKey, setNewKey] = useState('');
  const [numberText, setNumberText] = useState(String(value ?? ''));
  useEffect(() => { if (typeof value === 'number') setNumberText(String(value)); }, [value]);
  if (Array.isArray(value)) return <div className="space-y-3">
    {value.map((row, index) => <fieldset key={index} className="rounded border p-3 space-y-3"><legend className="px-1 text-sm">{label} {index + 1}</legend><ValueEditor value={row} label={label} fields={fields} onChange={next => onChange(value.map((item, i) => i === index ? next : item))} /><Button type="button" variant="outline" size="sm" onClick={() => onChange(value.filter((_, i) => i !== index))}>Remove {label.toLowerCase()}</Button></fieldset>)}
    <Button type="button" variant="outline" size="sm" onClick={() => onChange([...value, JSON.parse(JSON.stringify(template))])}>Add {label.toLowerCase()}</Button>
  </div>;
  if (value && typeof value === 'object') return <div className="space-y-3">
    {Object.entries(value).filter(([key]) => !dangerousKey(key)).map(([key, item]) => <div key={key} className="space-y-1"><p className="text-sm">{fields[key]?.label || keyOptions.find(option => option.id === key)?.name || fieldLabel(key)}</p><ValueEditor value={item} label={fields[key]?.label || fieldLabel(key)} fields={fields[key]?.fields || {}} onChange={next => onChange({ ...value, [key]: next })} />{map && <Button type="button" variant="ghost" size="sm" onClick={() => { const next = { ...value }; delete next[key]; onChange(next); }}>Remove {keyOptions.find(option => option.id === key)?.name || fieldLabel(key)}</Button>}</div>)}
    {keyOptions.length > 0 && <label className="block text-sm">Add outlet<select className="block w-full border rounded p-2 mt-1" value="" onChange={event => { if (event.target.value) onChange({ ...value, [event.target.value]: 0 }); }}><option value="">Choose an outlet</option>{keyOptions.filter(option => !Object.hasOwn(value, option.id)).map(option => <option key={option.id} value={option.id}>{option.name}</option>)}</select></label>}
    {map && keyOptions.length === 0 && <div className="flex flex-wrap gap-2"><Input aria-label={`New ${label.toLowerCase()} name`} placeholder={`${label} name`} value={newKey} onChange={event => setNewKey(event.target.value)} /><Button type="button" variant="outline" disabled={!newKey.trim() || dangerousKey(newKey.trim()) || Object.hasOwn(value, newKey.trim())} onClick={() => { onChange({ ...value, [newKey.trim()]: template }); setNewKey(''); }}>Add {label.toLowerCase()}</Button></div>}
  </div>;
  if (typeof value === 'boolean') return <select aria-label={label} className="block w-full border rounded p-2" value={String(value)} onChange={event => onChange(event.target.value === 'true')}><option value="true">Yes</option><option value="false">No</option></select>;
  const numeric = typeof value === 'number';
  return <Input aria-label={label} required={numeric} type={numeric ? 'number' : 'text'} step={numeric ? 'any' : undefined} value={numeric ? numberText : value ?? ''} onChange={event => {
    if (!numeric) onChange(event.target.value);
    else { setNumberText(event.target.value); if (event.target.value !== '' && Number.isFinite(event.target.valueAsNumber)) onChange(event.target.valueAsNumber); }
  }} />;
}

export default function StructuredRecordEditor({ text, onChange, label, fields, template, kind = 'array', outlets = [] }) {
  let value;
  try {
    value = text ? JSON.parse(text) : kind === 'array' ? [] : {};
    if (kind === 'array' ? !Array.isArray(value) : !value || Array.isArray(value) || typeof value !== 'object') throw new Error('Invalid collection');
  } catch {
    return <p role="alert" className="text-sm text-red-700">The saved {label.toLowerCase()} could not be read. Close this form and reload the record before editing; the saved value has been preserved.</p>;
  }
  return <ValueEditor value={value} onChange={next => onChange(JSON.stringify(next))} label={label} fields={fields} template={template ?? ''} keyOptions={outlets} map={kind === 'object'} />;
}
