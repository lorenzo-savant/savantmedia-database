export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.5"
  }
  graphql_public: {
    Tables: {
      [_ in never]: never
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      graphql: {
        Args: {
          extensions?: Json
          operationName?: string
          query?: string
          variables?: Json
        }
        Returns: Json
      }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
  public: {
    Tables: {
      companies: {
        Row: {
          adress_gata: string | null
          antal_anstallda: number | null
          arkiverad: boolean
          arkiverad_av: string | null
          arkiverad_datum: string | null
          bolagsnamn: string | null
          domain: string | null
          email_info: string | null
          foretagsnamn: string
          id: string
          interna_anteckningar: string | null
          land: string | null
          license_label: string | null
          organisationsnummer: string | null
          postnummer: string | null
          reception_telefon: string | null
          region: string | null
          schema_version: number
          senast_andrad: string
          skapad_datum: string
          sok_fler_kontakter: boolean
          stad: string | null
          storlek_kategori:
            | Database["public"]["Enums"]["storlek_kategori"]
            | null
          storlek_manuell: boolean
        }
        Insert: {
          adress_gata?: string | null
          antal_anstallda?: number | null
          arkiverad?: boolean
          arkiverad_av?: string | null
          arkiverad_datum?: string | null
          bolagsnamn?: string | null
          domain?: string | null
          email_info?: string | null
          foretagsnamn: string
          id?: string
          interna_anteckningar?: string | null
          land?: string | null
          license_label?: string | null
          organisationsnummer?: string | null
          postnummer?: string | null
          reception_telefon?: string | null
          region?: string | null
          schema_version?: number
          senast_andrad?: string
          skapad_datum?: string
          sok_fler_kontakter?: boolean
          stad?: string | null
          storlek_kategori?:
            | Database["public"]["Enums"]["storlek_kategori"]
            | null
          storlek_manuell?: boolean
        }
        Update: {
          adress_gata?: string | null
          antal_anstallda?: number | null
          arkiverad?: boolean
          arkiverad_av?: string | null
          arkiverad_datum?: string | null
          bolagsnamn?: string | null
          domain?: string | null
          email_info?: string | null
          foretagsnamn?: string
          id?: string
          interna_anteckningar?: string | null
          land?: string | null
          license_label?: string | null
          organisationsnummer?: string | null
          postnummer?: string | null
          reception_telefon?: string | null
          region?: string | null
          schema_version?: number
          senast_andrad?: string
          skapad_datum?: string
          sok_fler_kontakter?: boolean
          stad?: string | null
          storlek_kategori?:
            | Database["public"]["Enums"]["storlek_kategori"]
            | null
          storlek_manuell?: boolean
        }
        Relationships: []
      }
      contacts: {
        Row: {
          company_id: string
          email: string | null
          id: string
          is_dm: boolean | null
          linkedin_url: string | null
          namn: string
          roll: string | null
          senast_andrad: string
          skapad_datum: string
          telefon: string | null
          verifierad: boolean
          verifierat_av: string | null
          verifierat_datum: string | null
          verifieringskalla: string | null
          verifieringsmetod:
            | Database["public"]["Enums"]["verifieringsmetod"]
            | null
        }
        Insert: {
          company_id: string
          email?: string | null
          id?: string
          is_dm?: boolean | null
          linkedin_url?: string | null
          namn?: string
          roll?: string | null
          senast_andrad?: string
          skapad_datum?: string
          telefon?: string | null
          verifierad?: boolean
          verifierat_av?: string | null
          verifierat_datum?: string | null
          verifieringskalla?: string | null
          verifieringsmetod?:
            | Database["public"]["Enums"]["verifieringsmetod"]
            | null
        }
        Update: {
          company_id?: string
          email?: string | null
          id?: string
          is_dm?: boolean | null
          linkedin_url?: string | null
          namn?: string
          roll?: string | null
          senast_andrad?: string
          skapad_datum?: string
          telefon?: string | null
          verifierad?: boolean
          verifierat_av?: string | null
          verifierat_datum?: string | null
          verifieringskalla?: string | null
          verifieringsmetod?:
            | Database["public"]["Enums"]["verifieringsmetod"]
            | null
        }
        Relationships: [
          {
            foreignKeyName: "contacts_company_id_fkey"
            columns: ["company_id"]
            isOneToOne: false
            referencedRelation: "companies"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "contacts_company_id_fkey"
            columns: ["company_id"]
            isOneToOne: false
            referencedRelation: "companies_active"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "contacts_company_id_fkey"
            columns: ["company_id"]
            isOneToOne: false
            referencedRelation: "companies_verification_summary"
            referencedColumns: ["company_id"]
          },
        ]
      }
      knowledge_chunks: {
        Row: {
          content: string
          created_at: string
          embedding: string | null
          id: string
          kind: Database["public"]["Enums"]["knowledge_kind"]
          metadata: Json
          source_url: string | null
          vault_path: string | null
        }
        Insert: {
          content: string
          created_at?: string
          embedding?: string | null
          id?: string
          kind: Database["public"]["Enums"]["knowledge_kind"]
          metadata?: Json
          source_url?: string | null
          vault_path?: string | null
        }
        Update: {
          content?: string
          created_at?: string
          embedding?: string | null
          id?: string
          kind?: Database["public"]["Enums"]["knowledge_kind"]
          metadata?: Json
          source_url?: string | null
          vault_path?: string | null
        }
        Relationships: []
      }
      plans: {
        Row: {
          approved_at: string | null
          approved_steps: Json | null
          completed_at: string | null
          created_at: string
          created_by: string | null
          id: string
          status: Database["public"]["Enums"]["plan_status"]
          steps: Json
          user_prompt: string
        }
        Insert: {
          approved_at?: string | null
          approved_steps?: Json | null
          completed_at?: string | null
          created_at?: string
          created_by?: string | null
          id?: string
          status?: Database["public"]["Enums"]["plan_status"]
          steps?: Json
          user_prompt: string
        }
        Update: {
          approved_at?: string | null
          approved_steps?: Json | null
          completed_at?: string | null
          created_at?: string
          created_by?: string | null
          id?: string
          status?: Database["public"]["Enums"]["plan_status"]
          steps?: Json
          user_prompt?: string
        }
        Relationships: []
      }
      scrape_jobs: {
        Row: {
          blocked_reason: string | null
          company_id: string | null
          cost_estimate: number
          created_at: string
          error_message: string | null
          finished_at: string | null
          id: string
          plan_id: string | null
          query: string | null
          result_count: number | null
          started_at: string | null
          status: Database["public"]["Enums"]["scrape_job_status"]
          target_domain: string | null
          tier_used: number | null
        }
        Insert: {
          blocked_reason?: string | null
          company_id?: string | null
          cost_estimate?: number
          created_at?: string
          error_message?: string | null
          finished_at?: string | null
          id?: string
          plan_id?: string | null
          query?: string | null
          result_count?: number | null
          started_at?: string | null
          status?: Database["public"]["Enums"]["scrape_job_status"]
          target_domain?: string | null
          tier_used?: number | null
        }
        Update: {
          blocked_reason?: string | null
          company_id?: string | null
          cost_estimate?: number
          created_at?: string
          error_message?: string | null
          finished_at?: string | null
          id?: string
          plan_id?: string | null
          query?: string | null
          result_count?: number | null
          started_at?: string | null
          status?: Database["public"]["Enums"]["scrape_job_status"]
          target_domain?: string | null
          tier_used?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "scrape_jobs_company_id_fkey"
            columns: ["company_id"]
            isOneToOne: false
            referencedRelation: "companies"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "scrape_jobs_company_id_fkey"
            columns: ["company_id"]
            isOneToOne: false
            referencedRelation: "companies_active"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "scrape_jobs_company_id_fkey"
            columns: ["company_id"]
            isOneToOne: false
            referencedRelation: "companies_verification_summary"
            referencedColumns: ["company_id"]
          },
          {
            foreignKeyName: "scrape_jobs_plan_id_fkey"
            columns: ["plan_id"]
            isOneToOne: false
            referencedRelation: "plans"
            referencedColumns: ["id"]
          },
        ]
      }
      sources: {
        Row: {
          company_id: string | null
          contact_id: string | null
          critic_note: string | null
          fetched_at: string
          field_name: string
          id: string
          license_label: string | null
          raw_excerpt: string | null
          scraper_tier: number | null
          source_url: string | null
        }
        Insert: {
          company_id?: string | null
          contact_id?: string | null
          critic_note?: string | null
          fetched_at?: string
          field_name: string
          id?: string
          license_label?: string | null
          raw_excerpt?: string | null
          scraper_tier?: number | null
          source_url?: string | null
        }
        Update: {
          company_id?: string | null
          contact_id?: string | null
          critic_note?: string | null
          fetched_at?: string
          field_name?: string
          id?: string
          license_label?: string | null
          raw_excerpt?: string | null
          scraper_tier?: number | null
          source_url?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "sources_company_id_fkey"
            columns: ["company_id"]
            isOneToOne: false
            referencedRelation: "companies"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "sources_company_id_fkey"
            columns: ["company_id"]
            isOneToOne: false
            referencedRelation: "companies_active"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "sources_company_id_fkey"
            columns: ["company_id"]
            isOneToOne: false
            referencedRelation: "companies_verification_summary"
            referencedColumns: ["company_id"]
          },
          {
            foreignKeyName: "sources_contact_id_fkey"
            columns: ["contact_id"]
            isOneToOne: false
            referencedRelation: "contacts"
            referencedColumns: ["id"]
          },
        ]
      }
    }
    Views: {
      companies_active: {
        Row: {
          adress_gata: string | null
          antal_anstallda: number | null
          arkiverad: boolean | null
          arkiverad_av: string | null
          arkiverad_datum: string | null
          bolagsnamn: string | null
          domain: string | null
          email_info: string | null
          foretagsnamn: string | null
          id: string | null
          interna_anteckningar: string | null
          land: string | null
          license_label: string | null
          organisationsnummer: string | null
          postnummer: string | null
          reception_telefon: string | null
          region: string | null
          schema_version: number | null
          senast_andrad: string | null
          skapad_datum: string | null
          sok_fler_kontakter: boolean | null
          stad: string | null
          storlek_kategori:
            | Database["public"]["Enums"]["storlek_kategori"]
            | null
          storlek_manuell: boolean | null
        }
        Insert: {
          adress_gata?: string | null
          antal_anstallda?: number | null
          arkiverad?: boolean | null
          arkiverad_av?: string | null
          arkiverad_datum?: string | null
          bolagsnamn?: string | null
          domain?: string | null
          email_info?: string | null
          foretagsnamn?: string | null
          id?: string | null
          interna_anteckningar?: string | null
          land?: string | null
          license_label?: string | null
          organisationsnummer?: string | null
          postnummer?: string | null
          reception_telefon?: string | null
          region?: string | null
          schema_version?: number | null
          senast_andrad?: string | null
          skapad_datum?: string | null
          sok_fler_kontakter?: boolean | null
          stad?: string | null
          storlek_kategori?:
            | Database["public"]["Enums"]["storlek_kategori"]
            | null
          storlek_manuell?: boolean | null
        }
        Update: {
          adress_gata?: string | null
          antal_anstallda?: number | null
          arkiverad?: boolean | null
          arkiverad_av?: string | null
          arkiverad_datum?: string | null
          bolagsnamn?: string | null
          domain?: string | null
          email_info?: string | null
          foretagsnamn?: string | null
          id?: string | null
          interna_anteckningar?: string | null
          land?: string | null
          license_label?: string | null
          organisationsnummer?: string | null
          postnummer?: string | null
          reception_telefon?: string | null
          region?: string | null
          schema_version?: number | null
          senast_andrad?: string | null
          skapad_datum?: string | null
          sok_fler_kontakter?: boolean | null
          stad?: string | null
          storlek_kategori?:
            | Database["public"]["Enums"]["storlek_kategori"]
            | null
          storlek_manuell?: boolean | null
        }
        Relationships: []
      }
      companies_verification_summary: {
        Row: {
          company_id: string | null
          har_verifierad_kontakt: boolean | null
          kontakter_total: number | null
          kontakter_verifierade: number | null
        }
        Relationships: []
      }
    }
    Functions: {
      show_limit: { Args: never; Returns: number }
      show_trgm: { Args: { "": string }; Returns: string[] }
    }
    Enums: {
      knowledge_kind: "playbook" | "snippet" | "query_log" | "lesson"
      plan_status: "draft" | "approved" | "executing" | "done" | "cancelled"
      scrape_job_status: "pending" | "running" | "done" | "blocked" | "failed"
      storlek_kategori: "liten" | "medel" | "multinationell"
      verifieringsmetod:
        | "linkedin"
        | "foretagswebbplats"
        | "pressmeddelande"
        | "serpapi"
        | "manuell"
        | "annan"
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  graphql_public: {
    Enums: {},
  },
  public: {
    Enums: {
      knowledge_kind: ["playbook", "snippet", "query_log", "lesson"],
      plan_status: ["draft", "approved", "executing", "done", "cancelled"],
      scrape_job_status: ["pending", "running", "done", "blocked", "failed"],
      storlek_kategori: ["liten", "medel", "multinationell"],
      verifieringsmetod: [
        "linkedin",
        "foretagswebbplats",
        "pressmeddelande",
        "serpapi",
        "manuell",
        "annan",
      ],
    },
  },
} as const
