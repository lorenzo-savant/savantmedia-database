"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  ChevronLeft,
  Pencil,
  Trash2,
  Phone,
  Mail,
  MapPin,
  Clock,
  Linkedin,
  ShieldCheck,
  ShieldAlert,
  Hash,
  Globe,
  Users,
  ExternalLink,
  Archive,
  RotateCcw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  getCompanyById,
  archiveCompany,
  restoreCompany,
  permanentlyDeleteCompany,
  updateCompany,
  DuplicateOrgnrError,
} from "@/lib/data";
import { formatDate } from "@/lib/utils";
import { useToast } from "@/components/ui/toast";
import { Modal } from "@/components/ui/modal";
import { CompanyForm } from "@/components/company-form";
import {
  STORLEK_LABELS,
  VERIFIERINGSMETOD_LABELS,
  type Company,
  type CompanyFormData,
  type StorlekKategori,
} from "@/lib/types";

const storlekColors: Record<Exclude<StorlekKategori, "">, string> = {
  liten: "bg-sky-50 text-sky-700 border-sky-200",
  medel: "bg-violet-50 text-violet-700 border-violet-200",
  multinationell: "bg-indigo-50 text-indigo-700 border-indigo-200",
};

export default function CompanyDetailPage() {
  const params = useParams();
  const router = useRouter();
  const { showToast } = useToast();
  const [company, setCompany] = useState<Company | null>(null);
  const [loading, setLoading] = useState(true);
  const [editOpen, setEditOpen] = useState(false);

  const id = params.id as string;

  useEffect(() => {
    const c = getCompanyById(id);
    setCompany(c);
    setLoading(false);
  }, [id]);

  const handleArchive = () => {
    if (!company) return;
    if (
      !confirm(
        `Arkivera "${company.foretagsnamn}"?\n\nFöretaget göms från listan men finns kvar i Arkiv där det kan återställas när som helst.`
      )
    )
      return;
    archiveCompany(id);
    showToast(
      `"${company.foretagsnamn}" har arkiverats. Kan återställas från Arkiv.`,
      "info"
    );
    router.push("/");
  };

  const handleRestore = () => {
    if (!company) return;
    restoreCompany(id);
    setCompany(getCompanyById(id));
    showToast(`"${company.foretagsnamn}" har återställts.`, "success");
  };

  const handlePermanentDelete = () => {
    if (!company) return;
    if (
      !confirm(
        `TA BORT PERMANENT: "${company.foretagsnamn}".\n\nDetta går INTE att ångra. Företaget och alla dess kontakter försvinner för alltid.\n\nÄr du helt säker?`
      )
    )
      return;
    permanentlyDeleteCompany(id);
    showToast(`"${company.foretagsnamn}" har raderats permanent.`, "info");
    router.push("/arkiv");
  };

  const handleEdit = (data: CompanyFormData) => {
    try {
      updateCompany(id, data);
      const updated = getCompanyById(id);
      setCompany(updated);
      setEditOpen(false);
      showToast("Företaget har uppdaterats!", "success");
    } catch (err) {
      if (err instanceof DuplicateOrgnrError) {
        showToast(
          `Organisationsnummer ${data.organisationsnummer} finns redan på ett annat företag.`,
          "error"
        );
      } else {
        showToast(
          err instanceof Error ? err.message : "Något gick fel",
          "error"
        );
      }
    }
  };

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto py-12 text-center text-gray-400">
        Laddar...
      </div>
    );
  }

  if (!company) {
    return (
      <div className="max-w-3xl mx-auto py-12 text-center">
        <h2 className="text-xl font-bold text-gray-700">Företaget hittades inte</h2>
        <Button variant="outline" className="mt-4" onClick={() => router.push("/")}>
          <ChevronLeft className="w-4 h-4" /> Tillbaka till listan
        </Button>
      </div>
    );
  }

  const kontakter = company.kontakter || [];
  const verifiedCount = kontakter.filter((k) => k.verifierad).length;
  const storlek = company.storlekKategori as Exclude<StorlekKategori, "">;

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 py-5">
      <button
        onClick={() => router.push("/")}
        className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-blue-600 mb-5"
      >
        <ChevronLeft className="w-4 h-4" /> Tillbaka till listan
      </button>

      <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
        <div className="px-6 py-5 border-b border-gray-100 flex justify-between items-start gap-4">
          <div className="min-w-0">
            <h1 className="text-xl font-bold text-gray-900">
              {company.foretagsnamn}
            </h1>
            {company.bolagsnamn && company.bolagsnamn !== company.foretagsnamn && (
              <p className="text-sm text-gray-500 mt-0.5">{company.bolagsnamn}</p>
            )}
            <div className="flex flex-wrap gap-1.5 mt-2">
              {company.organisationsnummer && (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full border border-gray-200 bg-gray-50 text-gray-700">
                  <Hash className="w-3 h-3" />
                  {company.organisationsnummer}
                </span>
              )}
              {company.domain && (
                <a
                  href={`https://${company.domain}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full border border-blue-200 bg-blue-50 text-blue-700 hover:bg-blue-100"
                >
                  <Globe className="w-3 h-3" />
                  {company.domain}
                  <ExternalLink className="w-3 h-3" />
                </a>
              )}
              {storlek && (
                <span
                  className={`inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-semibold rounded-full border ${storlekColors[storlek]}`}
                  title={
                    company.storlekManuell
                      ? "Manuellt vald storlek"
                      : "Härled från antal anställda"
                  }
                >
                  <Users className="w-3 h-3" />
                  {STORLEK_LABELS[storlek]}
                  {company.antalAnstallda != null && (
                    <span className="text-[10px] font-normal opacity-75">
                      · {company.antalAnstallda} anst.
                    </span>
                  )}
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
                <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full border border-slate-200 bg-slate-50 text-slate-600">
                  hoppa över vid scraping
                </span>
              )}
            </div>
          </div>
          <div className="flex gap-2 shrink-0 flex-wrap justify-end">
            {!company.arkiverad ? (
              <>
                <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
                  <Pencil className="w-4 h-4" /> Redigera
                </Button>
                <Button variant="danger" size="sm" onClick={handleArchive}>
                  <Archive className="w-4 h-4" /> Arkivera
                </Button>
              </>
            ) : (
              <>
                <Button variant="primary" size="sm" onClick={handleRestore}>
                  <RotateCcw className="w-4 h-4" /> Återställ
                </Button>
                <Button variant="danger" size="sm" onClick={handlePermanentDelete}>
                  <Trash2 className="w-4 h-4" /> Radera permanent
                </Button>
              </>
            )}
          </div>
        </div>

        {company.arkiverad && (
          <div className="px-6 py-3 bg-amber-50 border-b border-amber-200 flex items-start gap-2 text-sm text-amber-900">
            <Archive className="w-4 h-4 shrink-0 mt-0.5" />
            <div>
              <strong className="font-semibold">Detta företag är arkiverat.</strong>{" "}
              Det visas inte i huvudlistan. Arkiverat{" "}
              {company.arkiveradDatum ? formatDate(company.arkiveradDatum) : ""}
              {company.arkiveradAv ? ` av ${company.arkiveradAv}` : ""}. Klicka{" "}
              <em>Återställ</em> för att lägga tillbaka det i databasen.
            </div>
          </div>
        )}

        <div className="px-6 py-5 space-y-6">
          {/* Allmänna kontaktuppgifter */}
          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-blue-600 mb-3 pb-2 border-b-2 border-blue-100">
              Allmänna kontaktuppgifter
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                  Reception / växel
                </p>
                <p className="text-sm text-gray-800 flex items-center gap-1.5 mt-0.5">
                  <Phone className="w-4 h-4 text-gray-400" />
                  {company.receptionTelefon || "-"}
                </p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                  E-post (info)
                </p>
                <p className="text-sm text-gray-800 flex items-center gap-1.5 mt-0.5">
                  <Mail className="w-4 h-4 text-gray-400" />
                  {company.emailInfo || "-"}
                </p>
              </div>
            </div>
          </section>

          {/* Address */}
          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-blue-600 mb-3 pb-2 border-b-2 border-blue-100">
              Huvudkontor — adress
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-y-3 gap-x-6">
              {[
                ["Gatuadress", company.adress.gata],
                ["Postnummer", company.adress.postnummer],
                ["Stad", company.adress.stad],
                ["Region", company.adress.region],
                ["Land", company.adress.land],
              ].map(([label, value]) => (
                <div key={label as string}>
                  <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                    {label as string}
                  </p>
                  <p className="text-sm text-gray-800 mt-0.5">
                    {(value as string) || "-"}
                  </p>
                </div>
              ))}
            </div>
            <p className="text-[11px] text-gray-400 mt-2 flex items-start gap-1">
              <MapPin className="w-3 h-3 mt-0.5 shrink-0" />
              Alla fysiska adresser betraktas som filialer av samma juridiska
              enhet identifierad genom organisationsnumret.
            </p>
          </section>

          {/* Contacts */}
          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-blue-600 mb-3 pb-2 border-b-2 border-blue-100">
              Beslutsfattare ({kontakter.length})
            </h3>
            {kontakter.length > 0 ? (
              <div className="space-y-3">
                {kontakter.map((k) => (
                  <div
                    key={k.id}
                    className={`rounded-lg p-4 border ${
                      k.verifierad
                        ? "bg-emerald-50/40 border-emerald-200"
                        : "bg-gray-50 border-gray-200"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-semibold text-gray-900">
                          {k.namn || "(namn saknas)"}
                        </p>
                        <p className="text-sm text-blue-700">{k.roll || "-"}</p>
                      </div>
                      {k.verifierad ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-semibold rounded-full border border-emerald-300 bg-emerald-100 text-emerald-800 shrink-0">
                          <ShieldCheck className="w-3 h-3" />
                          Verifierad
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-full border border-gray-200 bg-white text-gray-500 shrink-0">
                          <ShieldAlert className="w-3 h-3" />
                          Ej verifierad
                        </span>
                      )}
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-3 text-sm">
                      <div>
                        <p className="text-[11px] font-semibold text-gray-400 uppercase">
                          E-post
                        </p>
                        <p className="text-gray-800 flex items-center gap-1.5 mt-0.5 break-all">
                          <Mail className="w-3.5 h-3.5 text-gray-400 shrink-0" />
                          {k.email || "-"}
                        </p>
                      </div>
                      <div>
                        <p className="text-[11px] font-semibold text-gray-400 uppercase">
                          Telefon
                        </p>
                        <p className="text-gray-800 flex items-center gap-1.5 mt-0.5">
                          <Phone className="w-3.5 h-3.5 text-gray-400 shrink-0" />
                          {k.telefon || "-"}
                        </p>
                      </div>
                      <div>
                        <p className="text-[11px] font-semibold text-gray-400 uppercase">
                          LinkedIn
                        </p>
                        {k.linkedinUrl ? (
                          <a
                            href={k.linkedinUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-600 hover:underline flex items-center gap-1.5 mt-0.5 break-all"
                          >
                            <Linkedin className="w-3.5 h-3.5 shrink-0" />
                            Profil
                            <ExternalLink className="w-3 h-3" />
                          </a>
                        ) : (
                          <p className="text-gray-400 flex items-center gap-1.5 mt-0.5">
                            <Linkedin className="w-3.5 h-3.5 shrink-0" />
                            -
                          </p>
                        )}
                      </div>
                    </div>

                    {k.verifierad && (
                      <div className="mt-3 pt-3 border-t border-emerald-200 grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs text-emerald-900">
                        <div>
                          <p className="text-[10px] font-semibold uppercase opacity-70">
                            Metod
                          </p>
                          <p className="mt-0.5">
                            {k.verifieringsmetod
                              ? VERIFIERINGSMETOD_LABELS[
                                  k.verifieringsmetod as keyof typeof VERIFIERINGSMETOD_LABELS
                                ] || k.verifieringsmetod
                              : "-"}
                          </p>
                        </div>
                        <div>
                          <p className="text-[10px] font-semibold uppercase opacity-70">
                            Av
                          </p>
                          <p className="mt-0.5">{k.verifieratAv || "-"}</p>
                        </div>
                        <div>
                          <p className="text-[10px] font-semibold uppercase opacity-70">
                            Datum
                          </p>
                          <p className="mt-0.5">
                            {k.verifieratDatum ? formatDate(k.verifieratDatum) : "-"}
                          </p>
                        </div>
                        {k.verifieringskalla && (
                          <div className="sm:col-span-3">
                            <p className="text-[10px] font-semibold uppercase opacity-70">
                              Källa
                            </p>
                            <a
                              href={k.verifieringskalla}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-emerald-700 hover:underline break-all flex items-center gap-1 mt-0.5"
                            >
                              {k.verifieringskalla}
                              <ExternalLink className="w-3 h-3 shrink-0" />
                            </a>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-400">Inga kontakter registrerade</p>
            )}
          </section>

          {/* Internal notes */}
          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-blue-600 mb-3 pb-2 border-b-2 border-blue-100">
              Interna anteckningar (övriga)
            </h3>
            {company.internaAnteckningar ? (
              <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 text-sm text-gray-800 whitespace-pre-wrap">
                {company.internaAnteckningar}
              </div>
            ) : (
              <p className="text-sm text-gray-400">Inga anteckningar</p>
            )}
          </section>

          {/* Timestamps */}
          <section>
            <h3 className="text-xs font-bold uppercase tracking-wider text-blue-600 mb-3 pb-2 border-b-2 border-blue-100">
              Tidsstämplar
            </h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                  Skapad
                </p>
                <p className="text-sm text-gray-800 flex items-center gap-1.5 mt-0.5">
                  <Clock className="w-4 h-4 text-gray-400" />
                  {formatDate(company.skapadDatum)}
                </p>
              </div>
              <div>
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
                  Senast ändrad
                </p>
                <p className="text-sm text-gray-800 flex items-center gap-1.5 mt-0.5">
                  <Clock className="w-4 h-4 text-gray-400" />
                  {formatDate(company.senastAndrad)}
                </p>
              </div>
            </div>
          </section>
        </div>
      </div>

      <Modal
        open={editOpen}
        onClose={() => setEditOpen(false)}
        title={`Redigera: ${company.foretagsnamn}`}
      >
        <CompanyForm
          company={company}
          onSave={handleEdit}
          onCancel={() => setEditOpen(false)}
        />
      </Modal>
    </div>
  );
}
