import { Link, Outlet, createRootRoute } from "@tanstack/react-router";
import { ProjectSwitcher } from "../components/ProjectSwitcher";

export const Route = createRootRoute({
  component: RootLayout,
});

function RootLayout() {
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
              <Link to="/cases" activeProps={{ className: "text-slate-900 font-medium" }} className="text-slate-500 hover:text-slate-900">
                Cases
              </Link>
              <Link to="/runs" activeProps={{ className: "text-slate-900 font-medium" }} className="text-slate-500 hover:text-slate-900">
                Runs
              </Link>
            </nav>
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
