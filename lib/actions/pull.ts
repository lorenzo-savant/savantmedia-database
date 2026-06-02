"use server";

import { getSupabaseAdmin } from "@/lib/supabase/server";
import type { Database } from "@/lib/database.types";
import type {
  Company,
  Contact,
  StorlekKategori,
  Verifieringsmetod,
} from "@/lib/types";

type CompanyRow = Database["public"]["Tables"]["companies"]["Row"];
type ContactRow = Database["public"]["Tables"]["contacts"]["Row"];

function rowToCompany(c: CompanyRow, contacts: ContactRow[]): Company {
  return {
    id: c.id,
    schemaVersion: 2,
    organisationsnummer: c.organisationsnummer ?? "",
    domain: c.domain ?? "",
    foretagsnamn: c.foretagsnamn,
    bolagsnamn: c.bolagsnamn ?? "",
    antalAnstallda: c.antal_anstallda,
    storlekKategori: (c.storlek_kategori ?? "") as StorlekKategori,
    storlekManuell: c.storlek_manuell,
    adress: {
      gata: c.adress_gata ?? "",
      postnummer: c.postnummer ?? "",
      stad: c.stad ?? "",
      region: c.region ?? "",
      land: c.land ?? "Sverige",
    },
    receptionTelefon: c.reception_telefon ?? "",
    emailInfo: c.email_info ?? "",
    sniPrimaryKod: (c as unknown as { sni_primary_kod?: string | null }).sni_primary_kod ?? "",
    sniBranscher: (c as unknown as { sni_branscher?: string | null }).sni_branscher ?? "",
    sniHuvudgrupp: (c as unknown as { sni_huvudgrupp?: string | null }).sni_huvudgrupp ?? "",
    sniAllaKoder: ((c as unknown as { sni_alla_koder?: unknown }).sni_alla_koder as string[] | null) ?? [],
    kontakter: contacts.map((k) => ({
      id: k.id,
      namn: k.namn,
      roll: k.roll ?? "",
      telefon: k.telefon ?? "",
      email: k.email ?? "",
      linkedinUrl: k.linkedin_url ?? "",
      verifierad: k.verifierad,
      verifieringsmetod: (k.verifieringsmetod ?? "") as Verifieringsmetod,
      verifieringskalla: k.verifieringskalla ?? "",
      verifieratAv: k.verifierat_av ?? "",
      verifieratDatum: k.verifierat_datum ?? "",
    })),
    sokFlerKontakter: c.sok_fler_kontakter,
    internaAnteckningar: c.interna_anteckningar ?? "",
    arkiverad: c.arkiverad,
    arkiveradDatum: c.arkiverad_datum ?? "",
    arkiveradAv: c.arkiverad_av ?? "",
    enrichedAt: (c as unknown as { enriched_at?: string | null }).enriched_at ?? "",
    skapadDatum: c.skapad_datum,
    senastAndrad: c.senast_andrad,
  };
}

/**
 * Returns every company (active + archived) from Supabase, mapped to the
 * frontend Company type. The browser will overwrite localStorage with this.
 */
export async function pullAllCompaniesFromSupabase(): Promise<Company[]> {
  const sb = getSupabaseAdmin();

  const [{ data: companies, error: cErr }, { data: contacts, error: kErr }] =
    await Promise.all([
      sb.from("companies").select("*").order("senast_andrad", { ascending: false }),
      sb.from("contacts").select("*"),
    ]);

  if (cErr) throw cErr;
  if (kErr) throw kErr;

  const byCompany = new Map<string, ContactRow[]>();
  for (const k of contacts ?? []) {
    const arr = byCompany.get(k.company_id) ?? [];
    arr.push(k);
    byCompany.set(k.company_id, arr);
  }

  return (companies ?? []).map((c) => rowToCompany(c, byCompany.get(c.id) ?? []));
}
