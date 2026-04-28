import { useEffect, useState } from "react";

/** Tiny ticking clock for the header. Useful at a glance because Michelle's
 * timestamps (run started_at, case last_run, etc) are now displayed in
 * local time — the header clock confirms what "local" means and lets the
 * operator eyeball offsets between trigger time and run completion without
 * digging out the system clock. */
export function HeaderClock() {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  // Format as HH:MM:SS local. The full date appears on hover via title
  // so the header itself stays compact.
  const time = now.toLocaleTimeString();
  const full = now.toLocaleString();

  return (
    <span
      className="text-xs font-mono text-slate-500 tabular-nums select-none"
      title={full}
    >
      {time}
    </span>
  );
}
