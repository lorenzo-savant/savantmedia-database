import type {
  Company,
  Contact,
  ContactFormData,
  CompanyFormData,
  Filters,
  ImportResult,
} from "./types";
import {
  classifyStorlek,
  normalizeDomain,
  normalizeOrgnr,
  isValidOrgnr,
} from "./utils";

const STORAGE_KEY = "savantmedia_foretagsdb";

function generateId(): string {
  return "f" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

function generateContactId(): string {
  return "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

// ── Schema v1 → v2 migration ─────────────────────────────────────────────

type LegacyContact = Partial<Contact> & {
  namn?: string;
  roll?: string;
  telefon?: string;
  email?: string;
};

function migrateContact(c: LegacyContact): Contact {
  return {
    id: c.id || generateContactId(),
    namn: c.namn ?? "",
    roll: c.roll ?? "",
    telefon: c.telefon ?? "",
    email: c.email ?? "",
    linkedinUrl: c.linkedinUrl ?? "",
    verifierad: c.verifierad ?? false,
    verifieringsmetod: c.verifieringsmetod ?? "",
    verifieringskalla: c.verifieringskalla ?? "",
    verifieratAv: c.verifieratAv ?? "",
    verifieratDatum: c.verifieratDatum ?? "",
  };
}

function migrateCompany(raw: Record<string, unknown>): Company {
  const r = raw as Partial<Company> & Record<string, unknown>;
  const antal = (r.antalAnstallda as number | null | undefined) ?? null;
  const storlekManuell = !!r.storlekManuell;
  const storlek =
    (r.storlekKategori as Company["storlekKategori"]) ||
    (storlekManuell ? "" : classifyStorlek(antal));

  return {
    id: (r.id as string) || generateId(),
    schemaVersion: 2,
    organisationsnummer: normalizeOrgnr((r.organisationsnummer as string) || ""),
    domain: normalizeDomain((r.domain as string) || ""),
    foretagsnamn: (r.foretagsnamn as string) || "",
    bolagsnamn: (r.bolagsnamn as string) || (r.foretagsnamn as string) || "",
    antalAnstallda: antal,
    storlekKategori: storlek,
    storlekManuell,
    adress: {
      gata: r.adress && (r.adress as any).gata ? (r.adress as any).gata : "",
      postnummer:
        r.adress && (r.adress as any).postnummer
          ? (r.adress as any).postnummer
          : "",
      stad: r.adress && (r.adress as any).stad ? (r.adress as any).stad : "",
      region:
        r.adress && (r.adress as any).region ? (r.adress as any).region : "",
      land: r.adress && (r.adress as any).land ? (r.adress as any).land : "",
    },
    receptionTelefon: (r.receptionTelefon as string) || "",
    emailInfo: (r.emailInfo as string) || "",
    sniPrimaryKod: (r.sniPrimaryKod as string) || "",
    sniBranscher: (r.sniBranscher as string) || "",
    sniHuvudgrupp: (r.sniHuvudgrupp as string) || "",
    sniAllaKoder: Array.isArray(r.sniAllaKoder)
      ? (r.sniAllaKoder as string[])
      : [],
    kontakter: Array.isArray(r.kontakter)
      ? (r.kontakter as LegacyContact[]).map(migrateContact)
      : [],
    sokFlerKontakter:
      typeof r.sokFlerKontakter === "boolean" ? r.sokFlerKontakter : true,
    internaAnteckningar: (r.internaAnteckningar as string) || "",
    arkiverad: !!r.arkiverad,
    arkiveradDatum: (r.arkiveradDatum as string) || "",
    arkiveradAv: (r.arkiveradAv as string) || "",
    skapadDatum: (r.skapadDatum as string) || new Date().toISOString(),
    senastAndrad: (r.senastAndrad as string) || new Date().toISOString(),
  };
}

// ── Storage ──────────────────────────────────────────────────────────────

export function getAllCompanies(): Company[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    let needsRewrite = false;
    const migrated = parsed.map((item) => {
      const r = item as Record<string, unknown>;
      if (r.schemaVersion === 2) return r as unknown as Company;
      needsRewrite = true;
      return migrateCompany(r);
    });
    if (needsRewrite) saveCompanies(migrated as Company[]);
    return migrated as Company[];
  } catch {
    return [];
  }
}

function saveCompanies(data: Company[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
}

/** Overwrite the entire local cache (used by sync/export to refresh from Supabase). */
export function replaceAllCompanies(data: Company[]): void {
  if (typeof window === "undefined") return;
  saveCompanies(data);
}

/** Returns active (non-archived) companies. */
export function getActiveCompanies(): Company[] {
  return getAllCompanies().filter((c) => !c.arkiverad);
}

/** Returns only archived companies, sorted by archiveradDatum desc. */
export function getArchivedCompanies(): Company[] {
  return getAllCompanies()
    .filter((c) => c.arkiverad)
    .sort((a, b) => (b.arkiveradDatum || "").localeCompare(a.arkiveradDatum || ""));
}

/** Lookup by id — returns both active and archived. */
export function getCompanyById(id: string): Company | null {
  return getAllCompanies().find((f) => f.id === id) || null;
}

/** Lookup by org.nr — returns both active and archived. */
export function getCompanyByOrgnr(orgnr: string): Company | null {
  const target = normalizeOrgnr(orgnr);
  if (!target) return null;
  return (
    getAllCompanies().find(
      (f) => normalizeOrgnr(f.organisationsnummer) === target
    ) || null
  );
}

// ── Form → Company ────────────────────────────────────────────────────────

function buildContactsFromForm(items: ContactFormData[]): Contact[] {
  return items.map((k) => ({
    id: k.id || generateContactId(),
    namn: k.namn || "",
    roll: k.roll || "",
    telefon: k.telefon || "",
    email: k.email || "",
    linkedinUrl: k.linkedinUrl || "",
    verifierad: !!k.verifierad,
    verifieringsmetod: k.verifieringsmetod || "",
    verifieringskalla: k.verifieringskalla || "",
    verifieratAv: k.verifieratAv || "",
    verifieratDatum: k.verifieratDatum || "",
  }));
}

function normalizeForm(data: CompanyFormData): CompanyFormData {
  const orgnr = normalizeOrgnr(data.organisationsnummer || "");
  const domain = normalizeDomain(data.domain || "");
  const antal =
    data.antalAnstallda == null || isNaN(data.antalAnstallda as number)
      ? null
      : Math.max(0, Math.floor(data.antalAnstallda as number));
  const storlek = data.storlekManuell
    ? data.storlekKategori
    : classifyStorlek(antal);
  return {
    ...data,
    organisationsnummer: orgnr,
    domain,
    antalAnstallda: antal,
    storlekKategori: storlek,
  };
}

export class DuplicateOrgnrError extends Error {
  existingId: string;
  existingIsArchived: boolean;
  constructor(orgnr: string, existingId: string, existingIsArchived = false) {
    super(
      existingIsArchived
        ? `Organisationsnummer ${orgnr} finns redan i arkivet. Återställ det istället.`
        : `Organisationsnummer ${orgnr} finns redan i databasen.`
    );
    this.name = "DuplicateOrgnrError";
    this.existingId = existingId;
    this.existingIsArchived = existingIsArchived;
  }
}

export function addCompany(data: CompanyFormData): Company {
  const norm = normalizeForm(data);

  if (norm.organisationsnummer) {
    if (!isValidOrgnr(norm.organisationsnummer)) {
      throw new Error(
        "Ogiltigt organisationsnummer. Förväntat format: XXXXXX-XXXX (10 siffror)."
      );
    }
    const existing = getCompanyByOrgnr(norm.organisationsnummer);
    if (existing) {
      throw new DuplicateOrgnrError(
        norm.organisationsnummer,
        existing.id,
        existing.arkiverad
      );
    }
  }

  const companies = getAllCompanies();
  const company: Company = {
    id: generateId(),
    schemaVersion: 2,
    organisationsnummer: norm.organisationsnummer,
    domain: norm.domain,
    foretagsnamn: norm.foretagsnamn,
    bolagsnamn: norm.bolagsnamn || norm.foretagsnamn,
    antalAnstallda: norm.antalAnstallda,
    storlekKategori: norm.storlekKategori,
    storlekManuell: norm.storlekManuell,
    adress: norm.adress,
    receptionTelefon: norm.receptionTelefon,
    emailInfo: norm.emailInfo,
    sniPrimaryKod: norm.sniPrimaryKod ?? "",
    sniBranscher: norm.sniBranscher ?? "",
    sniHuvudgrupp: norm.sniHuvudgrupp ?? "",
    sniAllaKoder: [],
    kontakter: buildContactsFromForm(norm.kontakter),
    sokFlerKontakter: norm.sokFlerKontakter,
    internaAnteckningar: norm.internaAnteckningar,
    arkiverad: false,
    arkiveradDatum: "",
    arkiveradAv: "",
    skapadDatum: new Date().toISOString(),
    senastAndrad: new Date().toISOString(),
  };
  companies.push(company);
  saveCompanies(companies);
  return company;
}

export function updateCompany(
  id: string,
  data: CompanyFormData
): Company | null {
  const norm = normalizeForm(data);

  if (norm.organisationsnummer) {
    if (!isValidOrgnr(norm.organisationsnummer)) {
      throw new Error(
        "Ogiltigt organisationsnummer. Förväntat format: XXXXXX-XXXX (10 siffror)."
      );
    }
    const existing = getCompanyByOrgnr(norm.organisationsnummer);
    if (existing && existing.id !== id) {
      throw new DuplicateOrgnrError(
        norm.organisationsnummer,
        existing.id,
        existing.arkiverad
      );
    }
  }

  const companies = getAllCompanies();
  const index = companies.findIndex((f) => f.id === id);
  if (index === -1) return null;

  const prev = companies[index];
  const merged: Company = {
    ...prev,
    organisationsnummer: norm.organisationsnummer,
    domain: norm.domain,
    foretagsnamn: norm.foretagsnamn,
    bolagsnamn: norm.bolagsnamn || norm.foretagsnamn,
    antalAnstallda: norm.antalAnstallda,
    storlekKategori: norm.storlekKategori,
    storlekManuell: norm.storlekManuell,
    adress: norm.adress,
    receptionTelefon: norm.receptionTelefon,
    emailInfo: norm.emailInfo,
    sniPrimaryKod: norm.sniPrimaryKod ?? prev.sniPrimaryKod ?? "",
    sniBranscher: norm.sniBranscher ?? prev.sniBranscher ?? "",
    sniHuvudgrupp: norm.sniHuvudgrupp ?? prev.sniHuvudgrupp ?? "",
    kontakter: buildContactsFromForm(norm.kontakter),
    sokFlerKontakter: norm.sokFlerKontakter,
    internaAnteckningar: norm.internaAnteckningar,
    senastAndrad: new Date().toISOString(),
  };
  companies[index] = merged;
  saveCompanies(companies);
  return merged;
}

/**
 * Soft-delete: marks the company as archived. Use `restoreCompany` to undo
 * or `permanentlyDeleteCompany` to wipe forever.
 */
export function archiveCompany(id: string, archivedBy = "Manuell"): boolean {
  const companies = getAllCompanies();
  const idx = companies.findIndex((c) => c.id === id);
  if (idx === -1 || companies[idx].arkiverad) return false;
  companies[idx] = {
    ...companies[idx],
    arkiverad: true,
    arkiveradDatum: new Date().toISOString(),
    arkiveradAv: archivedBy,
    senastAndrad: new Date().toISOString(),
  };
  saveCompanies(companies);
  return true;
}

export function restoreCompany(id: string): boolean {
  const companies = getAllCompanies();
  const idx = companies.findIndex((c) => c.id === id);
  if (idx === -1 || !companies[idx].arkiverad) return false;
  companies[idx] = {
    ...companies[idx],
    arkiverad: false,
    arkiveradDatum: "",
    arkiveradAv: "",
    senastAndrad: new Date().toISOString(),
  };
  saveCompanies(companies);
  return true;
}

export function permanentlyDeleteCompany(id: string): boolean {
  const companies = getAllCompanies();
  const filtered = companies.filter((f) => f.id !== id);
  if (filtered.length === companies.length) return false;
  saveCompanies(filtered);
  return true;
}

/**
 * Backward-compat alias: now performs soft-delete (archive).
 * To permanently delete, call `permanentlyDeleteCompany`.
 */
export function deleteCompany(id: string): boolean {
  return archiveCompany(id);
}

// ── Search & filter ───────────────────────────────────────────────────────

export function searchCompanies(
  query: string,
  filters: Filters,
  options: { includeArchived?: boolean; archivedOnly?: boolean } = {}
): Company[] {
  let results = getAllCompanies();
  if (options.archivedOnly) {
    results = results.filter((c) => c.arkiverad);
  } else if (!options.includeArchived) {
    results = results.filter((c) => !c.arkiverad);
  }
  const q = query.toLowerCase().trim();

  if (q) {
    results = results.filter(
      (f) =>
        f.foretagsnamn.toLowerCase().includes(q) ||
        f.bolagsnamn.toLowerCase().includes(q) ||
        f.organisationsnummer.toLowerCase().includes(q) ||
        f.domain.toLowerCase().includes(q) ||
        f.adress.stad.toLowerCase().includes(q) ||
        f.adress.region.toLowerCase().includes(q) ||
        f.adress.land.toLowerCase().includes(q) ||
        f.emailInfo.toLowerCase().includes(q) ||
        (f.sniBranscher || "").toLowerCase().includes(q) ||
        (f.sniPrimaryKod || "").toLowerCase().includes(q) ||
        f.kontakter.some(
          (k) =>
            k.namn.toLowerCase().includes(q) ||
            k.roll.toLowerCase().includes(q) ||
            k.email.toLowerCase().includes(q)
        )
    );
  }

  if (filters.region) {
    results = results.filter(
      (f) => f.adress.region.toLowerCase() === filters.region.toLowerCase()
    );
  }

  if (filters.stad) {
    results = results.filter((f) =>
      f.adress.stad.toLowerCase().includes(filters.stad.toLowerCase())
    );
  }

  if (filters.land) {
    results = results.filter(
      (f) => f.adress.land.toLowerCase() === filters.land.toLowerCase()
    );
  }

  if (filters.storlek) {
    results = results.filter((f) => f.storlekKategori === filters.storlek);
  }

  if (filters.sniHuvudgrupp) {
    results = results.filter(
      (f) => (f.sniHuvudgrupp || "").toUpperCase() === filters.sniHuvudgrupp.toUpperCase()
    );
  }

  if (filters.endastVerifierade) {
    results = results.filter((f) =>
      f.kontakter.some((k) => k.verifierad)
    );
  }

  if (filters.endastMedDomain) {
    results = results.filter((f) => !!f.domain);
  }

  if (filters.andradFran) {
    const from = new Date(filters.andradFran);
    results = results.filter((f) => new Date(f.senastAndrad) >= from);
  }

  if (filters.andradTill) {
    const to = new Date(filters.andradTill);
    to.setHours(23, 59, 59, 999);
    results = results.filter((f) => new Date(f.senastAndrad) <= to);
  }

  return results;
}

export function getRegions(): string[] {
  return [
    ...new Set(
      getActiveCompanies()
        .map((f) => f.adress.region)
        .filter(Boolean)
    ),
  ].sort();
}

// ── Import / Export ───────────────────────────────────────────────────────

export function exportJSON(ids?: string[]): string {
  const data = ids
    ? getAllCompanies().filter((f) => ids.includes(f.id))
    : getActiveCompanies();
  return JSON.stringify(data, null, 2);
}

function csvEscape(val: unknown): string {
  if (val == null) return "";
  const s = String(val);
  if (s.includes(",") || s.includes('"') || s.includes("\n") || s.includes("\r")) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

export function exportCSV(ids?: string[]): string {
  const companies = ids
    ? getAllCompanies().filter((f) => ids.includes(f.id))
    : getActiveCompanies();

  const headers = [
    "Organisationsnummer",
    "Företagsnamn",
    "Bolagsnamn",
    "Domän",
    "Antal anställda",
    "Storlek",
    "Storlek (manuell)",
    "Gatuadress",
    "Postnummer",
    "Stad",
    "Region",
    "Land",
    "Reception telefon",
    "E-post (info)",
    "Sök fler kontakter",
    "Interna anteckningar",
    ...[1, 2, 3].flatMap((i) => [
      `Kontakt ${i} - Namn`,
      `Kontakt ${i} - Roll`,
      `Kontakt ${i} - Telefon`,
      `Kontakt ${i} - E-post`,
      `Kontakt ${i} - LinkedIn`,
      `Kontakt ${i} - Verifierad`,
      `Kontakt ${i} - Verifieringsmetod`,
      `Kontakt ${i} - Verifieringskälla`,
      `Kontakt ${i} - Verifierad av`,
      `Kontakt ${i} - Verifierad datum`,
    ]),
    "Skapad",
    "Senast ändrad",
  ];

  const rows = companies.map((f) => {
    const k = f.kontakter || [];
    const padded: Contact[] = [...k];
    while (padded.length < 3) {
      padded.push({
        id: "",
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
      });
    }

    const base = [
      csvEscape(f.organisationsnummer),
      csvEscape(f.foretagsnamn),
      csvEscape(f.bolagsnamn),
      csvEscape(f.domain),
      csvEscape(f.antalAnstallda ?? ""),
      csvEscape(f.storlekKategori),
      csvEscape(f.storlekManuell ? "ja" : "nej"),
      csvEscape(f.adress.gata),
      csvEscape(f.adress.postnummer),
      csvEscape(f.adress.stad),
      csvEscape(f.adress.region),
      csvEscape(f.adress.land),
      csvEscape(f.receptionTelefon),
      csvEscape(f.emailInfo),
      csvEscape(f.sokFlerKontakter ? "ja" : "nej"),
      csvEscape(f.internaAnteckningar),
    ];

    const kontaktCells = padded.slice(0, 3).flatMap((c) => [
      csvEscape(c.namn),
      csvEscape(c.roll),
      csvEscape(c.telefon),
      csvEscape(c.email),
      csvEscape(c.linkedinUrl),
      csvEscape(c.verifierad ? "ja" : "nej"),
      csvEscape(c.verifieringsmetod),
      csvEscape(c.verifieringskalla),
      csvEscape(c.verifieratAv),
      csvEscape(c.verifieratDatum),
    ]);

    return [...base, ...kontaktCells, csvEscape(f.skapadDatum), csvEscape(f.senastAndrad)].join(",");
  });

  // CRLF line endings (RFC 4180): some Windows CSV viewers do not break rows on
  // a lone LF and render the whole file as one giant line. BOM keeps Excel UTF-8.
  return "﻿" + [headers.join(","), ...rows].join("\r\n") + "\r\n";
}

function csvParseLine(line: string): string[] {
  const result: string[] = [];
  let current = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (i + 1 < line.length && line[i + 1] === '"') {
          current += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        current += ch;
      }
    } else {
      if (ch === '"') {
        inQuotes = true;
      } else if (ch === ",") {
        result.push(current);
        current = "";
      } else {
        current += ch;
      }
    }
  }
  result.push(current);
  return result;
}

function findColIndex(headers: string[], ...names: string[]): number {
  for (const n of names) {
    const idx = headers.findIndex(
      (h) => h.toLowerCase().trim() === n.toLowerCase().trim()
    );
    if (idx !== -1) return idx;
  }
  return -1;
}

export function importJSON(jsonString: string): ImportResult {
  const data = JSON.parse(jsonString);
  const arr = Array.isArray(data) ? data : [data];
  const companies = getAllCompanies();

  let imported = 0;
  let updated = 0;
  let skipped = 0;

  for (const item of arr) {
    if (!item || typeof item !== "object") {
      skipped++;
      continue;
    }
    if (!item.foretagsnamn && !item.organisationsnummer) {
      skipped++;
      continue;
    }

    const migrated = migrateCompany(item);

    let existingIdx = -1;
    if (migrated.organisationsnummer) {
      existingIdx = companies.findIndex(
        (c) =>
          normalizeOrgnr(c.organisationsnummer) ===
          migrated.organisationsnummer
      );
    }
    if (existingIdx === -1 && item.id) {
      existingIdx = companies.findIndex((c) => c.id === item.id);
    }

    if (existingIdx !== -1) {
      companies[existingIdx] = {
        ...companies[existingIdx],
        ...migrated,
        id: companies[existingIdx].id,
        senastAndrad: new Date().toISOString(),
      };
      updated++;
    } else {
      companies.push({ ...migrated, id: generateId() });
      imported++;
    }
  }

  saveCompanies(companies);
  return { imported, updated, skipped, total: arr.length };
}

export function importCSV(csvText: string): ImportResult {
  const lines = csvText
    .replace(/\r\n/g, "\n")
    .split("\n")
    .filter((l) => l.trim());
  if (lines.length < 2) throw new Error("CSV-filen är tom eller saknar data.");

  const headers = csvParseLine(lines[0]);
  const companies = getAllCompanies();
  let imported = 0;
  let updated = 0;
  let skipped = 0;

  const col = {
    orgnr: findColIndex(headers, "Organisationsnummer", "organisationsnummer", "orgnr"),
    namn: findColIndex(headers, "Företagsnamn", "foretagsnamn"),
    bolagsnamn: findColIndex(headers, "Bolagsnamn", "bolagsnamn"),
    domain: findColIndex(headers, "Domän", "Domain", "domain"),
    antal: findColIndex(headers, "Antal anställda", "antalAnstallda"),
    storlek: findColIndex(headers, "Storlek", "storlek", "storlekKategori"),
    storlekManuell: findColIndex(headers, "Storlek (manuell)", "storlekManuell"),
    gata: findColIndex(headers, "Gatuadress", "gata"),
    postnummer: findColIndex(headers, "Postnummer", "postnummer"),
    stad: findColIndex(headers, "Stad", "stad"),
    region: findColIndex(headers, "Region", "region"),
    land: findColIndex(headers, "Land", "land"),
    tel: findColIndex(headers, "Reception telefon", "Reception Telefon", "receptionTelefon"),
    email: findColIndex(headers, "E-post (info)", "emailInfo"),
    sokFler: findColIndex(headers, "Sök fler kontakter", "sokFlerKontakter"),
    anteckningar: findColIndex(headers, "Interna anteckningar", "internaAnteckningar"),
  };

  const contactCol = (ki: number, field: string) =>
    findColIndex(
      headers,
      `Kontakt ${ki} - ${field}`,
      `Kontakt${ki} - ${field}`,
      `k${ki}_${field}`
    );

  for (let i = 1; i < lines.length; i++) {
    const vals = csvParseLine(lines[i]);
    if (vals.length < 2) {
      skipped++;
      continue;
    }
    const get = (idx: number) =>
      idx >= 0 && idx < vals.length ? vals[idx].trim() : "";

    const foretagsnamn = get(col.namn);
    const orgnr = normalizeOrgnr(get(col.orgnr));
    if (!foretagsnamn && !orgnr) {
      skipped++;
      continue;
    }

    const kontakter: Contact[] = [];
    for (let ki = 1; ki <= 3; ki++) {
      const n = get(contactCol(ki, "Namn"));
      if (!n) continue;
      kontakter.push({
        id: generateContactId(),
        namn: n,
        roll: get(contactCol(ki, "Roll")),
        telefon: get(contactCol(ki, "Telefon")),
        email: get(contactCol(ki, "E-post")),
        linkedinUrl: get(contactCol(ki, "LinkedIn")),
        verifierad: get(contactCol(ki, "Verifierad")).toLowerCase() === "ja",
        verifieringsmetod: (get(contactCol(ki, "Verifieringsmetod")) ||
          "") as Contact["verifieringsmetod"],
        verifieringskalla: get(contactCol(ki, "Verifieringskälla")),
        verifieratAv: get(contactCol(ki, "Verifierad av")),
        verifieratDatum: get(contactCol(ki, "Verifierad datum")),
      });
    }

    const antalRaw = get(col.antal);
    const antal = antalRaw ? parseInt(antalRaw, 10) : NaN;
    const storlekManuell = get(col.storlekManuell).toLowerCase() === "ja";
    const storlekFromCsv = get(col.storlek) as Company["storlekKategori"];

    const partial: Partial<Company> = {
      organisationsnummer: orgnr,
      foretagsnamn,
      bolagsnamn: get(col.bolagsnamn) || foretagsnamn,
      domain: normalizeDomain(get(col.domain)),
      antalAnstallda: isNaN(antal) ? null : antal,
      storlekKategori: storlekManuell
        ? storlekFromCsv
        : classifyStorlek(isNaN(antal) ? null : antal),
      storlekManuell,
      adress: {
        gata: get(col.gata),
        postnummer: get(col.postnummer),
        stad: get(col.stad),
        region: get(col.region),
        land: get(col.land),
      },
      receptionTelefon: get(col.tel),
      emailInfo: get(col.email),
      kontakter,
      sokFlerKontakter: get(col.sokFler).toLowerCase() !== "nej",
      internaAnteckningar: get(col.anteckningar),
    };

    let existingIdx = -1;
    if (orgnr) {
      existingIdx = companies.findIndex(
        (c) => normalizeOrgnr(c.organisationsnummer) === orgnr
      );
    }
    if (existingIdx === -1 && foretagsnamn) {
      existingIdx = companies.findIndex(
        (c) => c.foretagsnamn.toLowerCase() === foretagsnamn.toLowerCase()
      );
    }

    if (existingIdx !== -1) {
      companies[existingIdx] = {
        ...companies[existingIdx],
        ...partial,
        id: companies[existingIdx].id,
        senastAndrad: new Date().toISOString(),
      } as Company;
      updated++;
    } else {
      companies.push(
        migrateCompany({
          id: generateId(),
          ...partial,
          skapadDatum: new Date().toISOString(),
          senastAndrad: new Date().toISOString(),
        })
      );
      imported++;
    }
  }

  saveCompanies(companies);
  return { imported, updated, skipped };
}

/**
 * Generates a CSV template with the same headers as exportCSV() + 2 example
 * rows. Useful for colleagues who want to fill in data manually offline
 * and import via the standard CSV importer.
 */
export function generateTemplateCSV(): string {
  const headers = [
    "Organisationsnummer",
    "Företagsnamn",
    "Bolagsnamn",
    "Domän",
    "Antal anställda",
    "Storlek",
    "Storlek (manuell)",
    "Gatuadress",
    "Postnummer",
    "Stad",
    "Region",
    "Land",
    "Reception telefon",
    "E-post (info)",
    "Sök fler kontakter",
    "Interna anteckningar",
    ...[1, 2, 3].flatMap((i) => [
      `Kontakt ${i} - Namn`,
      `Kontakt ${i} - Roll`,
      `Kontakt ${i} - Telefon`,
      `Kontakt ${i} - E-post`,
      `Kontakt ${i} - LinkedIn`,
      `Kontakt ${i} - Verifierad`,
      `Kontakt ${i} - Verifieringsmetod`,
      `Kontakt ${i} - Verifieringskälla`,
      `Kontakt ${i} - Verifierad av`,
      `Kontakt ${i} - Verifierad datum`,
    ]),
    "Skapad",
    "Senast ändrad",
  ];

  // Example row 1: full record (multinationell)
  const example1 = [
    "556999-1234",                          // Organisationsnummer (XXXXXX-XXXX, 10 siffror)
    "Exempel Företag AB",                   // Företagsnamn
    "Exempel Företag Aktiebolag",           // Bolagsnamn (officiellt)
    "exempelforetag.se",                    // Domän (utan https:// utan www.)
    "350",                                  // Antal anställda (heltal)
    "multinationell",                       // Storlek: liten | medel | multinationell (lämna tomt för auto)
    "nej",                                  // Storlek (manuell): ja | nej (auto-härled från Antal anställda)
    "Storgatan 1",                          // Gatuadress
    "111 22",                               // Postnummer
    "Stockholm",                            // Stad
    "Stockholms län",                       // Region (svenskt län)
    "Sverige",                              // Land
    "+46 8 123 456",                        // Reception telefon
    "info@exempelforetag.se",               // E-post (info)
    "ja",                                   // Sök fler kontakter: ja | nej
    "Notering: Verifiera VD-mailen.",       // Interna anteckningar
    // Kontakt 1
    "Anna Andersson",
    "Verkställande Direktör (VD)",
    "+46 70 111 22 33",
    "anna.andersson@exempelforetag.se",
    "https://www.linkedin.com/in/anna-andersson-demo/",
    "ja",                                   // Verifierad: ja | nej
    "linkedin",                             // Verifieringsmetod: linkedin | foretagswebbplats | pressmeddelande | serpapi | manuell | annan
    "https://www.linkedin.com/in/anna-andersson-demo/",
    "Lorenzo",                              // Verifierad av
    "2026-05-27T10:00:00.000Z",             // Verifierad datum (ISO)
    // Kontakt 2
    "Erik Eriksson",
    "Försäljningschef",
    "+46 70 222 33 44",
    "erik.eriksson@exempelforetag.se",
    "",
    "nej",
    "",
    "",
    "",
    "",
    // Kontakt 3
    "",
    "",
    "",
    "",
    "",
    "nej",
    "",
    "",
    "",
    "",
    new Date().toISOString(),               // Skapad (lämna tomt vid import = nu)
    new Date().toISOString(),               // Senast ändrad
  ];

  // Example row 2: minimal record (only required fields)
  const example2 = [
    "",                                     // Organisationsnummer (valfritt men rekommenderat)
    "Minimalt Företag AB",                  // Företagsnamn (OBLIGATORISKT)
    "",
    "",
    "",
    "",                                     // Storlek tom → auto från Antal anställda (också tomt här)
    "nej",
    "",
    "",
    "Göteborg",
    "Västra Götalands län",
    "Sverige",
    "",
    "",
    "ja",
    "",
    // No contacts
    ...Array(30).fill(""),
    "",
    "",
  ];

  const escape = (v: unknown): string => {
    if (v == null) return "";
    const s = String(v);
    if (s.includes(",") || s.includes('"') || s.includes("\n") || s.includes("\r")) {
      return '"' + s.replace(/"/g, '""') + '"';
    }
    return s;
  };

  const rows = [headers, example1, example2]
    .map((row) => row.map(escape).join(","))
    .join("\r\n");

  // BOM for Excel UTF-8 compatibility; CRLF (RFC 4180) so the downloaded
  // template opens as proper rows — not one giant line — in Excel/Windows viewers.
  return "﻿" + rows + "\r\n";
}

export function downloadFile(
  content: string,
  filename: string,
  mimeType: string
) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
