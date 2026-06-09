"use client";

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useRouter } from "next/navigation";
import { Plus, Upload, Download, RefreshCcw, ArrowDownUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { useToast } from "@/components/ui/toast";
import { SearchBar } from "@/components/search-bar";
import { FilterPanel } from "@/components/filter-panel";
import { CompanyCard } from "@/components/company-card";
import { CompanyForm } from "@/components/company-form";
import { ImportView, ExportView } from "@/components/import-export";
import { SavantLogo } from "@/components/savant-logo";
import {
  getAllCompanies,
  getActiveCompanies,
  searchCompanies,
  addCompany,
  updateCompany,
  archiveCompany,
  restoreCompany,
  DuplicateOrgnrError,
} from "@/lib/data";
import { pullAllCompaniesFromSupabase } from "@/lib/actions/pull";
import type { Company, CompanyFormData, Filters } from "@/lib/types";

const LOCAL_STORAGE_KEY = "savantmedia_foretagsdb";
const LAST_SYNC_KEY = "savantmedia_last_sync";

const DEMO_DATA: CompanyFormData[] = [
  {
    organisationsnummer: "556677-1001",
    domain: "tekniklosningar.se",
    foretagsnamn: "Svenska Tekniklösningar AB",
    bolagsnamn: "Svenska Tekniklösningar Aktiebolag",
    antalAnstallda: 38,
    storlekKategori: "liten",
    storlekManuell: false,
    adress: {
      gata: "Teknikvägen 12",
      postnummer: "164 40",
      stad: "Kista",
      region: "Stockholms län",
      land: "Sverige",
    },
    receptionTelefon: "+46 8 555 12 00",
    emailInfo: "info@tekniklosningar.se",
    sokFlerKontakter: true,
    internaAnteckningar:
      "VD och CTO bekräftade via LinkedIn 2026-05-20. Säljchef kvar att verifiera.",
    kontakter: [
      {
        namn: "Anna Lindberg",
        roll: "Verkställande Direktör (VD)",
        telefon: "+46 70 123 45 01",
        email: "anna.lindberg@tekniklosningar.se",
        linkedinUrl: "https://www.linkedin.com/in/anna-lindberg-demo/",
        verifierad: true,
        verifieringsmetod: "linkedin",
        verifieringskalla: "https://www.linkedin.com/in/anna-lindberg-demo/",
        verifieratAv: "Lorenzo",
        verifieratDatum: new Date().toISOString(),
      },
      {
        namn: "Erik Wallin",
        roll: "Försäljningschef",
        telefon: "+46 70 123 45 02",
        email: "erik.wallin@tekniklosningar.se",
        linkedinUrl: "",
        verifierad: false,
        verifieringsmetod: "",
        verifieringskalla: "",
        verifieratAv: "",
        verifieratDatum: "",
      },
      {
        namn: "Maria Ek",
        roll: "Teknisk Chef (CTO)",
        telefon: "+46 70 123 45 03",
        email: "maria.ek@tekniklosningar.se",
        linkedinUrl: "https://www.linkedin.com/in/maria-ek-demo/",
        verifierad: true,
        verifieringsmetod: "foretagswebbplats",
        verifieringskalla: "https://tekniklosningar.se/om-oss",
        verifieratAv: "Lorenzo",
        verifieratDatum: new Date().toISOString(),
      },
    ],
  },
  {
    organisationsnummer: "556677-2002",
    domain: "nordicconsulting.se",
    foretagsnamn: "Nordic Consulting Group AB",
    bolagsnamn: "Nordic Consulting Group Aktiebolag",
    antalAnstallda: 120,
    storlekKategori: "medel",
    storlekManuell: false,
    adress: {
      gata: "Kungsgatan 25",
      postnummer: "411 15",
      stad: "Göteborg",
      region: "Västra Götalands län",
      land: "Sverige",
    },
    receptionTelefon: "+46 31 700 55 00",
    emailInfo: "kontakt@nordicconsulting.se",
    sokFlerKontakter: true,
    internaAnteckningar: "",
    kontakter: [
      {
        namn: "Johan Bergström",
        roll: "Grundare & Partner",
        telefon: "+46 70 555 10 10",
        email: "johan.bergstrom@nordicconsulting.se",
        linkedinUrl: "",
        verifierad: false,
        verifieringsmetod: "",
        verifieringskalla: "",
        verifieratAv: "",
        verifieratDatum: "",
      },
      {
        namn: "Sofia Nygren",
        roll: "Ekonomichef (CFO)",
        telefon: "+46 70 555 10 11",
        email: "sofia.nygren@nordicconsulting.se",
        linkedinUrl: "",
        verifierad: false,
        verifieringsmetod: "",
        verifieringskalla: "",
        verifieratAv: "",
        verifieratDatum: "",
      },
    ],
  },
  {
    organisationsnummer: "556677-3003",
    domain: "skanelogistik.se",
    foretagsnamn: "Skånes Logistikpartner AB",
    bolagsnamn: "Skånes Logistikpartner AB",
    antalAnstallda: 18,
    storlekKategori: "liten",
    storlekManuell: false,
    adress: {
      gata: "Hamngatan 8",
      postnummer: "211 22",
      stad: "Malmö",
      region: "Skåne län",
      land: "Sverige",
    },
    receptionTelefon: "+46 40 12 34 50",
    emailInfo: "info@skanelogistik.se",
    sokFlerKontakter: false,
    internaAnteckningar:
      "Liten företag — grundaren är enda DM. Hoppa över vid nästa scrape.",
    kontakter: [
      {
        namn: "Per Nilsson",
        roll: "Grundare & Logistikchef",
        telefon: "+46 70 333 22 11",
        email: "per.nilsson@skanelogistik.se",
        linkedinUrl: "https://www.linkedin.com/in/per-nilsson-demo/",
        verifierad: true,
        verifieringsmetod: "linkedin",
        verifieringskalla: "https://www.linkedin.com/in/per-nilsson-demo/",
        verifieratAv: "Lorenzo",
        verifieratDatum: new Date().toISOString(),
      },
    ],
  },
  {
    organisationsnummer: "556677-4004",
    domain: "uppsalabiotek.se",
    foretagsnamn: "Uppsala Bioteknik AB",
    bolagsnamn: "Uppsala Bioteknik Aktiebolag",
    antalAnstallda: 320,
    storlekKategori: "multinationell",
    storlekManuell: false,
    adress: {
      gata: "Forskargatan 3",
      postnummer: "751 83",
      stad: "Uppsala",
      region: "Uppsala län",
      land: "Sverige",
    },
    receptionTelefon: "+46 18 56 78 00",
    emailInfo: "info@uppsalabiotek.se",
    sokFlerKontakter: true,
    internaAnteckningar: "Verkar globalt — fokusera på Nordic-kontakter.",
    kontakter: [
      {
        namn: "Helena Mårtensson",
        roll: "Forskningschef",
        telefon: "+46 70 444 55 66",
        email: "helena.martensson@uppsalabiotek.se",
        linkedinUrl: "",
        verifierad: false,
        verifieringsmetod: "",
        verifieringskalla: "",
        verifieratAv: "",
        verifieratDatum: "",
      },
    ],
  },
];

const defaultFilters: Filters = {
  region: "",
  stad: "",
  land: "",
  storlek: "",
  sniHuvudgrupp: "",
  endastVerifierade: false,
  endastMedDomain: false,
  andradFran: "",
  andradTill: "",
};

const filterTagLabels: Record<keyof Filters, string> = {
  region: "Region",
  stad: "Stad",
  land: "Land",
  storlek: "Storlek",
  sniHuvudgrupp: "Bransch",
  endastVerifierade: "Endast verifierade",
  endastMedDomain: "Endast med domän",
  andradFran: "Ändrad från",
  andradTill: "Ändrad till",
};

function isFilterActive(key: keyof Filters, value: Filters[keyof Filters]): boolean {
  if (typeof value === "boolean") return value;
  return !!value;
}

// ── Sortering (externt filter ovanför listan) ────────────────────────────────
type SortKey = "namn-asc" | "namn-desc" | "storlek-asc" | "storlek-desc";

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "namn-asc", label: "Namn A–Ö" },
  { value: "namn-desc", label: "Namn Ö–A" },
  { value: "storlek-asc", label: "Storlek: liten → stor" },
  { value: "storlek-desc", label: "Storlek: stor → liten" },
];

