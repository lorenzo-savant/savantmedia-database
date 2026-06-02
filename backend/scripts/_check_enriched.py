from dotenv import load_dotenv; load_dotenv()
import os; from supabase import create_client
sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SECRET_KEY'])
enr = sb.table("companies").select("foretagsnamn,domain,enriched_at,reception_telefon,email_info").not_.is_("enriched_at", "null").order("foretagsnamn").execute()
print(f"Enriched so far: {len(enr.data)}")
for r in enr.data:
    dom = r.get("domain") or "-"
    tel = "T" if r.get("reception_telefon") else "."
    em = "E" if r.get("email_info") else "."
    print(f"  {r['foretagsnamn'][:50]:50} → {dom[:30]:30} {tel}{em}")
