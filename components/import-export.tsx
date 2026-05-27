"use client";

import { useState, useRef } from "react";
import { Upload, Download, FileJson, FileSpreadsheet, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  getAllCompanies,
  exportJSON,
  exportCSV,
  downloadFile,
  importJSON,
  importCSV,
  searchCompanies,
  generateTemplateCSV,
} from "@/lib/data";
import { useToast } from "@/components/ui/toast";
import type { Filters } from "@/lib/types";

type ImportExportModalProps = {
  onComplete: () => void;
};

export function ImportView({ onComplete }: ImportExportModalProps) {
  const { showToast } = useToast();
  const [step, setStep] = useState<"choose" | "importing">("choose");
  const [format, setFormat] = useState<"json" | "csv">("json");
  const [result, setResult] = useState<{ imported: number; updated: number } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);

  const handleFormatChoice = (fmt: "json" | "csv") => {
    setFormat(fmt);
    setStep("importing");
  };

  const processFile = (file: File) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const content = e.target?.result as string;
      if (!content) return;

      try {
        const res =
          format === "json" ? importJSON(content) : importCSV(content);
        setResult(res);
        showToast(
          `Import klar! ${res.imported} nya, ${res.updated} uppdaterade.`,
          "success"
        );
        onComplete();
      } catch (err) {
        showToast(
          `Import misslyckades: ${err instanceof Error ? err.message : "Okänt fel"}`,
          "error"
        );
      }
    };
    reader.readAsText(file);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    dropRef.current?.classList.remove("border-blue-500", "bg-blue-50");
    const file = e.dataTransfer.files[0];
    if (file) processFile(file);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    dropRef.current?.classList.add("border-blue-500", "bg-blue-50");
  };

  const handleDragLeave = () => {
    dropRef.current?.classList.remove("border-blue-500", "bg-blue-50");
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
      "Mall nedladdad. Öppna i Excel, fyll i data, spara som CSV och importera tillbaka.",
      "success"
    );
  };

  if (step === "choose") {
    return (
      <div className="space-y-4">
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <div className="flex items-start gap-3">
            <FileText className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
            <div className="flex-1">
              <h4 className="font-semibold text-gray-900 text-sm">
                Behöver du en mall?
              </h4>
              <p className="text-xs text-gray-600 mt-0.5 mb-2.5">
                Ladda ner en CSV-mall med alla kolumner + 2 exempelrader.
                Öppna i Excel, fyll i dina företag och importera tillbaka.
              </p>
              <Button
                variant="accent"
                size="sm"
                onClick={handleDownloadTemplate}
              >
                <Download className="w-4 h-4" /> Ladda ner mall (CSV)
              </Button>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <button
            onClick={() => handleFormatChoice("json")}
            className="border-2 border-gray-200 rounded-xl p-6 text-center hover:border-blue-500 hover:bg-blue-50 transition-colors"
          >
            <FileJson className="w-10 h-10 text-blue-600 mx-auto mb-3" />
            <h4 className="font-semibold text-gray-900">Importera JSON</h4>
            <p className="text-sm text-gray-500 mt-1">
              Importera företagsdata från en JSON-fil
            </p>
          </button>
          <button
            onClick={() => handleFormatChoice("csv")}
            className="border-2 border-gray-200 rounded-xl p-6 text-center hover:border-blue-500 hover:bg-blue-50 transition-colors"
          >
            <FileSpreadsheet className="w-10 h-10 text-blue-600 mx-auto mb-3" />
            <h4 className="font-semibold text-gray-900">Importera CSV</h4>
            <p className="text-sm text-gray-500 mt-1">
              Importera från CSV-fil (exporterad från Excel eller mallen ovan)
            </p>
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      {result ? (
        <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-4 text-emerald-800 text-sm">
          <strong className="font-semibold">Import klar!</strong>
          <br />
          Importerade: {result.imported} nya företag
          <br />
          Uppdaterade: {result.updated} befintliga företag
        </div>
      ) : (
        <>
          <p className="text-sm text-gray-500 mb-3">
            Välj en {format.toUpperCase()}-fil att importera
          </p>
          <div
            ref={dropRef}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onClick={() => fileInputRef.current?.click()}
            className="border-2 border-dashed border-gray-300 rounded-xl p-10 text-center cursor-pointer hover:border-blue-500 hover:bg-blue-50 transition-colors"
          >
            <Upload className="w-12 h-12 text-gray-400 mx-auto mb-3" />
            <p className="text-sm text-gray-500">
              Släpp filen här eller klicka för att välja
            </p>
            <p className="text-xs text-gray-400 mt-1">
              Godkända format: .{format}
            </p>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept={format === "json" ? ".json" : ".csv"}
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) processFile(file);
            }}
          />
        </>
      )}
    </div>
  );
}

