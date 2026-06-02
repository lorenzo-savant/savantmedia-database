"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { getRegions } from "@/lib/data";
import {
  SVERIGE_REGIONER,
  STORLEK_LABELS,
  SNI_HUVUDGRUPPER,
  type Filters,
  type StorlekKategori,
} from "@/lib/types";

type FilterPanelProps = {
  filters: Filters;
  onApply: (filters: Filters) => void;
  onClear: () => void;
  open: boolean;
};

export function FilterPanel({ filters, onApply, onClear, open }: FilterPanelProps) {
  const [local, setLocal] = useState<Filters>(filters);
  const [regions, setRegions] = useState<string[]>([]);

  useEffect(() => {
    setLocal(filters);
  }, [filters]);

  useEffect(() => {
    setRegions(getRegions());
  }, []);

  const update = <K extends keyof Filters>(key: K, value: Filters[K]) => {
    setLocal((prev) => ({ ...prev, [key]: value }));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onApply(local);
  };

  if (!open) return null;

  const allRegions = [...new Set([...SVERIGE_REGIONER, ...regions])].sort();

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 shadow-sm mb-4">
      <form onSubmit={handleSubmit}>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-bold uppercase tracking-wider text-gray-500">
              Storlek
            </label>
            <select
              value={local.storlek}
              onChange={(e) =>
                update("storlek", e.target.value as StorlekKategori | "")
              }
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">Alla storlekar</option>
              {(["liten", "medel", "multinationell"] as const).map((s) => (
                <option key={s} value={s}>
                  {STORLEK_LABELS[s]}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-bold uppercase tracking-wider text-gray-500">
              Bransch (SNI)
            </label>
            <select
              value={local.sniHuvudgrupp}
              onChange={(e) => update("sniHuvudgrupp", e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">Alla branscher</option>
              {Object.entries(SNI_HUVUDGRUPPER).map(([k, v]) => (
                <option key={k} value={k}>
                  {k} — {v}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-bold uppercase tracking-wider text-gray-500">
              Region
            </label>
            <select
              value={local.region}
              onChange={(e) => update("region", e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">Alla regioner</option>
              {allRegions.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-bold uppercase tracking-wider text-gray-500">
              Stad
            </label>
            <input
              type="text"
              value={local.stad}
              onChange={(e) => update("stad", e.target.value)}
              placeholder="T.ex. Stockholm"
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-bold uppercase tracking-wider text-gray-500">
              Land
            </label>
            <input
              type="text"
              value={local.land}
              onChange={(e) => update("land", e.target.value)}
              placeholder="T.ex. Sverige"
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-bold uppercase tracking-wider text-gray-500">
              Ändrad från
            </label>
            <input
              type="date"
              value={local.andradFran}
              onChange={(e) => update("andradFran", e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-bold uppercase tracking-wider text-gray-500">
              Ändrad till
            </label>
            <input
              type="date"
              value={local.andradTill}
              onChange={(e) => update("andradTill", e.target.value)}
              className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div className="flex items-center gap-2 pt-5">
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={local.endastVerifierade}
                onChange={(e) => update("endastVerifierade", e.target.checked)}
                className="accent-emerald-600 w-4 h-4"
              />
              Endast med verifierad kontakt
            </label>
          </div>
          <div className="flex items-center gap-2 pt-5">
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={local.endastMedDomain}
                onChange={(e) => update("endastMedDomain", e.target.checked)}
                className="accent-blue-600 w-4 h-4"
              />
              Endast med domän
            </label>
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-4 pt-4 border-t border-gray-200">
          <Button type="button" variant="outline" size="sm" onClick={onClear}>
            Rensa filter
          </Button>
          <Button type="submit" variant="primary" size="sm">
            Applicera filter
          </Button>
        </div>
      </form>
    </div>
  );
}
