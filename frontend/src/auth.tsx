import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getToken, setToken, type Role, type User } from "./api";

interface AuthCtx {
  user: User | null;
  loading: boolean;
  login: (u: string, p: string) => Promise<void>;
  logout: () => void;
  can: (min: Role) => boolean;
}

const RANK: Record<Role, number> = { user: 1, poweruser: 2, admin: 3 };
const Ctx = createContext<AuthCtx>(null!);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    api.get<User>("/api/auth/me").then(setUser).catch(() => setToken(null)).finally(() => setLoading(false));
  }, []);

  const login = async (u: string, p: string) => {
    const t = await api.login(u, p);
    setToken(t);
    setUser(await api.get<User>("/api/auth/me"));
  };
  const logout = () => {
    setToken(null);
    setUser(null);
  };
  const can = (min: Role) => !!user && RANK[user.role] >= RANK[min];

  return <Ctx.Provider value={{ user, loading, login, logout, can }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
