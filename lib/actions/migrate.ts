"use server";

import { getSupabaseAdmin } from "@/lib/supabase/server";
import type { Database } from "@/lib/database.types";
import type { Company } from "@/lib/types";

type CompanyInsert = Database["public"]["Tables"]["companies"]["Insert"];
type ContactInsert = Database["public"]["Tables"]["contacts"]["Insert"];
type StorlekKategoriDb = Database["public"]["Enums"]["storlek_kategori"];
type VerifieringsmetodDb = Database["public"]["Enums"]["verifieringsmetod"];

export type MigrationResult = {
  imported: number;
  updated: number;
  contactsInserted: number;
  failed: number;
  errors: string[];
};

export type DbStats = {
  companies: number;
  contacts: number;
  archived: number;
};

function toDbStorlek(s: string): StorlekKategoriDb | null {
  if (s === "liten" || s === "medel" || s === "multinationell") return s;
  return null;
}

function toDbVerMetod(v: string): VerifieringsmetodDb | null {
  if (
    v === "linkedin" ||
    v === "foretagswebbplats" ||
    v === "pressmeddelande" ||
    v === "serpapi" ||
    v === "manuell" ||
    v === "annan"
  )
    return v;
  return null;
}

function emptyToNull(s: string | null | undefined): string | null {
  if (s == null) return null;
  const t = s.trim();
  return t === "" ? null : t;
}

function isoOrNull(s: string | null | undefined): string | null {
  if (!s) return null;
  // Validate ISO timestamp; if invalid return null
  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d.toISOString();
}

function companyToInsert(c: Company): CompanyInsert {
  return {
    organisationsnummer: emptyToNull(c.organisationsnummer),
    domain: emptyToNull(c.domain),
    foretagsnamn: c.foretagsnamn,
    bolagsnamn: emptyToNull(c.bolagsnamn),
    antal_anstallda: c.antalAnstallda,
    storlek_kategori: toDbStorlek(c.storlekKategori),
    storlek_manuell: c.storlekManuell,
    adress_gata: c.adress.gata,
    postnummer: c.adress.postnummer,
    stad: c.adress.stad,
    region: c.adress.region,
    land: c.adress.land || "Sverige",
    reception_telefon: c.receptionTelefon,
    email_info: c.emailInfo,
    sok_fler_kontakter: c.sokFlerKontakter,
    interna_anteckningar: c.internaAnteckningar,
    arkiverad: c.arkiverad,
    arkiverad_datum: isoOrNull(c.arkiveradDatum),
    arkiverad_av: c.arkiveradAv,
    skapad_datum: isoOrNull(c.skapadDatum) ?? new Date().toISOString(),
    senast_andrad: isoOrNull(c.senastAndrad) ?? new Date().toISOString(),
  };
}

function contactToInsert(
  k: Company["kontakter"][number],
  companyId: string
): ContactInsert {
  return {
    company_id: companyId,
    namn: k.namn,
    roll: k.roll,
    telefon: k.telefon,
    email: k.email,
    linkedin_url: k.linkedinUrl,
    verifierad: k.verifierad,
    verifieringsmetod: toDbVerMetod(k.verifieringsmetod),
    verifieringskalla: k.verifieringskalla,
    verifierat_av: k.verifieratAv,
    verifierat_datum: isoOrNull(k.verifieratDatum),
  };
}

/**
 * One-shot migration: takes companies (as they live in localStorage)
 * and pushes them to Supabase.
 *
 * Strategy:
 *   - Match existing rows by organisationsnummer (the natural key).
 *   - If match: UPDATE the company + DELETE+REINSERT contacts (full replace).
 *   - If no match: INSERT new company + contacts.
 *   - Records without org.nr always create a new row (no dedup possible).
 *
 * The frontend "id" (e.g. "f1ab2c…") is discarded — Supabase generates UUIDs.
 */
export async function migrateCompaniesFromLocalStorage(
  companies: Company[]
): Promise<MigrationResult> {
  const sb = getSupabaseAdmin();
  const result: MigrationResult = {
    imported: 0,
    updated: 0,
    contactsInserted: 0,
    failed: 0,
    errors: [],
  };

  for (const c of companies) {
    try {
      const insertPayload = companyToInsert(c);
      const orgnr = insertPayload.organisationsnummer;

      let existingId: string | null = null;
      if (orgnr) {
        const { data: existing, error: lookupErr } = await sb
          .from("companies")
          .select("id")
          .eq("organisationsnummer", orgnr)
          .maybeSingle();
        if (lookupErr) throw lookupErr;
        existingId = existing?.id ?? null;
      }

      let companyId: string;
      if (existingId) {
        const { error: updErr } = await sb
          .from("companies")
          .update(insertPayload)
          .eq("id", existingId);
        if (updErr) throw updErr;
        companyId = existingId;
        result.updated++;

        // Replace contacts wholesale to avoid stale rows
        const { error: delErr } = await sb
          .from("contacts")
          .delete()
          .eq("company_id", existingId);
        if (delErr) throw delErr;
      } else {
        const { data: inserted, error: insErr } = await sb
          .from("companies")
          .insert(insertPayload)
          .select("id")
          .single();
        if (insErr) throw insErr;
        companyId = inserted.id;
        result.imported++;
      }

      if (c.kontakter && c.kontakter.length > 0) {
        const rows = c.kontakter.map((k) => contactToInsert(k, companyId));
        const { error: cErr, data: insertedContacts } = await sb
          .from("contacts")
          .insert(rows)
          .select("id");
        if (cErr) throw cErr;
        result.contactsInserted += insertedContacts?.length ?? 0;
      }
    } catch (err) {
      result.failed++;
      result.errors.push(
        `${c.foretagsnamn || "(no name)"}: ${
          err instanceof Error ? err.message : String(err)
        }`
      );
    }
  }

  return result;
}

export async function getDbStats(): Promise<DbStats> {
  const sb = getSupabaseAdmin();

  const [companiesRes, contactsRes, archivedRes] = await Promise.all([
    sb.from("companies").select("*", { count: "exact", head: true }),
    sb.from("contacts").select("*", { count: "exact", head: true }),
    sb
      .from("companies")
      .select("*", { count: "exact", head: true })
      .eq("arkiverad", true),
  ]);

  if (companiesRes.error) throw companiesRes.error;
  if (contactsRes.error) throw contactsRes.error;
  if (archivedRes.error) throw archivedRes.error;

  return {
    companies: companiesRes.count ?? 0,
    contacts: contactsRes.count ?? 0,
    archived: archivedRes.count ?? 0,
  };
}