const SIZE_RANK: Record<string, number> = { liten: 1, medel: 2, multinationell: 3 };

/** Sorteringsvärde för storlek: kategori först, antal anställda som tiebreak. */
function sizeValue(c: Company): number {
  const rank = SIZE_RANK[c.storlekKategori] ?? 0;
  const emp = typeof c.antalAnstallda === "number" ? c.antalAnstallda : 0;
  return rank * 1_000_000 + emp;
}

function sortCompanies(list: Company[], sortBy: SortKey): Company[] {
  const arr = [...list];
  const byName = (a: Company, b: Company) =>
    a.foretagsnamn.localeCompare(b.foretagsnamn, "sv");
  switch (sortBy) {
    case "namn-asc":
      arr.sort(byName);
      break;
    case "namn-desc":
      arr.sort((a, b) => byName(b, a));
      break;
    case "storlek-asc":
      arr.sort((a, b) => sizeValue(a) - sizeValue(b) || byName(a, b));
      break;
    case "storlek-desc":
      arr.sort((a, b) => sizeValue(b) - sizeValue(a) || byName(a, b));
      break;
  }
  return arr;
}

export default function HomePage() {
  const { showToast } = useToast();
  const router = useRouter();
  const [companies, setCompanies] = useState<Company[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [sortBy, setSortBy] = useState<SortKey>("namn-asc");
  const [filters, setFilters] = useState<Filters>(defaultFilters);
  const [filterOpen, setFilterOpen] = useState(false);
  const [dataLoaded, setDataLoaded] = useState(false);

  const [modalOpen, setModalOpen] = useState(false);
  const [modalTitle, setModalTitle] = useState("");
  const [modalView, setModalView] = useState<"add" | "edit" | "import" | "export" | null>(null);
  const [editCompany, setEditCompany] = useState<Company | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [lastSync, setLastSync] = useState<string | null>(null);

  const debounceRef = useRef<NodeJS.Timeout | null>(null);

  const pullFromSupabase = useCallback(async (silent: boolean) => {
    setSyncing(true);
    try {
      const remote = await pullAllCompaniesFromSupabase();
      localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(remote));
      const now = new Date().toISOString();
      localStorage.setItem(LAST_SYNC_KEY, now);
      setLastSync(now);
      setCompanies(searchCompanies(searchQuery, filters));
      if (!silent) {
        showToast(`Synkroniserat ${remote.length} företag från Supabase.`, "success");
      }
    } catch (err) {
      // Always surface sync failures — a silently-failed pull leaves the user
      // staring at a stale/partial local cache with no idea why.
      showToast(
        `Kunde inte synkronisera från Supabase: ${err instanceof Error ? err.message : String(err)}`,
        "error"
      );
    } finally {
      setSyncing(false);
    }
  }, [searchQuery, filters, showToast]);

  useEffect(() => {
    if (dataLoaded) return;
    if (typeof window === "undefined") return;
    const lastSyncStr = localStorage.getItem(LAST_SYNC_KEY);
    setLastSync(lastSyncStr);
    const existing = getActiveCompanies();
    // Always pull from Supabase on load — Supabase is the source of truth and the
    // local cache is only a fast first paint. Gating on a TTL let a stale/partial
    // cache (e.g. synced before the DB grew) persist and hide rows. Cheap for an
    // internal-sized dataset; the pull below overwrites the cache wholesale.
    pullFromSupabase(existing.length > 0).then(() => {
      // If Supabase ritorna 0 (DB vuoto, errore creds), seed demo come fallback
      if (getActiveCompanies().length === 0) {
        DEMO_DATA.forEach((d) => {
          try {
            addCompany(d);
          } catch {
            // ignore demo seed duplicates
          }
        });
        showToast("Demo-data har laddats in.", "info");
      }
    });
    setDataLoaded(true);
  }, [dataLoaded, showToast, pullFromSupabase]);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setCompanies(searchCompanies(searchQuery, filters));
    }, 200);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [searchQuery, filters]);

  const refresh = useCallback(() => {
    setCompanies(searchCompanies(searchQuery, filters));
  }, [searchQuery, filters]);

  const openModalFor = (
    view: "add" | "edit" | "import" | "export",
    company?: Company
  ) => {
    setEditCompany(company || null);
    setModalView(view);
    setModalTitle(
      view === "add"
        ? "Lägg till nytt företag"
        : view === "edit"
        ? `Redigera: ${company?.foretagsnamn}`
        : view === "import"
        ? "Importera data"
        : "Exportera data"
    );
    setModalOpen(true);
  };

  const handleSave = (data: CompanyFormData) => {
    try {
      if (modalView === "edit" && editCompany) {
        updateCompany(editCompany.id, data);
        showToast("Företaget har uppdaterats!", "success");
      } else {
        addCompany(data);
        showToast("Företaget har lagts till!", "success");
      }
      setModalOpen(false);
      refresh();
    } catch (err) {
      if (err instanceof DuplicateOrgnrError) {
        if (err.existingIsArchived) {
          const wantRestore = confirm(
            `Organisationsnummer ${data.organisationsnummer} finns i arkivet. Vill du återställa det istället?`
          );
          if (wantRestore) {
            restoreCompany(err.existingId);
            showToast("Företaget har återställts från arkivet.", "success");
            setModalOpen(false);
            refresh();
            router.push(`/companies/${err.existingId}`);
            return;
          }
          showToast("Återställning avbruten. Företaget ligger kvar i arkivet.", "info");
        } else {
          showToast(
            `Organisationsnummer ${data.organisationsnummer} finns redan i databasen. Redigera det befintliga företaget istället.`,
            "error"
          );
        }
      } else {
        showToast(
          err instanceof Error ? err.message : "Något gick fel",
          "error"
        );
      }
    }
  };

  const handleArchive = (id: string) => {
    const company = companies.find((c) => c.id === id);
    if (!company) return;
    if (
      !confirm(
        `Arkivera "${company.foretagsnamn}"?\n\nFöretaget göms från listan men finns kvar i Arkiv där det kan återställas.`
      )
    )
      return;
    archiveCompany(id);
    showToast(
      `"${company.foretagsnamn}" har arkiverats. Återställ från Arkiv om det behövs.`,
      "info"
    );
    refresh();
  };

  const handleApplyFilters = (newFilters: Filters) => {
    setFilters(newFilters);
    setSearchQuery("");
  };

  const handleClearFilters = () => {
    setFilters(defaultFilters);
    setFilterOpen(false);
  };

  const sortedCompanies = useMemo(
    () => sortCompanies(companies, sortBy),
    [companies, sortBy]
  );

  const activeFilterEntries = (Object.entries(filters) as [keyof Filters, Filters[keyof Filters]][])
    .filter(([k, v]) => isFilterActive(k, v));
  const hasActiveFilters = activeFilterEntries.length > 0;

  const formatFilterValue = (key: keyof Filters, value: Filters[keyof Filters]): string => {
    if (typeof value === "boolean") return "ja";
    if (key === "storlek") {
      const map = { liten: "Liten", medel: "Medel", multinationell: "Multinationell" };
      return map[value as keyof typeof map] || String(value);
    }
    return String(value);
  };

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-5">
      <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3 mb-4">
        <SearchBar
          value={searchQuery}
          onChange={setSearchQuery}
          onToggleFilter={() => setFilterOpen(!filterOpen)}
          filterOpen={filterOpen}
        />
        <div className="flex gap-2 shrink-0">
          <Button
            variant="outline"
            size="md"
            onClick={() => pullFromSupabase(false)}
            disabled={syncing}
            title={lastSync ? `Senast hämtning: ${new Date(lastSync).toLocaleString("sv-SE")} — klick = hämta senaste data från Supabase` : "Klick för att hämta data från Supabase"}
          >
            <RefreshCcw className={`w-4 h-4 ${syncing ? "animate-spin" : ""}`} />
            {syncing ? "Hämtar..." : "Hämta"}
          </Button>
          <Button variant="outline" size="md" onClick={() => openModalFor("import")}>
            <Upload className="w-4 h-4" /> Importera
          </Button>
          <Button variant="outline" size="md" onClick={() => openModalFor("export")}>
            <Download className="w-4 h-4" /> Exportera
          </Button>
          <Button variant="primary" size="md" onClick={() => openModalFor("add")}>
            <Plus className="w-4 h-4" /> Nytt företag
          </Button>
        </div>
      </div>

      {lastSync && (
        <div className="text-[11px] text-gray-400 mb-3">
          Senast synkroniserat med Supabase: {new Date(lastSync).toLocaleString("sv-SE")}
        </div>
      )}

      <FilterPanel
        filters={filters}
        onApply={handleApplyFilters}
        onClear={handleClearFilters}
        open={filterOpen}
      />

      <div className="flex items-center gap-3 mb-4 text-sm text-gray-500 flex-wrap">
        <span className="font-semibold text-gray-700">
          {companies.length} företag
        </span>
        {hasActiveFilters && (
          <div className="flex flex-wrap gap-1.5">
            {activeFilterEntries.map(([key, val]) => (
              <span
                key={key}
                className="inline-flex items-center gap-1 px-2.5 py-0.5 bg-blue-50 text-blue-700 rounded-full text-xs font-medium"
              >
                {filterTagLabels[key]}: {formatFilterValue(key, val)}
                <button
                  onClick={handleClearFilters}
                  className="text-blue-500 hover:text-blue-700 ml-0.5"
                  aria-label="Rensa filter"
                >
                  &times;
                </button>
              </span>
            ))}
          </div>
        )}

        <label className="ml-auto inline-flex items-center gap-1.5 text-gray-500">
          <ArrowDownUp className="w-4 h-4" />
          <span className="text-xs font-medium hidden sm:inline">Sortera:</span>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as SortKey)}
            className="px-2.5 py-1.5 text-sm rounded-lg border border-gray-300 bg-white text-gray-700 focus:border-blue-500 focus:ring-2 focus:ring-blue-100 outline-none cursor-pointer"
            aria-label="Sortera företagslistan"
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {sortedCompanies.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 gap-4">
          <SavantLogo size={80} rounded="2xl" className="opacity-30" />
          <h3 className="text-lg font-semibold text-gray-500">Inga företag hittades</h3>
          <p className="text-sm text-gray-400">
            Lägg till ett nytt företag eller ändra dina filter.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {sortedCompanies.map((company) => (
            <CompanyCard
              key={company.id}
              company={company}
              onView={(id) => window.open(`/companies/${id}`, "_self")}
              onEdit={() => openModalFor("edit", company)}
              onDelete={handleArchive}
            />
          ))}
        </div>
      )}

      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title={modalTitle}>
        {modalView === "add" && (
          <CompanyForm onSave={handleSave} onCancel={() => setModalOpen(false)} />
        )}
        {modalView === "edit" && editCompany && (
          <CompanyForm
            company={editCompany}
            onSave={handleSave}
            onCancel={() => setModalOpen(false)}
          />
        )}
        {modalView === "import" && (
          <ImportView onComplete={() => { setModalOpen(false); refresh(); }} />
        )}
        {modalView === "export" && (
          <ExportView
            searchQuery={searchQuery}
            filters={filters}
            onComplete={() => setModalOpen(false)}
          />
        )}
      </Modal>
    </div>
  );
}
