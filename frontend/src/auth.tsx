import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getToken, setToken, type Role, type User } from "./api";

interface AuthCtx {
  user: User | null;
  loading: boolean;
  bootstrapError: string;
  login: (u: string, p: string) => Promise<void>;
  logout: () => void;
  can: (min: Role) => boolean;
}

const RANK: Record<Role, number> = { user: 1, poweruser: 2, admin: 3, owner: 4 };
const Ctx = createContext<AuthCtx>(null!);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [bootstrapError, setBootstrapError] = useState("");

  useEffect(() => {
    let cancelled = false;
    if (!getToken()) {
      setLoading(false);
      return;
    }
    setBootstrapError("");
    api.get<User>("/api/auth/me")
      .then((u) => {
        if (!cancelled) setUser(u);
      })
      .catch((e) => {
        if (cancelled) return;
        if ((e as { status?: number }).status !== 401) {
          setBootstrapError((e as Error).message || "Oturum doğrulanamadı");
        }
        setToken(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = async (u: string, p: string) => {
    const t = await api.login(u, p);
    setToken(t);
    setBootstrapError("");
    setUser(await api.get<User>("/api/auth/me"));
  };
  const logout = () => {
    setToken(null);
    setUser(null);
    setBootstrapError("");
  };
  const can = (min: Role) => !!user && RANK[user.role] >= RANK[min];

  return <Ctx.Provider value={{ user, loading, bootstrapError, login, logout, can }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
