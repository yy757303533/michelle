import { Link, Outlet, createRootRoute } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ProjectSwitcher } from "../components/ProjectSwitcher";
import { HeaderClock } from "../components/HeaderClock";
import { apiFetch, getCurrentUser, getSessionToken, setSession } from "../lib/adminAuth";

export const Route = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
  const qc = useQueryClient();
  const [authToken, setAuthTokenState] = useState(getSessionToken());
  const [user, setUser] = useState(getCurrentUser<{ username: string; role: string }>());
  const health = useQuery({
    queryKey: ["healthz-auth"],
    queryFn: async (): Promise<{ auth_required?: boolean }> => {
      const r = await apiFetch("/healthz");
      if (!r.ok) throw new Error("backend down");
      return r.json();
    },
    staleTime: 30_000,
  });
  const me = useQuery({
    queryKey: ["auth-me", authToken],
    enabled: Boolean(health.data?.auth_required),
    retry: false,
    queryFn: async (): Promise<{ data: { username: string; role: string } }> => {
      const r = await apiFetch("/api/auth/me");
      if (!r.ok) throw new Error("login required");
      return r.json();
    },
  });

  useEffect(() => {
    if (!me.data?.data || user) return;
    setSession(authToken || "cookie-session", me.data.data);
    setAuthTokenState(authToken || "cookie-session");
    setUser(me.data.data);
  }, [authToken, me.data?.data, user]);

  if (health.isLoading) {
    return (
      <div className="min-h-screen grid place-items-center text-sm text-slate-400">
        connecting to Michelle…
      </div>
    );
  }

  if (health.data?.auth_required && !authToken && !me.isLoading) {
    return (
      <AdminLogin
        onLogin={(token, nextUser) => {
          setAuthTokenState(token);
          setUser(nextUser);
        }}
      />
    );
  }

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-slate-200 bg-white">
        <div className="max-w-7xl mx-auto px-6 py-3 flex items-center justify-between gap-4">
          <Link to="/" className="text-lg font-semibold tracking-tight shrink-0">
            Michelle <span className="text-slate-400 font-normal">/ AI-native test platform</span>
          </Link>
          <div className="flex items-center gap-6">
            <ProjectSwitcher />
            <nav className="flex gap-4 text-sm">
              <Link to="/" activeProps={{ className: "text-slate-900 font-medium" }} className="text-slate-500 hover:text-slate-900">
                Dashboard
              </Link>
              <Link to="/prd" activeProps={{ className: "text-slate-900 font-medium" }} className="text-slate-500 hover:text-slate-900">
                PRD
              </Link>
              <Link to="/coverage" activeProps={{ className: "text-slate-900 font-medium" }} className="text-slate-500 hover:text-slate-900">
                Coverage
              </Link>
              <Link to="/cases" activeProps={{ className: "text-slate-900 font-medium" }} className="text-slate-500 hover:text-slate-900">
                Cases
              </Link>
              <Link to="/runs" activeProps={{ className: "text-slate-900 font-medium" }} className="text-slate-500 hover:text-slate-900">
                Runs
              </Link>
              <Link to="/queue" activeProps={{ className: "text-slate-900 font-medium" }} className="text-slate-500 hover:text-slate-900">
                Queue
              </Link>
            </nav>
            <AdminSessionControl
              required={Boolean(health.data?.auth_required)}
              token={authToken}
              user={user}
              onTokenChange={setAuthTokenState}
              onUserChange={setUser}
              onLoggedOut={() => {
                qc.removeQueries({ queryKey: ["auth-me"] });
              }}
            />
            <HeaderClock />
          </div>
        </div>
      </header>
      <main className="flex-1 max-w-7xl w-full mx-auto px-6 py-6">
        <Outlet />
      </main>
      <footer className="border-t border-slate-200 bg-white text-xs text-slate-400 px-6 py-3 text-center">
        Michelle · v0.1.0
      </footer>
    </div>
  );
}

