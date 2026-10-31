// Use "/" when the frontend and API are served from the same domain (the VPS setup behind Caddy).
export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

export type Platform = "tiktok" | "youtube" | "instagram" | "facebook";

export interface MediaOption {
  id: string;
  kind: "video" | "audio";
  label: string;
  ext: string;
  height: number | null;
  watermark: boolean | null;
  size: number | null;
}

export interface MediaInfo {
  platform: Platform;
  title: string;
  thumbnail: string | null;
  duration: number | null;
  options: MediaOption[];
}

export type JobState = "queued" | "downloading" | "processing" | "done" | "error";

export interface JobStatus {
  status: JobState;
  progress: number;
  error: string | null;
}

export class ApiError extends Error {
  constructor(message: string, public code: string, public status: number) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError("Can't reach the server. Check your connection and try again.", "network", 0);
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const err = body?.error;
    throw new ApiError(err?.message ?? `Request failed (${res.status}).`, err?.code ?? "unknown", res.status);
  }
  return body as T;
}

export const getInfo = (url: string) =>
  request<MediaInfo>("/api/info", { method: "POST", body: JSON.stringify({ url }) });

export const createJob = (url: string, optionId: string) =>
  request<{ job_id: string }>("/api/jobs", { method: "POST", body: JSON.stringify({ url, option_id: optionId }) });

export const getJob = (jobId: string) => request<JobStatus>(`/api/jobs/${jobId}`);

/** Fire-and-forget page visit count; never affects the page. */
export function trackVisit(): void {
  fetch(`${API_URL}/api/visit`, { method: "POST", keepalive: true }).catch(() => undefined);
}

export const fileUrl = (jobId: string) => `${API_URL}/api/jobs/${jobId}/file`;

export function formatDuration(seconds: number | null): string {
  if (!seconds) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60).toString().padStart(2, "0");
  return h ? `${h}:${m.toString().padStart(2, "0")}:${s}` : `${m}:${s}`;
}

export function formatSize(bytes: number | null): string {
  if (!bytes) return "";
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `~${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}
