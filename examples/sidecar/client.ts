/**
 * Minimal TypeScript client for the contextweaver HTTP sidecar (#677).
 *
 * The sidecar exposes two endpoints over HTTP/JSON so non-Python agents can use
 * the deterministic router and the context firewall without embedding Python:
 *
 *   POST /v1/route   — tool routing
 *   POST /v1/compact — tool-result compaction
 *
 * Types below mirror the published JSON Schemas under
 * `schemas/sidecar/v1/` (the source of truth — regenerate/refine from there).
 * Uses the global `fetch` (Node >= 18, Deno, Bun, browsers); no dependencies.
 *
 * Run against a server started with `contextweaver serve-api --catalog <file>`:
 *   SIDECAR_URL=http://127.0.0.1:8731 npx tsx examples/sidecar/client.ts
 */

export interface RouteRequest {
  query: string;
  top_k?: number;
  exclude_ids?: string[];
  allowed_namespaces?: string[];
  context_hints?: string[];
}

export interface RouteResponse {
  api_version: string;
  candidate_ids: string[];
  scores: number[];
  is_ambiguous: boolean;
  clarifying_question: string | null;
  cards: Array<Record<string, unknown>>;
}

export interface CompactRequest {
  data: unknown;
  threshold_chars?: number;
  budget?: number;
  strategy?: "auto" | "structured" | "text" | "passthrough";
  keep?: string[];
}

export interface CompactResponse {
  api_version: string;
  firewalled: boolean;
  payload: unknown;
  summary: string | null;
  facts: string[];
  artifact_ref: string | null;
  tokens_saved: number;
}

export interface SidecarError {
  error: string;
  message: string;
  retryable: boolean;
  details?: Record<string, unknown>;
}

export class SidecarClient {
  constructor(
    private readonly baseUrl: string,
    private readonly apiKey?: string,
  ) {}

  private async post<T>(path: string, body: unknown): Promise<T> {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (this.apiKey) headers["Authorization"] = `Bearer ${this.apiKey}`;
    const resp = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
    const json = await resp.json();
    if (!resp.ok) {
      const err = json as SidecarError;
      throw new Error(`sidecar ${path} failed: [${err.error}] ${err.message}`);
    }
    return json as T;
  }

  route(req: RouteRequest): Promise<RouteResponse> {
    return this.post<RouteResponse>("/v1/route", req);
  }

  compact(req: CompactRequest): Promise<CompactResponse> {
    return this.post<CompactResponse>("/v1/compact", req);
  }
}

async function main(): Promise<void> {
  const baseUrl = process.env.SIDECAR_URL ?? "http://127.0.0.1:8731";
  const client = new SidecarClient(baseUrl, process.env.SIDECAR_API_KEY);

  const routed = await client.route({ query: "send a follow-up email", top_k: 3 });
  console.log(`route -> ${routed.candidate_ids.length} candidates:`, routed.candidate_ids);

  const compacted = await client.compact({
    data: { rows: Array.from({ length: 60 }, (_, i) => ({ id: i, blob: "x".repeat(40) })) },
    threshold_chars: 200,
  });
  console.log(
    `compact -> firewalled=${compacted.firewalled} tokens_saved=${compacted.tokens_saved}`,
  );
}

// Execute only when run directly (not when imported as a module).
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
