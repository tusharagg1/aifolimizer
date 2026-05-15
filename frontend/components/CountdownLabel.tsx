"use client";

import { useEffect, useState } from "react";

type Props = {
  intervalMs: number;
  resetKey: number;
};

export default function CountdownLabel({ intervalMs, resetKey }: Props) {
  const [now, setNow] = useState<number>(() => Date.now());

  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(tick);
  }, []);

  const elapsed = (now - resetKey) / 1000;
  const remaining = Math.max(0, Math.round(intervalMs / 1000 - elapsed));

  return (
    <span className="text-xs text-slate-600">
      refresh in {Math.floor(remaining / 60)}:
      {String(remaining % 60).padStart(2, "0")}
    </span>
  );
}
