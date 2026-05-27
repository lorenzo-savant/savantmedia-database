"use client";

import { useState, useEffect, useMemo } from "react";
import {
  SVERIGE_REGIONER,
  STORLEK_LABELS,
  VERIFIERINGSMETOD_LABELS,
  type CompanyFormData,
  type Company,
  type ContactFormData,
  type StorlekKategori,
  type Verifieringsmetod,
} from "@/lib/types";
import {
  checkEmail,
  classifyStorlek,
  isValidOrgnr,
  normalizeDomain,
  normalizeOrgnr,
} from "@/lib/utils";

type CompanyFormProps = {
  company?: Company | null;
  onSave: (data: CompanyFormData) => void;
  onCancel: () => void;
};

const emptyContact: ContactFormData = {
  namn: "",
  roll: "",
  telefon: "",
  email: "",
  linkedinUrl: "",
  verifierad: false,
  verifieringsmetod: "",
  verifieringskalla: "",
  verifieratAv: "",
  verifieratDatum: "",
};

const emptyForm: CompanyFormData = {
  organisationsnummer: "",
  domain: "",
  foretagsnamn: "",
  bolagsnamn: "",
  antalAnstallda: null,
  storlekKategori: "",
  storlekManuell: false,
  adress: { gata: "", postnummer: "", stad: "", region: "", land: "Sverige" },
  receptionTelefon: "",
  emailInfo: "",
  kontakter: [emptyContact, emptyContact, emptyContact],
  sokFlerKontakter: true,
  internaAnteckningar: "",
};

