"use client";

import { useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import {
  ChevronLeft,
  RotateCcw,
  Trash2,
  Search,
  Archive,
  Eye,
  Hash,
  Globe,
  Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { SavantLogo } from "@/components/savant-logo";
import {
  getArchivedCompanies,
  restoreCompany,
  permanentlyDeleteCompany,
} from "@/lib/data";
import { formatDate } from "@/lib/utils";
import {
  STORLEK_LABELS,
  type Company,
  type StorlekKategori,
} from "@/lib/types";

const storlekColors: Record<Exclude<StorlekKategori, "">, string> = {
  liten: "bg-sky-50 text-sky-700 border-sky-200",
  medel: "bg-violet-50 text-violet-700 border-violet-200",
  multinationell: "bg-indigo-50 text-indigo-700 border-indigo-200",
};

export default function ArchivePage() {
  const router = useRouter();
  const { showToast } = useToast();
  const [items, setItems] = useState<Company[]>([]);
  const [query, setQuery] = useState("");

  const refresh = useCallback(() => {
    setItems(getArchivedCompanies());
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const q = query.toLowerCase().trim();
  const filtered = q
    ? items.filter(
        (c) =>
          c.foretagsnamn.toLowerCase().includes(q) ||
          c.bolagsnamn.toLowerCase().includes(q) ||
          c.organisationsnummer.toLowerCase().includes(q) ||
          c.domain.toLowerCase().includes(q) ||
          c.adress.stad.toLowerCase().includes(q)
      )
    : items;

  const handleRestore = (c: Company) => {
    restoreCompany(c.id);
    showToast(`"${c.foretagsnamn}" har återställts.`, "success");
    refresh();
  };

  const handlePermanentDelete = (c: Company) => {
    if (
      !confirm(
        `TA BORT PERMANENT: "${c.foretagsnamn}".\n\nDetta går INTE att ångra. Företaget och alla dess kontakter försvinner för alltid.\n\nÄr du helt säker?`
      )
    )
      return;
    permanentlyDeleteCompany(c.id);
    showToast(`"${c.foretagsnamn}" har raderats permanent.`, "info");
    refresh();
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-5">
      <button
        onClick={() => router.push("/")}
        className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-blue-600 mb-4"
      >
        <ChevronLeft className="w-4 h-4" /> Tillbaka till databasen
      </button>

      <div className="flex items-center gap-3 mb-4 flex-wrap">
        <Archive className="w-6 h-6 text-amber-600" />
        <h1 className="text-xl font-bold text-gray-900">Arkiv</h1>
        <span className="text-sm text-gray-500">
          {items.length} arkiverade företag
        </span>
      </div>

      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-900 mb-4 flex items-start gap-2">
        <Archive className="w-4 h-4 shrink-0 mt-0.5" />
        <div>
          Företag arkiveras med <strong>Arkivera</strong>-knappen istället för
          att raderas. De försvinner från huvudlistan men kan{" "}
          <strong>återställas</strong> härifrån när som helst. Bara{" "}
          <strong>Radera permanent</strong> tar bort dem för gott.
        </div>
      </div>

      <div className="relative mb-4 max-w-md">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Sök i arkiv (namn, org.nr, domän, stad)..."
          className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>

      {filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 gap-4">
          <SavantLogo size={80} rounded="2xl" className="opacity-30" />
          <h3 className="text-lg font-semibold text-gray-500">
            {items.length === 0
              ? "Arkivet är tomt"
              : "Inga arkiverade företag matchar sökningen"}
          </h3>
          <p className="text-sm text-gray-400">
            {items.length === 0
              ? "Företag du arkiverar kommer att visas här."
              : "Prova en annan söksträng."}
          </p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="text-left px-4 py-2.5 text-[11px] font-bold uppercase tracking-wider text-gray-500">
                  Företag
                </th>
                <th className="text-left px-4 py-2.5 text-[11px] font-bold uppercase tracking-wider text-gray-500 hidden md:table-cell">
                  Stad
                </th>
                <th className="text-left px-4 py-2.5 text-[11px] font-bold uppercase tracking-wider text-gray-500 hidden lg:table-cell">
                  Arkiverad
                </th>
                <th className="text-right px-4 py-2.5 text-[11px] font-bold uppercase tracking-wider text-gray-500">
                  Åtgärder
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => {
                const storlek = c.storlekKategori as Exclude<StorlekKategori, "">;
                return (
                  <tr
                    key={c.id}
                    className="border-b border-gray-100 last:border-0 hover:bg-gray-50"
                  >
                    <td className="px-4 py-3">
                      <div className="font-semibold text-gray-900">
                        {c.foretagsnamn}
                      </div>
                      <div className="flex flex-wrap gap-1.5 mt-1">
                        {c.organisationsnummer && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded border border-gray-200 bg-gray-50 text-gray-600">
                            <Hash className="w-2.5 h-2.5" />
                            {c.organisationsnummer}
                          </span>
                        )}
                        {c.domain && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-medium rounded border border-gray-200 bg-gray-50 text-gray-600">
                            <Globe className="w-2.5 h-2.5" />
                            {c.domain}
                          </span>
                        )}
                        {storlek && (
                          <span
                            className={`inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] font-semibold rounded border ${storlekColors[storlek]}`}
                          >
                            <Users className="w-2.5 h-2.5" />
                            {STORLEK_LABELS[storlek]}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-gray-600 hidden md:table-cell">
                      {c.adress.stad || "-"}
                    </td>
                    <td className="px-4 py-3 text-gray-600 hidden lg:table-cell whitespace-nowrap">
                      <div>{formatDate(c.arkiveradDatum)}</div>
                      {c.arkiveradAv && (
                        <div className="text-[11px] text-gray-400">
                          av {c.arkiveradAv}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex gap-1 justify-end">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() =>
                            window.open(`/companies/${c.id}`, "_self")
                          }
                          title="Visa detaljer"
                        >
                          <Eye className="w-4 h-4" />
                        </Button>
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={() => handleRestore(c)}
                          title="Återställ"
                        >
                          <RotateCcw className="w-3.5 h-3.5" />
                          Återställ
                        </Button>
                        <Button
                          variant="danger"
                          size="icon"
                          onClick={() => handlePermanentDelete(c)}
                          title="Radera permanent"
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
