"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  ChevronLeft,
  Database,
  CheckCircle2,
  AlertTriangle,
  ArrowRight,
  ArrowLeft,
  RefreshCcw,
  Trash2,
  Download,
  FileSpreadsheet,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { SavantLogo } from "@/components/savant-logo";
import {
  getAllCompanies,
  generateTemplateCSV,
  downloadFile,
  writeCompanyCache,
} from "@/lib/data";
import {
  migrateCompaniesFromLocalStorage,
  getDbStats,
  type MigrationResult,
  type DbStats,
} from "@/lib/actions/migrate";
import { pullAllCompaniesFromSupabase } from "@/lib/actions/pull";
import type { Company } from "@/lib/types";

const STORAGE_KEY = "savantmedia_foretagsdb";

export default function MigratePage() {
  const router = useRouter();
  const { showToast } = useToast();
  const [pending, startTransition] = useTransition();
  const [pulling, setPulling] = useState(false);
  const [localCompanies, setLocalCompanies] = useState<Company[]>([]);
  const [stats, setStats] = useState<DbStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(true);
  const [statsError, setStatsError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<MigrationResult | null>(null);

  const loadStats = async () => {
    setStatsLoading(true);
    setStatsError(null);
    try {
      const s = await getDbStats();
      setStats(s);
    } catch (err) {
      setStatsError(err instanceof Error ? err.message : String(err));
    } finally {
      setStatsLoading(false);
    }
  };

  useEffect(() => {
    setLocalCompanies(getAllCompanies());
    loadStats();
  }, []);

  const handleMigrate = () => {
    if (localCompanies.length === 0) {
      showToast("Inget i localStorage att migrera.", "info");
      return;
    }
    startTransition(async () => {
      try {
        const result = await migrateCompaniesFromLocalStorage(localCompanies);
        setLastResult(result);
        await loadStats();
        if (result.failed === 0) {
          showToast(
            `Migration klar: ${result.imported} nya, ${result.updated} uppdaterade, ${result.contactsInserted} kontakter.`,
            "success"
          );
        } else {
          showToast(
            `Migration delvis: ${result.failed} fel, se detaljer nedan.`,
            "error"
          );
        }
      } catch (err) {
        showToast(
          err instanceof Error ? err.message : "Migration misslyckades",
          "error"
        );
      }
    });
  };

  const handlePullFromSupabase = async () => {
    if (
      localCompanies.length > 0 &&
      !confirm(
        `Skriv över ${localCompanies.length} företag i localStorage med det som finns i Supabase?\n\nLokala ändringar som inte är pushade går förlorade.`
      )
    )
      return;
    setPulling(true);
    try {
      const fromDb = await pullAllCompaniesFromSupabase();
      const cached = writeCompanyCache(fromDb);
      setLocalCompanies(fromDb);
      showToast(
        cached
          ? `Hämtade ${fromDb.length} företag från Supabase till localStorage.`
          : `Hämtade ${fromDb.length} företag, men de ryms inte i localStorage ` +
              `(kvot överskriden) — visas men sparas inte lokalt.`,
        cached ? "success" : "error",
      );
    } catch (err) {
      showToast(
        err instanceof Error ? err.message : "Hämtning misslyckades",
        "error"
      );
    } finally {
      setPulling(false);
    }
  };

  const handleDownloadTemplate = () => {
    const content = generateTemplateCSV();
    const date = new Date().toISOString().slice(0, 10);
    downloadFile(
      content,
      `savantsdatabas_mall_${date}.csv`,
      "text/csv;charset=utf-8"
    );
    showToast(
      "Mall nedladdad. Öppna i Excel, fyll i data och importera via Importera-knappen på startsidan.",
      "success"
    );
  };

  const handleClearLocal = () => {
    if (
      !confirm(
        "Rensa localStorage helt?\n\nDetta tar bort all data från webbläsaren. Använd ENDAST efter att migrationen till Supabase är verifierad."
      )
    )
      return;
    localStorage.removeItem(STORAGE_KEY);
    setLocalCompanies([]);
    showToast("localStorage rensad.", "info");
  };

  const archivedLocal = localCompanies.filter((c) => c.arkiverad).length;
  const totalContactsLocal = localCompanies.reduce(
    (sum, c) => sum + c.kontakter.length,
    0
  );

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 py-5">
      <button
        onClick={() => router.push("/")}
        className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-blue-600 mb-4"
      >
        <ChevronLeft className="w-4 h-4" /> Tillbaka till databasen
      </button>

      <div className="flex items-center gap-3 mb-2 flex-wrap">
        <SavantLogo size={32} />
        <h1 className="text-xl font-bold text-gray-900">Admin — Migration</h1>
      </div>
      <p className="text-sm text-gray-500 mb-6">
        Engångsverktyg: flytta data från webbläsarens localStorage till Supabase.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">
        {/* Local */}
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-[11px] font-bold uppercase tracking-wider text-gray-500">
              Lokalt (webbläsare)
            </span>
          </div>
          <div className="space-y-1.5 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-600">Företag totalt</span>
              <span className="font-semibold text-gray-900">
                {localCompanies.length}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-600">Varav arkiverade</span>
              <span className="font-semibold text-gray-900">
                {archivedLocal}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-600">Kontakter totalt</span>
              <span className="font-semibold text-gray-900">
                {totalContactsLocal}
              </span>
            </div>
          </div>
        </div>

        {/* Remote */}
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
          <div className="flex items-center gap-2 mb-3">
            <Database className="w-3.5 h-3.5 text-emerald-600" />
            <span className="text-[11px] font-bold uppercase tracking-wider text-gray-500">
              Supabase (remote)
            </span>
            <button
              onClick={loadStats}
              className="ml-auto text-gray-400 hover:text-blue-600"
              title="Uppdatera"
              disabled={statsLoading}
            >
              <RefreshCcw
                className={`w-3.5 h-3.5 ${statsLoading ? "animate-spin" : ""}`}
              />
            </button>
          </div>
          {statsError ? (
            <div className="text-sm text-red-600 break-words">
              <AlertTriangle className="w-3.5 h-3.5 inline mr-1" />
              {statsError}
            </div>
          ) : statsLoading || !stats ? (
            <div className="text-sm text-gray-400">Hämtar...</div>
          ) : (
            <div className="space-y-1.5 text-sm">
              <div className="flex justify-between">
                <span className="text-gray-600">Företag totalt</span>
                <span className="font-semibold text-gray-900">
                  {stats.companies}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-600">Varav arkiverade</span>
                <span className="font-semibold text-gray-900">
                  {stats.archived}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-600">Kontakter totalt</span>
                <span className="font-semibold text-gray-900">
                  {stats.contacts}
                </span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* CSV template for colleagues */}
      <div className="bg-white rounded-xl border border-amber-200 shadow-sm p-5 mb-4">
        <h2 className="text-sm font-bold text-gray-900 mb-2 flex items-center gap-2">
          <FileSpreadsheet className="w-4 h-4 text-amber-600" />
          Mall för dataimport (för kollegor)
        </h2>
        <p className="text-sm text-gray-600 mb-3">
          Ladda ner en CSV-mall med exakt de kolumner som behövs för att kunna
          importeras till databasen. Mallen innehåller alla fält (företag +
          upp till 3 kontakter per företag) samt två exempelrader: en
          fullständig och en minimal. Öppna i Excel, fyll i dina företag och
          importera tillbaka via{" "}
          <strong>Importera-knappen på startsidan</strong>.
        </p>
        <ol className="text-xs text-gray-600 mb-4 space-y-1 list-decimal list-inside bg-amber-50 border border-amber-200 rounded-lg p-3">
          <li>
            Klicka på <strong>Ladda ner mall</strong> nedan
          </li>
          <li>Öppna filen i Excel (eller Google Sheets / Numbers)</li>
          <li>
            Ta bort de två exempelraderna och fyll i dina egna företag — ett per
            rad
          </li>
          <li>
            Spara som <code className="bg-white px-1 rounded">.csv</code>{" "}
            (UTF-8)
          </li>
          <li>
            Gå till startsidan → klicka <strong>Importera</strong> → välj{" "}
            <strong>CSV</strong> och dra in din fil
          </li>
          <li>
            Verifiera att allt ser bra ut, kom sedan hit och klicka{" "}
            <strong>Starta migration</strong> för att skicka till Supabase
          </li>
        </ol>
        <Button variant="accent" size="md" onClick={handleDownloadTemplate}>
          <Download className="w-4 h-4" />
          Ladda ner mall (CSV)
        </Button>
      </div>

      {/* Pull from Supabase to local */}
      <div className="bg-white rounded-xl border border-emerald-200 shadow-sm p-5 mb-4">
        <h2 className="text-sm font-bold text-gray-900 mb-2 flex items-center gap-2">
          <Download className="w-4 h-4 text-emerald-600" />
          Steg 0 — Pull från Supabase till localStorage
        </h2>
        <p className="text-sm text-gray-600 mb-4">
          Hämtar alla företag (inklusive arkiverade) från Supabase och{" "}
          <strong>skriver över</strong> webbläsarens localStorage. Använd det
          här när du vill se data som ett annat verktyg/skript (eller en kollega
          via direkt SQL) har lagt till — t.ex. de 90+ företag som Claude har
          synkat via MCP.
        </p>
        <Button
          variant="primary"
          size="md"
          onClick={handlePullFromSupabase}
          disabled={pulling}
        >
          {pulling ? "Hämtar..." : "Hämta från Supabase"}
          <ArrowLeft className="w-4 h-4" />
        </Button>
      </div>

      {/* Migration action */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5 mb-4">
        <h2 className="text-sm font-bold text-gray-900 mb-2">
          Steg 1 — Migrera lokalt → Supabase
        </h2>
        <p className="text-sm text-gray-600 mb-4">
          Skickar alla {localCompanies.length} företag (inklusive arkiverade,
          kontakter, verifieringsbadges, anteckningar) till Supabase. Matchning
          görs på <code className="text-xs bg-gray-100 px-1 rounded">organisationsnummer</code>:
          dubletter uppdateras, övriga skapas nytt. Idempotent — säkert att köra
          flera gånger.
        </p>
        <Button
          variant="primary"
          size="md"
          onClick={handleMigrate}
          disabled={pending || localCompanies.length === 0}
        >
          {pending ? "Migrerar..." : "Starta migration"}
          <ArrowRight className="w-4 h-4" />
        </Button>
      </div>

      {/* Result */}
      {lastResult && (
        <div
          className={`rounded-xl border p-5 mb-4 ${
            lastResult.failed === 0
              ? "bg-emerald-50 border-emerald-200"
              : "bg-amber-50 border-amber-200"
          }`}
        >
          <div className="flex items-center gap-2 mb-3">
            {lastResult.failed === 0 ? (
              <CheckCircle2 className="w-5 h-5 text-emerald-600" />
            ) : (
              <AlertTriangle className="w-5 h-5 text-amber-600" />
            )}
            <h3 className="font-bold text-gray-900">Resultat</h3>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
            <Stat label="Nya" value={lastResult.imported} tone="ok" />
            <Stat label="Uppdaterade" value={lastResult.updated} tone="ok" />
            <Stat
              label="Kontakter"
              value={lastResult.contactsInserted}
              tone="ok"
            />
            <Stat
              label="Fel"
              value={lastResult.failed}
              tone={lastResult.failed > 0 ? "err" : "ok"}
            />
          </div>
          {lastResult.errors.length > 0 && (
            <div className="mt-3 pt-3 border-t border-amber-200">
              <p className="text-xs font-bold uppercase tracking-wider text-amber-800 mb-1.5">
                Felmeddelanden
              </p>
              <ul className="text-xs text-amber-900 space-y-1 max-h-48 overflow-y-auto">
                {lastResult.errors.map((e, i) => (
                  <li key={i} className="break-words">
                    • {e}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Cleanup */}
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-5">
        <h2 className="text-sm font-bold text-gray-900 mb-2">
          Steg 2 — Rensa localStorage (efter verifierad migration)
        </h2>
        <p className="text-sm text-gray-600 mb-4">
          När du har bekräftat i Supabase Studio att allt finns där, kan du
          rensa webbläsarens localStorage. <strong>OBS</strong>: huvudsidan av
          appen läser fortfarande från localStorage tills nästa fas av
          implementationen. Rensa endast om du är säker.
        </p>
        <Button
          variant="danger"
          size="md"
          onClick={handleClearLocal}
          disabled={localCompanies.length === 0}
        >
          <Trash2 className="w-4 h-4" /> Rensa localStorage
        </Button>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "ok" | "err";
}) {
  return (
    <div>
      <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-500">
        {label}
      </p>
      <p
        className={`text-2xl font-bold ${
          tone === "err" ? "text-red-700" : "text-gray-900"
        }`}
      >
        {value}
      </p>
    </div>
  );
}