function AdminLogin({
  onLogin,
}: {
  onLogin: (token: string, user: { username: string; role: string }) => void;
}) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [checking, setChecking] = useState(false);

  const submit = async () => {
    if (!username.trim() || !password) return;
    setChecking(true);
    setError("");
    try {
      const r = await apiFetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      if (!r.ok) throw new Error("invalid username or password");
      const body = await r.json();
      setSession(body.data.token, body.data.user);
      onLogin(body.data.token, body.data.user);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="min-h-screen grid place-items-center bg-slate-50 px-6">
      <div className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
        <div className="text-xs uppercase tracking-wide text-slate-400">Michelle</div>
        <h1 className="mt-1 text-xl font-semibold">Login</h1>
        <label className="mt-4 block text-sm text-slate-600">
          Username
          <input
            autoFocus
            className="mt-1 w-full rounded border border-slate-200 px-3 py-2 font-mono"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label className="mt-3 block text-sm text-slate-600">
          Password
          <input
            type="password"
            className="mt-1 w-full rounded border border-slate-200 px-3 py-2 font-mono"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void submit();
            }}
          />
        </label>
        <button
          onClick={() => void submit()}
          disabled={!username.trim() || !password || checking}
          className="mt-4 w-full rounded bg-slate-900 px-3 py-2 text-sm text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {checking ? "checking…" : "login"}
        </button>
        {error && <div className="mt-3 text-xs text-red-600">{error}</div>}
      </div>
    </div>
  );
}

function AdminSessionControl({
  required,
  token,
  user,
  onTokenChange,
  onUserChange,
  onLoggedOut,
}: {
  required: boolean;
  token: string;
  user: { username: string; role: string } | null;
  onTokenChange: (token: string) => void;
  onUserChange: (user: { username: string; role: string } | null) => void;
  onLoggedOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [message, setMessage] = useState("");
  const [changing, setChanging] = useState(false);

  if (!required) return null;

  const changePassword = async () => {
    if (!currentPassword || newPassword.length < 8) return;
    setChanging(true);
    setMessage("");
    try {
      const r = await apiFetch("/api/auth/me/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      if (!r.ok) throw new Error(await r.text());
      setCurrentPassword("");
      setNewPassword("");
      setMessage("password updated");
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setChanging(false);
    }
  };

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className={
          "text-xs px-2 py-0.5 rounded border " +
          (token
            ? "border-emerald-300 text-emerald-700"
            : "border-amber-300 text-amber-700")
        }
      >
        {token ? "logged in" : "login"}
      </button>
      {open && (
        <div className="absolute right-0 top-7 z-10 w-72 border border-slate-200 bg-white rounded p-3 shadow text-xs">
          <div className="text-slate-500">
            Signed in as <span className="font-medium text-slate-900">{user?.username ?? "user"}</span>
            <span className="ml-1 font-mono text-slate-400">{user?.role}</span>
          </div>
          <div className="mt-3 space-y-2">
            <input
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              placeholder="current password"
              className="w-full rounded border border-slate-200 px-2 py-1"
            />
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="new password"
              className="w-full rounded border border-slate-200 px-2 py-1"
            />
            <button
              disabled={!currentPassword || newPassword.length < 8 || changing}
              onClick={() => void changePassword()}
              className="w-full rounded bg-slate-900 px-2 py-1 text-white hover:bg-slate-700 disabled:opacity-50"
            >
              {changing ? "updating…" : "change password"}
            </button>
            {message && <div className="break-words text-slate-500">{message}</div>}
          </div>
          <button
            onClick={async () => {
              await apiFetch("/api/auth/logout", { method: "POST" }).catch(() => undefined);
              setSession("");
              onTokenChange("");
              onUserChange(null);
              onLoggedOut();
              setOpen(false);
            }}
            className="mt-3 w-full rounded border border-red-200 px-2 py-1 text-red-700 hover:bg-red-50"
          >
            logout
          </button>
        </div>
      )}
    </div>
  );
}
