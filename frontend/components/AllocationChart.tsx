"use client";

import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer } from "recharts";
import { Position } from "@/lib/api";

interface Props {
  positions: Position[];
  cashAvailable: number;
  totalValue: number;
}

const COLORS = ["#6366f1", "#22d3ee", "#f59e0b", "#10b981", "#f43f5e", "#a78bfa", "#34d399"];

export default function AllocationChart({ positions, cashAvailable, totalValue }: Props) {
  const grouped: Record<string, number> = {};

  for (const p of positions) {
    const key = p.asset_class.charAt(0).toUpperCase() + p.asset_class.slice(1);
    grouped[key] = (grouped[key] || 0) + p.market_value;
  }

  if (cashAvailable > 0) {
    grouped["Cash"] = (grouped["Cash"] || 0) + cashAvailable;
  }

  const data = Object.entries(grouped).map(([name, value]) => ({
    name,
    value: parseFloat(((value / totalValue) * 100).toFixed(1)),
  }));

  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={60}
            outerRadius={95}
            paddingAngle={2}
            dataKey="value"
          >
            {data.map((_, i) => (
              <Cell key={i} fill={COLORS[i % COLORS.length]} />
            ))}
          </Pie>
          <Tooltip
            formatter={(val) => [`${val}%`, ""]}
            contentStyle={{ backgroundColor: "#1e293b", border: "1px solid #334155", borderRadius: 8 }}
            labelStyle={{ color: "#e2e8f0" }}
          />
          <Legend
            iconType="circle"
            iconSize={8}
            formatter={(val) => <span className="text-slate-300 text-xs">{val}</span>}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}
