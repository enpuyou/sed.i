"use client";

import {
  createContext,
  useContext,
  useState,
  useEffect,
  ReactNode,
} from "react";
import { authAPI } from "@/lib/api";
import { User } from "@/types";
import { initPostHog } from "@/lib/posthog";

interface AuthContextType {
  user: User | null;
  login: (email: string, password: string) => Promise<void>;
  register: (
    email: string,
    password: string,
    fullName: string | undefined,
    username: string,
  ) => Promise<void>;
  logout: () => void;
  isLoading: boolean;
  mutate: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    initPostHog();
    // Auth is httpOnly-cookie-based (see app/core/auth_cookies.py) — JS
    // cannot read the cookie to check "is there a token" before deciding
    // whether to call the backend, unlike the old localStorage flow. Always
    // attempt fetchUser(); a 401 (no valid cookie) resolves to isLoading=false
    // with user=null in the catch branch below, same end state as before.
    fetchUser();
  }, []);

  const fetchUser = async () => {
    try {
      const userData = await authAPI.getCurrentUser();
      setUser(userData);
    } catch {
      // getCurrentUser already attempted a refresh-token retry internally
      // (see fetchWithAuth in lib/api.ts) — reaching here means both the
      // access-token cookie and the refresh attempt failed, i.e. genuinely
      // not logged in. Nothing to clear client-side — cookies are managed
      // entirely by the backend (set on login/refresh, cleared on logout).
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  };

  const login = async (email: string, password: string) => {
    await authAPI.login(email, password);
    await fetchUser();
  };

  const register = async (
    email: string,
    password: string,
    fullName: string | undefined,
    username: string,
  ) => {
    await authAPI.register(fullName || email, email, password, username);
    // Auto-login after registration
    await login(email, password);
  };

  const logout = () => {
    void authAPI.logout();
    setUser(null);
  };

  const mutate = async () => {
    await fetchUser();
  };

  return (
    <AuthContext.Provider
      value={{ user, login, register, logout, isLoading, mutate }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
};