type ExportViewProps = {
  searchQuery: string;
  filters: Filters;
  onComplete: () => void;
};

export function ExportView({ searchQuery, filters, onComplete }: ExportViewProps) {
  const { showToast } = useToast();
  const [type, setType] = useState<"all" | "visible" | "single">("all");
  const [selectedId, setSelectedId] = useState("");
  const companies = getAllCompanies();

  const doExport = (format: "json" | "csv") => {
    let data: string;
    let filename: string;
    const date = new Date().toISOString().slice(0, 10);

    if (type === "all") {
      data = format === "json" ? exportJSON() : exportCSV();
      filename = `alla_foretag_${date}.${format}`;
    } else if (type === "visible") {
      const visible = searchCompanies(searchQuery, filters);
      const ids = visible.map((f) => f.id);
      data = format === "json" ? exportJSON(ids) : exportCSV(ids);
      filename = `filtrerade_foretag_${date}.${format}`;
    } else {
      if (!selectedId) {
        showToast("Välj ett företag att exportera.", "error");
        return;
      }
      data =
        format === "json"
          ? exportJSON([selectedId])
          : exportCSV([selectedId]);
      const company = companies.find((f) => f.id === selectedId);
      filename = `${company?.foretagsnamn || "foretag"}_${date}.${format}`;
    }

    const mime =
      format === "json"
        ? "application/json"
        : "text/csv;charset=utf-8";
    downloadFile(data, filename, mime);
    showToast(`Exporterad som ${filename}`, "success");
    onComplete();
  };

  return (
    <div className="space-y-4">
      <label className="flex items-center gap-3 p-3 border border-gray-200 rounded-lg cursor-pointer hover:border-blue-500 hover:bg-blue-50 transition-colors">
        <input
          type="radio"
          name="export-type"
          value="all"
          checked={type === "all"}
          onChange={() => setType("all")}
          className="accent-blue-600"
        />
        <div>
          <div className="font-medium text-sm">Exportera alla företag</div>
          <div className="text-xs text-gray-500">
            {companies.length} företag
          </div>
        </div>
      </label>

      <label className="flex items-center gap-3 p-3 border border-gray-200 rounded-lg cursor-pointer hover:border-blue-500 hover:bg-blue-50 transition-colors">
        <input
          type="radio"
          name="export-type"
          value="visible"
          checked={type === "visible"}
          onChange={() => setType("visible")}
          className="accent-blue-600"
        />
        <div>
          <div className="font-medium text-sm">Exportera filtrerade företag</div>
          <div className="text-xs text-gray-500">
            Endast de företag som visas i sökresultatet
          </div>
        </div>
      </label>

      <label className="flex items-center gap-3 p-3 border border-gray-200 rounded-lg cursor-pointer hover:border-blue-500 hover:bg-blue-50 transition-colors">
        <input
          type="radio"
          name="export-type"
          value="single"
          checked={type === "single"}
          onChange={() => setType("single")}
          className="accent-blue-600"
        />
        <div>
          <div className="font-medium text-sm">Exportera enskilt företag</div>
          <div className="text-xs text-gray-500">
            Välj ett specifikt företag att exportera
          </div>
        </div>
      </label>

      {type === "single" && (
        <select
          value={selectedId}
          onChange={(e) => setSelectedId(e.target.value)}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="">-- Välj företag --</option>
          {companies.map((f) => (
            <option key={f.id} value={f.id}>
              {f.foretagsnamn}
            </option>
          ))}
        </select>
      )}

      <div className="flex gap-2 pt-2">
        <Button
          variant="primary"
          size="md"
          onClick={() => doExport("json")}
        >
          <Download className="w-4 h-4" />
          Exportera JSON
        </Button>
        <Button
          variant="accent"
          size="md"
          onClick={() => doExport("csv")}
        >
          <Download className="w-4 h-4" />
          Exportera CSV
        </Button>
      </div>
    </div>
  );
}
