import { useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Typography } from "@/components/NouiTypography";
import {
  consumeTokenFromUrlFragment,
  fetchAuthStatus,
  getBootstrapToken,
  loginWithPassword,
  loginWithToken,
  type DashboardAuthStatus,
} from "@/lib/dashboardAuth";
import { cn } from "@/lib/utils";

interface AuthGateProps {
  children: ReactNode;
}

export function AuthGate({ children }: AuthGateProps) {
  const [status, setStatus] = useState<DashboardAuthStatus | null>(null);
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const loginKind = useMemo(() => {
    if (!status?.required) return "none";
    if (status.supports_password_login) return "password";
    if (status.supports_token_login) return "token";
    return "identity";
  }, [status]);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchAuthStatus();
      setStatus(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to check authentication status");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let alive = true;
    async function init() {
      setLoading(true);
      setError(null);
      try {
        const fragmentToken = consumeTokenFromUrlFragment();
        const bootstrapToken = fragmentToken ?? getBootstrapToken();
        if (bootstrapToken) {
          await loginWithToken(bootstrapToken);
        }
        const next = await fetchAuthStatus();
        if (alive) setStatus(next);
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : "Authentication failed");
      } finally {
        if (alive) setLoading(false);
      }
    }
    void init();
    return () => {
      alive = false;
    };
  }, []);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!secret.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      if (loginKind === "password") await loginWithPassword(secret);
      else await loginWithToken(secret);
      setSecret("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="flex h-dvh items-center justify-center bg-black text-midground">
        <Typography>Checking dashboard authentication…</Typography>
      </div>
    );
  }

  if (status && (!status.required || status.authenticated)) {
    return <>{children}</>;
  }

  const identityMode = loginKind === "identity";
  return (
    <div className="font-mondwest flex h-dvh items-center justify-center bg-black p-4 uppercase text-midground antialiased">
      <div
        className={cn(
          "w-full max-w-md border border-current/20 bg-background-base/95 p-6",
          "shadow-[0_0_50px_rgba(240,230,210,0.08)]",
        )}
        style={{ clipPath: "var(--component-panel-clip-path)" }}
      >
        <Typography className="mb-2 text-xl tracking-[0.08em]">Hermes Dashboard</Typography>
        <p className="mb-5 text-sm leading-6 opacity-70">
          {identityMode
            ? "This dashboard expects identity from a trusted proxy or Tailscale header. Refresh after your proxy authenticates you."
            : `Enter the dashboard ${loginKind} to continue.`}
        </p>

        {error && (
          <p className="mb-4 border border-red-500/40 bg-red-950/30 p-3 text-sm text-red-200">
            {error}
          </p>
        )}

        {identityMode ? (
          <Button type="button" onClick={() => void refresh()} className="w-full">
            Retry
          </Button>
        ) : (
          <form className="space-y-4" onSubmit={submit}>
            <label className="block text-sm tracking-[0.12em] opacity-70" htmlFor="dashboard-secret">
              {loginKind === "password" ? "Password" : "Token"}
            </label>
            <input
              id="dashboard-secret"
              autoFocus
              autoComplete={loginKind === "password" ? "current-password" : "off"}
              className="w-full border border-current/20 bg-black/40 px-3 py-2 text-base text-midground outline-none focus:border-current/60"
              type={loginKind === "password" ? "password" : "text"}
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
            />
            <Button type="submit" disabled={submitting || !secret.trim()} className="w-full">
              {submitting ? "Authenticating…" : "Unlock dashboard"}
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