export function CompanyForm({ company, onSave, onCancel }: CompanyFormProps) {
  const isEdit = !!company;
  const [form, setForm] = useState<CompanyFormData>(emptyForm);
  const [errors, setErrors] = useState<string[]>([]);

  useEffect(() => {
    if (!company) {
      setForm(emptyForm);
      return;
    }
    const padded: ContactFormData[] = company.kontakter.map((c) => ({
      id: c.id,
      namn: c.namn,
      roll: c.roll,
      telefon: c.telefon,
      email: c.email,
      linkedinUrl: c.linkedinUrl,
      verifierad: c.verifierad,
      verifieringsmetod: c.verifieringsmetod,
      verifieringskalla: c.verifieringskalla,
      verifieratAv: c.verifieratAv,
      verifieratDatum: c.verifieratDatum,
    }));
    while (padded.length < 3) padded.push({ ...emptyContact });

    setForm({
      organisationsnummer: company.organisationsnummer,
      domain: company.domain,
      foretagsnamn: company.foretagsnamn,
      bolagsnamn: company.bolagsnamn,
      antalAnstallda: company.antalAnstallda,
      storlekKategori: company.storlekKategori,
      storlekManuell: company.storlekManuell,
      adress: { ...company.adress },
      receptionTelefon: company.receptionTelefon,
      emailInfo: company.emailInfo,
      kontakter: padded,
      sokFlerKontakter: company.sokFlerKontakter,
      internaAnteckningar: company.internaAnteckningar,
    });
  }, [company]);

  const autoStorlek = useMemo(
    () => classifyStorlek(form.antalAnstallda),
    [form.antalAnstallda]
  );

  const updateField = <K extends keyof CompanyFormData>(
    field: K,
    value: CompanyFormData[K]
  ) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const updateAddress = (field: keyof CompanyFormData["adress"], value: string) => {
    setForm((prev) => ({ ...prev, adress: { ...prev.adress, [field]: value } }));
  };

  const updateContact = <K extends keyof ContactFormData>(
    index: number,
    field: K,
    value: ContactFormData[K]
  ) => {
    setForm((prev) => {
      const kontakter = [...prev.kontakter];
      kontakter[index] = { ...kontakter[index], [field]: value };
      return { ...prev, kontakter };
    });
  };

  const orgnrValid = !form.organisationsnummer || isValidOrgnr(form.organisationsnummer);
  const domainNormalized = normalizeDomain(form.domain);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const errs: string[] = [];

    if (!form.foretagsnamn.trim()) {
      errs.push("Företagsnamn är obligatoriskt.");
    }
    if (form.organisationsnummer && !orgnrValid) {
      errs.push("Organisationsnummer måste innehålla 10 siffror (XXXXXX-XXXX).");
    }
    if (form.domain && !/^[a-z0-9-]+(\.[a-z0-9-]+)+$/.test(domainNormalized)) {
      errs.push("Domänen ser inte giltig ut (förväntat t.ex. foretag.se).");
    }

    if (errs.length > 0) {
      setErrors(errs);
      return;
    }
    setErrors([]);

    const filteredKontakter = form.kontakter.filter(
      (k) =>
        k.namn ||
        k.roll ||
        k.telefon ||
        k.email ||
        k.linkedinUrl ||
        k.verifierad
    );

    onSave({ ...form, kontakter: filteredKontakter });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* Identity */}
      <section>
        <h3 className="text-xs font-bold uppercase tracking-wider text-blue-600 mb-3 pb-2 border-b-2 border-blue-100">
          Identitet
        </h3>
        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">
                Företagsnamn <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={form.foretagsnamn}
                onChange={(e) => updateField("foretagsnamn", e.target.value)}
                placeholder="AB Företaget"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">
                Bolagsnamn (officiellt)
              </label>
              <input
                type="text"
                value={form.bolagsnamn}
                onChange={(e) => updateField("bolagsnamn", e.target.value)}
                placeholder="Företaget Aktiebolag"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">
                Organisationsnummer <span className="text-gray-400 font-normal">(unikt)</span>
              </label>
              <input
                type="text"
                value={form.organisationsnummer}
                onChange={(e) => updateField("organisationsnummer", e.target.value)}
                onBlur={(e) =>
                  updateField("organisationsnummer", normalizeOrgnr(e.target.value))
                }
                placeholder="556677-8899"
                className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 ${
                  orgnrValid
                    ? "border-gray-300 focus:ring-blue-500"
                    : "border-red-400 focus:ring-red-400"
                }`}
              />
              {!orgnrValid && (
                <p className="text-[11px] text-red-600 mt-1">
                  10 siffror krävs (XXXXXX-XXXX)
                </p>
              )}
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">
                Domän <span className="text-gray-400 font-normal">(för e-postmönster)</span>
              </label>
              <input
                type="text"
                value={form.domain}
                onChange={(e) => updateField("domain", e.target.value)}
                onBlur={(e) => updateField("domain", normalizeDomain(e.target.value))}
                placeholder="foretag.se"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
        </div>
      </section>

      {/* Size */}
      <section>
        <h3 className="text-xs font-bold uppercase tracking-wider text-blue-600 mb-3 pb-2 border-b-2 border-blue-100">
          Storlek
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">
              Antal anställda
            </label>
            <input
              type="number"
              min={0}
              value={form.antalAnstallda ?? ""}
              onChange={(e) => {
                const v = e.target.value;
                updateField("antalAnstallda", v === "" ? null : parseInt(v, 10));
              }}
              placeholder="t.ex. 24"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">
              Kategori
            </label>
            <select
              value={form.storlekManuell ? form.storlekKategori : autoStorlek}
              onChange={(e) => {
                const v = e.target.value as StorlekKategori;
                updateField("storlekKategori", v);
                updateField("storlekManuell", v !== autoStorlek);
              }}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="">(härled från antal)</option>
              <option value="liten">Liten (0–49)</option>
              <option value="medel">Medel (50–249)</option>
              <option value="multinationell">Multinationell (250+)</option>
            </select>
          </div>
          <div className="text-xs text-gray-500 pb-1">
            {form.storlekManuell ? (
              <>
                Manuell:{" "}
                <span className="font-semibold text-gray-700">
                  {STORLEK_LABELS[form.storlekKategori as Exclude<StorlekKategori, "">] || "—"}
                </span>{" "}
                ·{" "}
                <button
                  type="button"
                  onClick={() => {
                    updateField("storlekManuell", false);
                    updateField("storlekKategori", autoStorlek);
                  }}
                  className="text-blue-600 hover:underline"
                >
                  återställ till automatisk
                </button>
              </>
            ) : (
              <>
                Automatisk:{" "}
                <span className="font-semibold text-gray-700">
                  {autoStorlek
                    ? STORLEK_LABELS[autoStorlek as Exclude<StorlekKategori, "">]
                    : "—"}
                </span>
              </>
            )}
          </div>
        </div>
      </section>

      {/* Contact info */}
      <section>
        <h3 className="text-xs font-bold uppercase tracking-wider text-blue-600 mb-3 pb-2 border-b-2 border-blue-100">
          Allmänna kontaktuppgifter
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">
              Reception / växel
            </label>
            <input
              type="text"
              value={form.receptionTelefon}
              onChange={(e) => updateField("receptionTelefon", e.target.value)}
              placeholder="+46 8 123 456"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">
              E-post (info / kontakt)
            </label>
            <input
              type="email"
              value={form.emailInfo}
              onChange={(e) => updateField("emailInfo", e.target.value)}
              placeholder="info@foretag.se"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        </div>
      </section>

      {/* Address */}
      <section>
        <h3 className="text-xs font-bold uppercase tracking-wider text-blue-600 mb-3 pb-2 border-b-2 border-blue-100">
          Huvudkontor — adress
        </h3>
        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">
                Gatuadress
              </label>
              <input
                type="text"
                value={form.adress.gata}
                onChange={(e) => updateAddress("gata", e.target.value)}
                placeholder="Storgatan 1"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">
                Postnummer
              </label>
              <input
                type="text"
                value={form.adress.postnummer}
                onChange={(e) => updateAddress("postnummer", e.target.value)}
                placeholder="111 22"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">
                Stad
              </label>
              <input
                type="text"
                value={form.adress.stad}
                onChange={(e) => updateAddress("stad", e.target.value)}
                placeholder="Stockholm"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">
                Region
              </label>
              <select
                value={form.adress.region}
                onChange={(e) => updateAddress("region", e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">Välj region...</option>
                {SVERIGE_REGIONER.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">
                Land
              </label>
              <input
                type="text"
                value={form.adress.land}
                onChange={(e) => updateAddress("land", e.target.value)}
                placeholder="Sverige"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
        </div>
      </section>

      {/* Contacts */}
      <section>
        <div className="flex items-end justify-between mb-3 pb-2 border-b-2 border-blue-100">
          <h3 className="text-xs font-bold uppercase tracking-wider text-blue-600">
            Beslutsfattare
          </h3>
          <label className="flex items-center gap-2 text-xs text-gray-600">
            <input
              type="checkbox"
              checked={form.sokFlerKontakter}
              onChange={(e) => updateField("sokFlerKontakter", e.target.checked)}
              className="accent-blue-600"
            />
            Sök fler kontakter nästa körning
          </label>
        </div>

        {form.storlekKategori === "liten" &&
          form.kontakter.filter((k) => k.namn).length < 3 && (
            <div className="mb-3 bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-800">
              Liten företag — antagligen endast grundare/VD som DM. Det är OK
              att lämna kontakt 2 och 3 tomma; markera{" "}
              <em>Sök fler kontakter</em> som av för att hoppa över denna
              företag vid nästa scrape.
            </div>
          )}

        <div className="space-y-4">
          {[0, 1, 2].map((i) => (
            <ContactBlock
              key={i}
              index={i}
              contact={form.kontakter[i] || emptyContact}
              companyDomain={domainNormalized}
              onChange={(field, value) => updateContact(i, field, value)}
            />
          ))}
        </div>
      </section>

      {/* Notes */}
      <section>
        <h3 className="text-xs font-bold uppercase tracking-wider text-blue-600 mb-3 pb-2 border-b-2 border-blue-100">
          Interna anteckningar <span className="text-gray-400 font-normal normal-case">(synliga för kollegor)</span>
        </h3>
        <textarea
          value={form.internaAnteckningar}
          onChange={(e) => updateField("internaAnteckningar", e.target.value)}
          rows={3}
          placeholder="T.ex. 'CTO bekräftad via LinkedIn, e-post återstår' eller 'kontakta via reception, ej DM-listad'"
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </section>

      {errors.length > 0 && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3">
          {errors.map((err, i) => (
            <p key={i} className="text-sm text-red-700">
              {err}
            </p>
          ))}
        </div>
      )}

      <div className="flex justify-end gap-2 pt-2">
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50"
        >
          Avbryt
        </button>
        <button
          type="submit"
          className="px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700"
        >
          {isEdit ? "Spara ändringar" : "Spara företag"}
        </button>
      </div>
    </form>
  );
}

// ── Contact sub-component ────────────────────────────────────────────────

function ContactBlock({
  index,
  contact,
  companyDomain,
  onChange,
}: {
  index: number;
  contact: ContactFormData;
  companyDomain: string;
  onChange: <K extends keyof ContactFormData>(
    field: K,
    value: ContactFormData[K]
  ) => void;
}) {
  const emailCheck = useMemo(
    () => checkEmail(contact.email, companyDomain),
    [contact.email, companyDomain]
  );

  return (
    <div className="bg-gray-50 rounded-lg p-4 border border-gray-200">
      <div className="flex items-center justify-between mb-3">
        <p className="text-sm font-semibold text-gray-700">Kontakt {index + 1}</p>
        <label className="flex items-center gap-2 text-xs text-gray-700">
          <input
            type="checkbox"
            checked={contact.verifierad}
            onChange={(e) => {
              onChange("verifierad", e.target.checked);
              if (e.target.checked && !contact.verifieratDatum) {
                onChange("verifieratDatum", new Date().toISOString());
              }
              if (e.target.checked && !contact.verifieratAv) {
                onChange("verifieratAv", "Manuell");
              }
            }}
            className="accent-emerald-600"
          />
          <span
            className={
              contact.verifierad
                ? "font-semibold text-emerald-700"
                : "text-gray-500"
            }
          >
            Verifierad
          </span>
        </label>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
        <div>
          <label className="block text-[11px] font-semibold text-gray-600 mb-1">
            Namn (förnamn efternamn)
          </label>
          <input
            type="text"
            value={contact.namn}
            onChange={(e) => onChange("namn", e.target.value)}
            placeholder="Anna Lindberg"
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        <div>
          <label className="block text-[11px] font-semibold text-gray-600 mb-1">
            Roll
          </label>
          <input
            type="text"
            value={contact.roll}
            onChange={(e) => onChange("roll", e.target.value)}
            placeholder="VD / CTO / CFO ..."
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
        <div>
          <label className="block text-[11px] font-semibold text-gray-600 mb-1">
            Professionell e-post
          </label>
          <input
            type="email"
            value={contact.email}
            onChange={(e) => onChange("email", e.target.value)}
            placeholder="anna.lindberg@foretag.se"
            className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 ${
              contact.email && emailCheck.generic
                ? "border-red-400 focus:ring-red-400"
                : contact.email &&
                  (emailCheck.suspiciousProvider || emailCheck.domainMismatch)
                ? "border-amber-400 focus:ring-amber-400"
                : "border-gray-300 focus:ring-blue-500"
            }`}
          />
          {contact.email && emailCheck.reason && (
            <p
              className={`text-[11px] mt-1 ${
                emailCheck.generic
                  ? "text-red-600"
                  : emailCheck.suspiciousProvider || emailCheck.domainMismatch
                  ? "text-amber-700"
                  : "text-gray-500"
              }`}
            >
              {emailCheck.reason}
            </p>
          )}
        </div>
        <div>
          <label className="block text-[11px] font-semibold text-gray-600 mb-1">
            Telefon <span className="text-gray-400 font-normal">(valfri)</span>
          </label>
          <input
            type="text"
            value={contact.telefon}
            onChange={(e) => onChange("telefon", e.target.value)}
            placeholder="+46 70 123 45 67"
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      </div>

      <div className="mb-3">
        <label className="block text-[11px] font-semibold text-gray-600 mb-1">
          LinkedIn (personlig profil)
        </label>
        <input
          type="url"
          value={contact.linkedinUrl}
          onChange={(e) => onChange("linkedinUrl", e.target.value)}
          placeholder="https://www.linkedin.com/in/anna-lindberg/"
          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>

      {contact.verifierad && (
        <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 space-y-2">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] font-semibold text-emerald-800 mb-1">
                Verifieringsmetod
              </label>
              <select
                value={contact.verifieringsmetod}
                onChange={(e) =>
                  onChange(
                    "verifieringsmetod",
                    e.target.value as Verifieringsmetod
                  )
                }
                className="w-full px-3 py-2 border border-emerald-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-emerald-500"
              >
                <option value="">Välj...</option>
                {Object.entries(VERIFIERINGSMETOD_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-emerald-800 mb-1">
                Verifierad av
              </label>
              <input
                type="text"
                value={contact.verifieratAv}
                onChange={(e) => onChange("verifieratAv", e.target.value)}
                placeholder="Lorenzo / scraper-name / kollega"
                className="w-full px-3 py-2 border border-emerald-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
              />
            </div>
          </div>
          <div>
            <label className="block text-[11px] font-semibold text-emerald-800 mb-1">
              Källa (URL till bevis)
            </label>
            <input
              type="url"
              value={contact.verifieringskalla}
              onChange={(e) => onChange("verifieringskalla", e.target.value)}
              placeholder="https://foretag.se/kontakt eller LinkedIn-URL"
              className="w-full px-3 py-2 border border-emerald-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
            />
          </div>
        </div>
      )}
    </div>
  );
}
