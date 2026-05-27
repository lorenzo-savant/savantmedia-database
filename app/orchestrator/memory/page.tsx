"use client";

import { useCallback, useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  Brain,
  ChevronLeft,
  RefreshCcw,
  Search,
  Sparkles,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { useToast } from "@/components/ui/toast";
import { SavantLogo } from "@/components/savant-logo";
import { formatDate } from "@/lib/utils";
import {
  getKnowledgeStats,
  searchKnowledgeChunks,
  semanticSearchKnowledgeChunks,
  type KnowledgeKind,
  type KnowledgeStats,
  type MemoryChunk,
} from "@/lib/actions/memory";
import { ChunkCard } from "./_components/chunk-card";
import { KindBadge, kindStyle } from "./_components/kind-badge";

const PAGE_SIZE = 20;

type KindFilter = "all" | KnowledgeKind;

const KIND_FILTERS: { id: KindFilter; label: string }[] = [
  { id: "all", label: "Alla typer" },
  { id: "playbook", label: "Playbook" },
  { id: "snippet", label: "Snippet" },
  { id: "query_log", label: "Query log" },
  { id: "lesson", label: "Lesson" },
];

export default function OrchestratorMemoryPage() {
  const router = useRouter();
  const { showToast } = useToast();

  // ── Filter / search state ────────────────────────────────────────────────
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [query, setQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [semantic, setSemantic] = useState(false);

  // ── Results state ────────────────────────────────────────────────────────
  const [results, setResults] = useState<MemoryChunk[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  // Last-applied search params — used by "Visa fler" so paginazione
  // resta coerente anche se l'utente edita i filtri senza ri-cliccare "Sök"
  const [appliedParams, setAppliedParams] = useState<{
    kind: KindFilter;
    query: string;
    sourceFilter: string;
    semantic: boolean;
  } | null>(null);

  // ── Stats ────────────────────────────────────────────────────────────────
  const [stats, setStats] = useState<KnowledgeStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);

  // ── Modal ────────────────────────────────────────────────────────────────
  const [modalChunk, setModalChunk] = useState<MemoryChunk | null>(null);

  // ── Load stats once ──────────────────────────────────────────────────────
  const loadStats = useCallback(async () => {
    setStatsLoading(true);
    try {
      const s = await getKnowledgeStats();
      setStats(s);
    } catch (err) {
      // Non blocca la UI — stats è un widget opzionale
      console.warn("getKnowledgeStats failed:", err);
    } finally {
      setStatsLoading(false);
    }
  }, []);

  // ── Run search (replaces results) ────────────────────────────────────────
  const runSearch = useCallback(
    (override?: {
      kind?: KindFilter;
      query?: string;
      sourceFilter?: string;
      semantic?: boolean;
    }) => {
      const effective = {
        kind: override?.kind ?? kindFilter,
        query: (override?.query ?? query).trim(),
        sourceFilter: (override?.sourceFilter ?? sourceFilter).trim(),
        semantic: override?.semantic ?? semantic,
      };

      // Semantic mode richiede una query non-vuota
      if (effective.semantic && !effective.query) {
        showToast(
          "Semantisk sökning kräver en sökfråga.",
          "info"
        );
        return;
      }

      setError(null);
      setAppliedParams(effective);
      setOffset(0);

      startTransition(async () => {
        try {
          const rows = effective.semantic
            ? await semanticSearchKnowledgeChunks({
                query: effective.query,
                kind: effective.kind === "all" ? null : effective.kind,
                limit: PAGE_SIZE,
              })
            : await searchKnowledgeChunks({
                kind: effective.kind === "all" ? null : effective.kind,
                query: effective.query || null,
                sourceFilter: effective.sourceFilter || null,
                limit: PAGE_SIZE,
                offset: 0,
              });
          setResults(rows);
          // Semantic API ritorna sempre i top-N — no "load more"
          setHasMore(!effective.semantic && rows.length === PAGE_SIZE);
        } catch (err) {
          const msg =
            err instanceof Error ? err.message : "Sökning misslyckades.";
          setError(msg);
          setResults([]);
          setHasMore(false);
        }
      });
    },
    [kindFilter, query, sourceFilter, semantic, showToast]
  );

  // ── Load more (only in keyword mode) ─────────────────────────────────────
  const loadMore = useCallback(() => {
    if (!appliedParams || appliedParams.semantic) return;
    const nextOffset = offset + PAGE_SIZE;
    startTransition(async () => {
      try {
        const rows = await searchKnowledgeChunks({
          kind: appliedParams.kind === "all" ? null : appliedParams.kind,
          query: appliedParams.query || null,
          sourceFilter: appliedParams.sourceFilter || null,
          limit: PAGE_SIZE,
          offset: nextOffset,
        });
        setResults((prev) => [...prev, ...rows]);
        setOffset(nextOffset);
        setHasMore(rows.length === PAGE_SIZE);
      } catch (err) {
        const msg =
          err instanceof Error ? err.message : "Kunde inte hämta fler.";
        showToast(msg, "error");
      }
    });
  }, [appliedParams, offset, showToast]);

  // ── Initial load: stats + browse mode (no query) ─────────────────────────
  useEffect(() => {
    loadStats();
    runSearch({ kind: "all", query: "", sourceFilter: "", semantic: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    runSearch();
  };

  const onRefreshAll = () => {
    loadStats();
    runSearch();
  };

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 py-5">
      {/* Breadcrumb */}
      <button
        onClick={() => router.push("/orchestrator")}
        className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-blue-600 mb-4"
      >
        <ChevronLeft className="w-4 h-4" /> Tillbaka till orchestrator
      </button>

      {/* Header */}
      <div className="flex items-center gap-3 mb-2 flex-wrap">
        <SavantLogo size={32} />
        <h1 className="text-xl font-bold text-gray-900 flex items-center gap-2">
          <Brain className="w-5 h-5 text-violet-600" />
          Agent Memory
        </h1>
        <button
          onClick={onRefreshAll}
          disabled={pending || statsLoading}
          className="ml-auto inline-flex items-center gap-1 text-xs text-gray-500 hover:text-blue-600"
        >
          <RefreshCcw
            className={`w-3.5 h-3.5 ${pending || statsLoading ? "animate-spin" : ""}`}
          />
          Uppdatera
        </button>
      </div>
      <p className="text-sm text-gray-500 mb-5">
        Bläddra och sök i agentens långtidsminne (knowledge_chunks):
        playbooks, snippets, query-loggar och lärdomar från tidigare körningar
        och vault-ingest.
      </p>

      {/* Stats row */}
      <StatsRow stats={stats} loading={statsLoading} />

      {/* Filter / search form */}
      <form
        onSubmit={onSubmit}
        className="bg-white rounded-xl border border-gray-200 shadow-sm p-4 mb-5"
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
          {/* Kind dropdown */}
          <div>
            <label className="block text-[11px] font-semibold uppercase tracking-wider text-gray-500 mb-1">
              Typ
            </label>
            <select
              value={kindFilter}
              onChange={(e) => setKindFilter(e.target.value as KindFilter)}
              className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 focus:border-blue-500 focus:ring-2 focus:ring-blue-100 outline-none bg-white"
            >
              {KIND_FILTERS.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.label}
                </option>
              ))}
            </select>
          </div>

          {/* Source filter */}
          <div>
            <label className="block text-[11px] font-semibold uppercase tracking-wider text-gray-500 mb-1">
              Källa (vault_path eller URL)
            </label>
            <input
              type="text"
              value={sourceFilter}
              onChange={(e) => setSourceFilter(e.target.value)}
              placeholder="t.ex. reference_ eller bolagsverket.se"
              disabled={semantic}
              className="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 focus:border-blue-500 focus:ring-2 focus:ring-blue-100 outline-none disabled:bg-gray-50 disabled:text-gray-400"
            />
          </div>
        </div>

        <div className="mb-3">
          <label className="block text-[11px] font-semibold uppercase tracking-wider text-gray-500 mb-1">
            Sökfråga
          </label>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={
                semantic
                  ? "Vad letar agenten efter? (Ollama embedding-sökning)"
                  : "Substring i content (lämna tomt för att browsa allt)"
              }
              className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border border-gray-300 focus:border-blue-500 focus:ring-2 focus:ring-blue-100 outline-none"
            />
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 flex-wrap">
          <label className="inline-flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
            <input
              type="checkbox"
              checked={semantic}
              onChange={(e) => setSemantic(e.target.checked)}
              className="w-4 h-4 rounded border-gray-300 text-violet-600 focus:ring-violet-500"
            />
            <Sparkles className="w-3.5 h-3.5 text-violet-600" />
            Semantisk sökning
            <span className="text-[11px] text-gray-400">
              (via FastAPI + Ollama)
            </span>
          </label>

          <Button type="submit" variant="primary" size="md" disabled={pending}>
            {pending ? "Söker..." : "Sök"}
            <Search className="w-4 h-4" />
          </Button>
        </div>
      </form>

      {/* Error banner */}
      {error && (
        <div className="flex items-start gap-2 p-3 mb-4 rounded-lg bg-red-50 border border-red-200 text-sm text-red-700">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <span className="break-words">{error}</span>
        </div>
      )}

      {/* Results */}
      <ResultsSection
        results={results}
        pending={pending}
        hasMore={hasMore}
        onLoadMore={loadMore}
        onOpenFull={setModalChunk}
        semanticMode={appliedParams?.semantic ?? false}
      />

      {/* Full-content modal */}
      <Modal
        open={!!modalChunk}
        onClose={() => setModalChunk(null)}
        title={modalChunk ? `${kindStyle(modalChunk.kind).label}` : ""}
      >
        {modalChunk && <FullChunkView chunk={modalChunk} />}
      </Modal>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// StatsRow — widget compatto in alto
// ─────────────────────────────────────────────────────────────────────────────

function StatsRow({
  stats,
  loading,
}: {
  stats: KnowledgeStats | null;
  loading: boolean;
}) {
  if (loading && !stats) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4 mb-5 text-sm text-gray-400">
        Hämtar statistik...
      </div>
    );
  }
  if (!stats) return null;

  const kinds: KnowledgeKind[] = ["playbook", "snippet", "query_log", "lesson"];
  return (
    <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-4 mb-5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-500">
            Totalt minne
          </p>
          <p className="text-2xl font-bold text-gray-900">{stats.total}</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {kinds.map((k) => (
            <div
              key={k}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-50 border border-gray-200"
            >
              <KindBadge kind={k} />
              <span className="text-sm font-semibold text-gray-900">
                {stats.by_kind[k] ?? 0}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// ResultsSection
// ─────────────────────────────────────────────────────────────────────────────

function ResultsSection({
  results,
  pending,
  hasMore,
  onLoadMore,
  onOpenFull,
  semanticMode,
}: {
  results: MemoryChunk[];
  pending: boolean;
  hasMore: boolean;
  onLoadMore: () => void;
  onOpenFull: (c: MemoryChunk) => void;
  semanticMode: boolean;
}) {
  // Loading initial
  if (pending && results.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-8 text-center text-sm text-gray-500">
        Söker i minnet...
      </div>
    );
  }

  // Empty state
  if (!pending && results.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-dashed border-gray-300 p-10 text-center">
        <div className="flex justify-center mb-3 opacity-30">
          <SavantLogo size={48} rounded="2xl" />
        </div>
        <p className="text-sm text-gray-500 mb-1">
          Inget minne hittades.
        </p>
        <p className="text-xs text-gray-400">
          Agenten har ännu inte sparat något — eller dina filter matchar inget.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="flex items-center justify-between mb-3 px-1">
        <p className="text-xs text-gray-500">
          {results.length} {results.length === 1 ? "resultat" : "resultat"}
          {semanticMode && (
            <span className="ml-1 text-violet-600 font-medium">
              (semantisk)
            </span>
          )}
        </p>
      </div>

      <div className="space-y-3">
        {results.map((c) => (
          <ChunkCard key={c.id} chunk={c} onOpenFull={onOpenFull} />
        ))}
      </div>

      {hasMore && (
        <div className="mt-4 flex justify-center">
          <Button
            variant="outline"
            size="md"
            onClick={onLoadMore}
            disabled={pending}
          >
            {pending ? "Hämtar..." : "Visa fler"}
          </Button>
        </div>
      )}
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// FullChunkView — body del modal
// ─────────────────────────────────────────────────────────────────────────────

function FullChunkView({ chunk }: { chunk: MemoryChunk }) {
  const metaIsEmpty =
    !chunk.metadata ||
    (typeof chunk.metadata === "object" &&
      !Array.isArray(chunk.metadata) &&
      Object.keys(chunk.metadata as Record<string, unknown>).length === 0);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 flex-wrap text-xs text-gray-500">
        <KindBadge kind={chunk.kind} />
        <span>{formatDate(chunk.created_at)}</span>
        <code className="bg-gray-100 px-1.5 py-0.5 rounded">{chunk.id}</code>
      </div>

      {chunk.vault_path && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-500 mb-0.5">
            Vault path
          </p>
          <p className="text-xs text-indigo-700 break-all">{chunk.vault_path}</p>
        </div>
      )}

      {chunk.source_url && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-500 mb-0.5">
            Source URL
          </p>
          <a
            href={chunk.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-blue-700 hover:text-blue-900 break-all"
          >
            {chunk.source_url}
          </a>
        </div>
      )}

      <div>
        <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-500 mb-1">
          Content
        </p>
        <pre className="text-sm bg-gray-50 border border-gray-200 rounded-md p-3 whitespace-pre-wrap break-words text-gray-800">
          {chunk.content}
        </pre>
      </div>

      {!metaIsEmpty && (
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-500 mb-1">
            Metadata
          </p>
          <pre className="text-[11px] bg-gray-50 border border-gray-200 rounded-md p-3 overflow-x-auto whitespace-pre-wrap break-all text-gray-700">
            {JSON.stringify(chunk.metadata, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
