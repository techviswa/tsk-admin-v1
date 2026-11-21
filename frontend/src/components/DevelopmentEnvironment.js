import { useEffect, useState } from 'react';
import api from '@/lib/api';

export default function DevelopmentEnvironment() {
  const [environment, setEnvironment] = useState(null);
  useEffect(() => {
    if (process.env.NODE_ENV !== 'development') return;
    let active = true;
    api.get('/health').then(({ data }) => { if (active) setEnvironment(data.environment || 'unknown'); }).catch(() => { if (active) setEnvironment('unreachable'); });
    return () => { active = false; };
  }, []);
  if (process.env.NODE_ENV !== 'development') return null;
  return <div role="status" className={`rounded-md border px-3 py-2 text-xs ${environment === 'development' ? 'border-blue-200 bg-blue-50 text-blue-800' : 'border-amber-200 bg-amber-50 text-amber-900'}`}>
    {environment === 'development' ? 'Local development · local data' : environment === 'production' ? 'Local frontend · live backend data' : environment === 'unreachable' ? 'Local API unavailable' : 'Checking API environment…'}
  </div>;
}
