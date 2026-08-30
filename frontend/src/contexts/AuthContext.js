import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import api, { clearAuthTokens, setAuthTokens } from '@/lib/api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const checkAuth = useCallback(async () => {
    try {
      const { data } = await api.get('/auth/me');
      setUser(data);
    } catch {
      setUser(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { checkAuth(); }, [checkAuth]);

  const login = async (email, password) => {
    const { data } = await api.post('/auth/login', { email, password });
    setAuthTokens(data.access_token, data.refresh_token);
    const account = data.user || data;
    setUser(account);
    return account;
  };

  const register = async (email, password, name) => {
    const { data } = await api.post('/auth/register', { email, password, name });
    setAuthTokens(data.access_token, data.refresh_token);
    const account = data.user || data;
    setUser(account);
    return account;
  };

  const logout = async () => {
    try { await api.post('/auth/logout'); } catch {}
    clearAuthTokens();
    setUser(false);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, setUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
