// CLI: trova tutte le email indicizzate su un dominio via Brave/Ecosia/Bing.
//
// Usage:
//   node find_emails.js savantmedia.se
//
// Stdout: JSON array di email (lowercased, deduplicate).
// Stderr: log diagnostico.
// Exit 0: anche se 0 email (set vuoto). Exit 2: tutti i motori falliti.

import { searchAllEngines } from "./common.js";

async function main() {
  const domain = (process.argv[2] || "").trim().toLowerCase().replace(/^\.+/, "");
  if (!domain) {
    console.error("Usage: node find_emails.js <domain>");
    process.exit(1);
  }

  const queries = [
    `"@${domain}"`,
    `site:${domain} "@${domain}"`,
    `site:${domain} kontakt`,
  ];

  const all = new Set();
  let anySuccess = false;
  for (const q of queries) {
    try {
      const set = await searchAllEngines(q, domain);
      anySuccess = true;
      for (const em of set) all.add(em);
      console.error(`query "${q}" → ${set.size} hits`);
    } catch (err) {
      console.error(`query "${q}" → ERROR: ${err?.message || err}`);
    }
  }

  if (!anySuccess && all.size === 0) {
    console.error("All engines failed");
    process.exit(2);
  }

  console.log(JSON.stringify([...all].sort()));
}

main().catch((err) => {
  console.error("FATAL:", err?.stack || err);
  process.exit(1);
});
