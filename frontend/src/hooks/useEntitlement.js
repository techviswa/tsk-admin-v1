import { useCallback, useEffect, useState } from 'react';
import { useBusiness } from '@/components/layout/DashboardLayout';
import api from '@/lib/api';

export function useEntitlement() {
  const { selectedBusiness } = useBusiness();
  const [entitlements, setEntitlements] = useState(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!selectedBusiness?.id) {
      setEntitlements(null);
      return;
    }
    setLoading(true);
    try {
      const { data } = await api.get(`/businesses/${selectedBusiness.id}/entitlements`);
      setEntitlements(data);
    } finally {
      setLoading(false);
    }
  }, [selectedBusiness]);

  useEffect(() => { refresh(); }, [refresh]);

  const hasFeature = useCallback((featureCode) => Boolean(entitlements?.features?.[featureCode]), [entitlements]);
  const getLimit = useCallback((limitCode) => entitlements?.limits?.[limitCode], [entitlements]);

  return { entitlements, loading, refresh, hasFeature, getLimit };
}

export function FeatureGate({ feature, fallback = null, children }) {
  const { loading, hasFeature } = useEntitlement();
  if (loading) return null;
  return hasFeature(feature) ? children : fallback;
}

export function PlanLimitGate({ limit, current = 0, fallback = null, children }) {
  const { loading, getLimit } = useEntitlement();
  if (loading) return null;
  const value = getLimit(limit);
  const allowed = value === 'unlimited' || (value !== null && value !== undefined && Number(current) < Number(value));
  return allowed ? children : fallback;
}
