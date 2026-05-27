"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import type { Database } from "@/lib/database.types";

/**
 * Browser Supabase client using the PUBLISHABLE_KEY.
 * Subject to RLS — currently RLS is disabled, so reads/writes from the
 * browser will fail until Fase 4 (auth + RLS policies).
 *
 * For now, prefer Server Actions that use getSupabaseAdmin().
 */
let _client: SupabaseClient<Database> | null = null;

export function getSupabaseClient(): SupabaseClient<Database> {
  if (_client) return _client;

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const publishable = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

  if (!url || !publishable) {
    throw new Error(
      "Missing Supabase env vars: NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY are required."
    );
  }

  _client = createClient<Database>(url, publishable);
  return _client;
}
