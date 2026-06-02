"""
Prepara 10 batch JSON per subagent SPECIALIZZATI per campo.

Ogni subagent ha un task strettamente delimitato e usa la query
più efficace per quel campo specifico.

Batch                  | Target              | n  | Output campo
-----------------------|---------------------|----|---------------------
specialized_domain     | companies no domain | 50 | id, domain
specialized_phone_co   | companies no phone  | 50 | id, reception_telefon
specialized_email_info | companies no email  | 50 | id, email_info
specialized_employees  | companies no anstallda | 50 | id, antal_anstallda
specialized_vd_email_1 | DM no email         | 50 | id, email
specialized_vd_email_2 | DM no email (next)  | 50 | id, email
specialized_vd_linkedin_1 | DM no linkedin   | 50 | id, linkedin_url
specialized_vd_linkedin_2 | DM no linkedin   | 50 | id, linkedin_url
specialized_vd_phone   | DM no phone         | 50 | id, telefon
specialized_new_dm     | aziende con <2 DM   | 50 | company_id, contacts[]

Usage:
    .venv/Scripts/python.exe -m scripts.prepare_specialized_batches
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

from dotenv import load_dotenv
from rich.console import Console
from supabase import create_client

console = Console()
DATA = Path(__file__).resolve().parent.parent / "data"


def _slim(d: dict, *keys: str) -> dict:
    return {k: (d.get(k) or "") for k in keys}


def main() -> None:
    load_dotenv()
    sb = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"]
    )
    DATA.mkdir(exist_ok=True)

    # 1) Companies no domain
    r = sb.table("companies").select(
        "id, foretagsnamn, organisationsnummer, stad, sni_branscher"
    ).eq("arkiverad", False).or_(
        "domain.is.null,domain.eq."
    ).order("foretagsnamn").limit(50).execute()
    out = [_slim(c, "id", "foretagsnamn", "organisationsnummer", "stad",
                   "sni_branscher") for c in r.data]
    (DATA / "specialized_domain.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    console.print(f"[green]specialized_domain: {len(out)}[/]")

    # 2) Companies no phone, with domain
    r = sb.table("companies").select(
        "id, foretagsnamn, organisationsnummer, stad, domain, sni_branscher"
    ).eq("arkiverad", False).or_(
        "reception_telefon.is.null,reception_telefon.eq."
    ).order("foretagsnamn").limit(150).execute()
    out = [_slim(c, "id", "foretagsnamn", "organisationsnummer", "stad",
                   "domain", "sni_branscher") for c in r.data if c.get("domain")][:50]
    (DATA / "specialized_phone_co.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    console.print(f"[green]specialized_phone_co: {len(out)}[/]")

    # 3) Companies no email_info but with domain
    r = sb.table("companies").select(
        "id, foretagsnamn, domain, sni_branscher"
    ).eq("arkiverad", False).or_(
        "email_info.is.null,email_info.eq."
    ).order("foretagsnamn").limit(200).execute()
    out = [_slim(c, "id", "foretagsnamn", "domain", "sni_branscher")
           for c in r.data if c.get("domain")][:50]
    (DATA / "specialized_email_info.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    console.print(f"[green]specialized_email_info: {len(out)}[/]")

    # 4) Companies no antal_anstallda (skip vilande Z section: SCB sa che sono dormienti)
    r = sb.table("companies").select(
        "id, foretagsnamn, organisationsnummer, stad, domain, sni_huvudgrupp, sni_branscher"
    ).eq("arkiverad", False).is_(
        "antal_anstallda", "null"
    ).neq("sni_huvudgrupp", "Z").order("foretagsnamn").limit(50).execute()
    out = [_slim(c, "id", "foretagsnamn", "organisationsnummer", "stad",
                   "domain", "sni_branscher") for c in r.data]
    (DATA / "specialized_employees.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    console.print(f"[green]specialized_employees: {len(out)}[/]")

    # 5-6) DM no email — 100 in 2 batch (skip first 200 covered by bm2gimccb job)
    r = sb.table("contacts").select(
        "id, namn, roll, company_id"
    ).eq("is_dm", True).or_("email.is.null,email.eq.").order("namn").execute()
    all_dm_no_email = r.data
    # Skip primi 200 (job background li sta processando)
    after_offset = all_dm_no_email[200:300] if len(all_dm_no_email) > 200 else []
    # Hydrate company info
    if after_offset:
        cids = sorted({c["company_id"] for c in after_offset})
        cresp = sb.table("companies").select(
            "id, foretagsnamn, domain"
        ).in_("id", cids).execute()
        by_id = {c["id"]: c for c in cresp.data}
        for c in after_offset:
            comp = by_id.get(c["company_id"], {})
            c["foretagsnamn"] = comp.get("foretagsnamn", "")
            c["domain"] = comp.get("domain", "")
    # Filter: must have domain
    with_domain = [c for c in after_offset if c.get("domain")]
    half = len(with_domain) // 2
    batch1 = with_domain[:half] or with_domain[:50]
    batch2 = with_domain[half:half + 50]
    for name, batch in (("specialized_vd_email_1", batch1),
                         ("specialized_vd_email_2", batch2)):
        out = [_slim(c, "id", "namn", "roll", "foretagsnamn", "domain")
               for c in batch]
        (DATA / f"{name}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        console.print(f"[green]{name}: {len(out)}[/]")

    # 7-8) DM no linkedin — 100 in 2 batch (con verifierad=true preferenza)
    r = sb.table("contacts").select(
        "id, namn, roll, company_id, verifierad"
    ).eq("is_dm", True).or_("linkedin_url.is.null,linkedin_url.eq.")\
        .eq("verifierad", True).order("namn").limit(100).execute()
    if r.data:
        cids = sorted({c["company_id"] for c in r.data})
        cresp = sb.table("companies").select(
            "id, foretagsnamn"
        ).in_("id", cids).execute()
        by_id = {c["id"]: c for c in cresp.data}
        for c in r.data:
            c["foretagsnamn"] = by_id.get(c["company_id"], {}).get("foretagsnamn", "")
    batch1 = r.data[:50]
    batch2 = r.data[50:100]
    for name, batch in (("specialized_vd_linkedin_1", batch1),
                         ("specialized_vd_linkedin_2", batch2)):
        out = [_slim(c, "id", "namn", "roll", "foretagsnamn") for c in batch]
        (DATA / f"{name}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        console.print(f"[green]{name}: {len(out)}[/]")

    # 9) DM no phone
    r = sb.table("contacts").select(
        "id, namn, roll, company_id"
    ).eq("is_dm", True).or_("telefon.is.null,telefon.eq.")\
        .eq("verifierad", True).order("namn").limit(50).execute()
    if r.data:
        cids = sorted({c["company_id"] for c in r.data})
        cresp = sb.table("companies").select(
            "id, foretagsnamn, domain"
        ).in_("id", cids).execute()
        by_id = {c["id"]: c for c in cresp.data}
        for c in r.data:
            comp = by_id.get(c["company_id"], {})
            c["foretagsnamn"] = comp.get("foretagsnamn", "")
            c["domain"] = comp.get("domain", "")
    out = [_slim(c, "id", "namn", "roll", "foretagsnamn", "domain")
           for c in r.data]
    (DATA / "specialized_vd_phone.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    console.print(f"[green]specialized_vd_phone: {len(out)}[/]")

    # 10) Companies with <2 DM (find new CFO/CMO/COO)
    r = sb.table("companies").select(
        "id, foretagsnamn, domain"
    ).eq("arkiverad", False).execute()
    cresp = sb.table("contacts").select("company_id").eq("is_dm", True).execute()
    dm_counts: dict[str, int] = {}
    for c in cresp.data:
        cid = c["company_id"]
        dm_counts[cid] = dm_counts.get(cid, 0) + 1
    low_dm = [
        c for c in r.data
        if c.get("domain") and dm_counts.get(c["id"], 0) < 2
    ][:50]
    out = [_slim(c, "id", "foretagsnamn", "domain") for c in low_dm]
    (DATA / "specialized_new_dm.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    console.print(f"[green]specialized_new_dm: {len(out)}[/]")


if __name__ == "__main__":
    main()
