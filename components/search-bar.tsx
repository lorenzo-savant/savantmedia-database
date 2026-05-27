"use client";

import { Search, SlidersHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";

type SearchBarProps = {
  value: string;
  onChange: (value: string) => void;
  onToggleFilter: () => void;
  filterOpen: boolean;
};

export function SearchBar({ value, onChange, onToggleFilter, filterOpen }: SearchBarProps) {
  return (
    <div className="flex items-center gap-3 flex-wrap">
      <div className="relative flex-1 min-w-[220px]">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4.5 h-4.5 text-gray-400 pointer-events-none" />
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Sök företag, stad, region, kontaktperson..."
          className="w-full pl-10 pr-4 py-2.5 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 transition-shadow"
        />
      </div>
      <Button
        variant={filterOpen ? "primary" : "outline"}
        size="md"
        onClick={onToggleFilter}
      >
        <SlidersHorizontal className="w-4 h-4" />
        Filter
      </Button>
    </div>
  );
}
