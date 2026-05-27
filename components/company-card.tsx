"use client";

import {
  Phone,
  Mail,
  MapPin,
  Clock,
  Eye,
  Pencil,
  Trash2,
  Linkedin,
  ShieldCheck,
  ShieldAlert,
  Hash,
  Globe,
  Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatDate } from "@/lib/utils";
import { STORLEK_LABELS, type Company, type StorlekKategori } from "@/lib/types";

type CompanyCardProps = {
  company: Company;
  onView: (id: string) => void;
  onEdit: (id: string) => void;
  onDelete: (id: string) => void;
};

const storlekColors: Record<Exclude<StorlekKategori, "">, string> = {
  liten: "bg-sky-50 text-sky-700 border-sky-200",
  medel: "bg-violet-50 text-violet-700 border-violet-200",
  multinationell: "bg-indigo-50 text-indigo-700 border-indigo-200",
};

export function CompanyCard({ company, onView, onEdit, onDelete }: CompanyCardProps) {
  const { adress, kontakter } = company;
  const verifiedCount = kontakter.filter((k) => k.verifierad).length;
  const storlek = company.storlekKategori;

  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm hover:shadow-md transition-shadow">
      <div className="px-5 py-4 border-b border-gray-100">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-base font-bold text-gray-900 leading-tight truncate">
              {company.foretagsnamn}
            </h3>
            {company.organisationsnummer && (
              <p className="text-[11px] text-gray-500 mt-0.5 flex items-center gap-1">
                <Hash className="w-3 h-3" />
                {company.organisationsnummer}
              </p>
            )}
          </div>
          <div className="flex gap-1 shrink-0">
            <Button variant="ghost" size="icon" onClick={() => onView(company.id)} title="Visa detaljer">
              <Eye className="w-4 h-4" />
            </Button>
            <Button variant="ghost" size="icon" onClick={() => onEdit(company.id)} title="Redigera">
              <Pencil className="w-4 h-4" />
            </Button>
            <Button variant="ghost" size="icon" onClick={() => onDelete(company.id)} title="Ta bort">
              <Trash2 className="w-4 h-4" />
            </Button>
          </div>
        </div>

        <div className="flex flex-wrap gap-1.5 mt-2">
          {storlek && (
            <span
              className={`inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-semibold rounded-full border ${storlekColors[storlek as Exclude<StorlekKategori, "">]}`}
              title={
                company.storlekManuell
                  ? "Storlek satt manuellt"
                  : "Härled från antal anställda"
              }
            >
              <Users className="w-3 h-3" />
              {STORLEK_LABELS[storlek as Exclude<StorlekKategori, "">]}
              {company.antalAnstallda != null && (
                <span className="text-[10px] font-normal opacity-75">
                  · {company.antalAnstallda}
                </span>
              )}
            </span>
          )}
          {company.domain && (
            <span
              className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full border border-gray-200 bg-gray-50 text-gray-700"
              title="Domän"
            >
              <Globe className="w-3 h-3" />
              {company.domain}
            </span>
          )}
          {kontakter.length > 0 && (
            <span
              className={`inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-semibold rounded-full border ${
                verifiedCount === kontakter.length
                  ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                  : verifiedCount > 0
                  ? "bg-amber-50 text-amber-800 border-amber-200"
                  : "bg-gray-50 text-gray-600 border-gray-200"
              }`}
              title="Verifierade kontakter"
            >
              {verifiedCount === kontakter.length && verifiedCount > 0 ? (
                <ShieldCheck className="w-3 h-3" />
              ) : (
                <ShieldAlert className="w-3 h-3" />
              )}
              {verifiedCount}/{kontakter.length} verifierad
            </span>
          )}
          {!company.sokFlerKontakter && (
            <span
              className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full border border-slate-200 bg-slate-50 text-slate-600"
              title="Hoppa över vid nästa scraping"
            >
              hoppa över
            </span>
          )}
        </div>
      </div>

      <div className="px-5 py-3 space-y-2">
        <div className="flex items-start gap-2 text-sm text-gray-600">
          <MapPin className="w-4 h-4 mt-0.5 shrink-0 text-gray-400" />
          <span className="truncate">
            {adress.gata && `${adress.gata}, `}
            {adress.postnummer && `${adress.postnummer} `}
            {adress.stad}
          </span>
        </div>
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <Phone className="w-4 h-4 shrink-0 text-gray-400" />
          <span>{company.receptionTelefon || "-"}</span>
        </div>
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <Mail className="w-4 h-4 shrink-0 text-gray-400" />
          <span className="truncate">{company.emailInfo || "-"}</span>
        </div>
      </div>

      {kontakter.length > 0 && (
        <div className="px-5 pb-3">
          <p className="text-[11px] font-bold uppercase tracking-wider text-gray-400 mb-2 pt-2 border-t border-gray-100">
            Beslutsfattare ({kontakter.length})
          </p>
          <div className="space-y-1.5">
            {kontakter.map((k) => (
              <div
                key={k.id}
                className="px-3 py-2 bg-gray-50 rounded-lg text-xs space-y-0.5"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold text-gray-800 truncate">
                    {k.namn}
                  </span>
                  {k.verifierad ? (
                    <ShieldCheck
                      className="w-3.5 h-3.5 text-emerald-600 shrink-0"
                      aria-label="Verifierad"
                    />
                  ) : (
                    <ShieldAlert
                      className="w-3.5 h-3.5 text-gray-300 shrink-0"
                      aria-label="Ej verifierad"
                    />
                  )}
                </div>
                <div className="text-blue-600 font-medium text-[11px]">
                  {k.roll}
                </div>
                <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-gray-600">
                  <div className="flex items-center gap-1 truncate">
                    <Mail className="w-3 h-3 text-gray-400 shrink-0" />
                    <span className="truncate">{k.email || "-"}</span>
                  </div>
                  <div className="flex items-center gap-1 truncate">
                    {k.linkedinUrl ? (
                      <a
                        href={k.linkedinUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1 text-blue-600 hover:underline truncate"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Linkedin className="w-3 h-3 shrink-0" />
                        LinkedIn
                      </a>
                    ) : (
                      <>
                        <Phone className="w-3 h-3 text-gray-400 shrink-0" />
                        <span className="truncate">{k.telefon || "-"}</span>
                      </>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {kontakter.length === 0 && (
        <div className="px-5 pb-3">
          <p className="text-xs text-gray-400 py-1">Inga kontakter registrerade</p>
        </div>
      )}

      <div className="px-5 py-2.5 bg-gray-50 border-t border-gray-100 flex justify-between items-center text-[11px] text-gray-400">
        <span>{adress.region || adress.land || "-"}</span>
        <span className="flex items-center gap-1">
          <Clock className="w-3 h-3" />
          {formatDate(company.senastAndrad)}
        </span>
      </div>
    </div>
  );
}
