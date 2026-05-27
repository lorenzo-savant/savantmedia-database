"use client";

import { ExternalLink, FileText, FolderOpen, Info } from "lucide-react";
import { Button } from "@/components/ui/button";
import { formatDate } from "@/lib/utils";
import { KindBadge } from "./kind-badge";
import type { MemoryChunk } from "@/lib/actions/memory";

const EXCERPT_LEN = 300;

function obsidianUri(vaultPath: string): string {
  // obsidian://open?vault=<vault>&file=<file>
  // Senza nome di vault (è specifico per Lorenzo), proviamo il formato
  // "advanced" che apre per path assoluto se la vault è registrata.
  return `obsidian://open?path=${encodeURIComponent(vaultPath)}`;
}

export function ChunkCard({
  chunk,
  onOpenFull,
}: {
  chunk: MemoryChunk;
  onOpenFull: (c: MemoryChunk) => void;
}) {
  const isTruncated = chunk.content.length > EXCERPT_LEN;
  const excerpt = isTruncated
    ? chunk.content.slice(0, EXCERPT_LEN).trimEnd() + "..."
    : chunk.content;

  const metaIsEmpty =
    !chunk.metadata ||
    (typeof chunk.metadata === "object" &&
      !Array.isArray(chunk.metadata) &&
      Object.keys(chunk.metadata as Record<string, unknown>).length === 0);

  return (
    <article className="bg-white rounded-xl border border-gray-200 shadow-sm p-4">
      <header className="flex items-center justify-between gap-2 flex-wrap mb-2">
        <div className="flex items-center gap-2 flex-wrap">
          <KindBadge kind={chunk.kind} />
          <span className="text-[11px] text-gray-400">
            {formatDate(chunk.created_at)}
          </span>
        </div>
        <code className="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">
          {chunk.id.slice(0, 8)}
        </code>
      </header>

      {chunk.audit_note && (
        <p className="flex items-start gap-1.5 text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2 py-1 mb-2">
          <Info className="w-3 h-3 mt-0.5 shrink-0" />
          <span>{chunk.audit_note}</span>
        </p>
      )}

      <p className="text-sm text-gray-800 whitespace-pre-wrap break-words mb-3">
        {excerpt}
      </p>

      {(chunk.vault_path || chunk.source_url) && (
        <div className="flex flex-col gap-1 text-xs mb-3">
          {chunk.vault_path && (
            <a
              href={obsidianUri(chunk.vault_path)}
              className="inline-flex items-center gap-1 text-indigo-600 hover:text-indigo-800 break-all"
              title="Öppna i Obsidian"
            >
              <FolderOpen className="w-3 h-3 shrink-0" />
              <span className="break-all">{chunk.vault_path}</span>
            </a>
          )}
          {chunk.source_url && (
            <a
              href={chunk.source_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-blue-600 hover:text-blue-800 break-all"
            >
              <ExternalLink className="w-3 h-3 shrink-0" />
              <span className="break-all">{chunk.source_url}</span>
            </a>
          )}
        </div>
      )}

      {!metaIsEmpty && (
        <details className="mb-3">
          <summary className="text-[11px] font-semibold uppercase tracking-wider text-gray-500 cursor-pointer hover:text-gray-700">
            Metadata
          </summary>
          <pre className="mt-1.5 text-[11px] bg-gray-50 border border-gray-200 rounded-md p-2 overflow-x-auto whitespace-pre-wrap break-all text-gray-700">
            {JSON.stringify(chunk.metadata, null, 2)}
          </pre>
        </details>
      )}

      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={() => onOpenFull(chunk)}>
          <FileText className="w-3.5 h-3.5" />
          Visa fullständig
        </Button>
      </div>
    </article>
  );
}
