"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import {
  ApiError,
  createJob,
  fileUrl,
  formatDuration,
  formatSize,
  getInfo,
  getJob,
  type JobStatus,
  type MediaInfo,
  type MediaOption,
} from "@/lib/api";

const PLATFORMS = { tiktok: "TikTok", youtube: "YouTube", instagram: "Instagram" } as const;
const POLL_MS = 1200;

interface Job extends JobStatus {
  id: string;
  option: MediaOption;
}

const message = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Please try again.");

export default function Downloader() {
  const [url, setUrl] = useState("");
  const [submittedUrl, setSubmittedUrl] = useState("");
  const [info, setInfo] = useState<MediaInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Poll the active job until it finishes.
  useEffect(() => {
    if (!job || job.status === "done" || job.status === "error") return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const status = await getJob(job.id);
        if (!cancelled) setJob((j) => (j && j.id === job.id ? { ...j, ...status } : j));
      } catch (e) {
        if (!cancelled) setJob((j) => (j ? { ...j, status: "error", error: message(e) } : j));
      }
    }, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [job]);

  async function fetchInfo(e?: FormEvent) {
    e?.preventDefault();
    const value = url.trim();
    if (!value) {
      setError("Paste a TikTok, YouTube or Instagram link first.");
      inputRef.current?.focus();
      return;
    }
    setLoading(true);
    setError(null);
    setInfo(null);
    setJob(null);
    try {
      setInfo(await getInfo(value));
      setSubmittedUrl(value);
    } catch (err) {
      setError(message(err));
    } finally {
      setLoading(false);
    }
  }

  async function startJob(option: MediaOption) {
    setError(null);
    try {
      const { job_id } = await createJob(submittedUrl, option.id);
      setJob({ id: job_id, option, status: "queued", progress: 0, error: null });
    } catch (err) {
      setError(message(err));
    }
  }

  async function pasteFromClipboard() {
    try {
      const text = await navigator.clipboard.readText();
      if (text) setUrl(text.trim());
    } catch {
      inputRef.current?.focus();
    }
  }

  function reset() {
    setUrl("");
    setInfo(null);
    setJob(null);
    setError(null);
    inputRef.current?.focus();
  }

  const busy = job !== null && job.status !== "error";

  return (
    <div className="space-y-5">
      <form onSubmit={fetchInfo} className="rounded-3xl border border-white/10 bg-zinc-900/60 p-3 shadow-2xl backdrop-blur sm:p-4">
        <label htmlFor="url" className="sr-only">Video link</label>
        <div className="flex flex-col gap-3 sm:flex-row">
          <div className="relative flex-1">
            <input
              ref={inputRef}
              id="url"
              type="url"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              placeholder="Paste a video link"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="h-14 w-full rounded-2xl border border-white/10 bg-zinc-950/80 pl-4 pr-24 text-base text-zinc-100 placeholder:text-zinc-500 outline-none transition focus:border-violet-400/60 focus:ring-4 focus:ring-violet-500/15"
            />
            <button
              type="button"
              onClick={url ? () => setUrl("") : pasteFromClipboard}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded-xl bg-white/5 px-3 py-2 text-sm font-medium text-zinc-300 transition hover:bg-white/10"
            >
              {url ? "Clear" : "Paste"}
            </button>
          </div>
          <button
            type="submit"
            disabled={loading}
            className="flex h-14 items-center justify-center gap-2 rounded-2xl bg-gradient-to-r from-violet-500 to-fuchsia-500 px-7 text-base font-semibold text-white shadow-lg shadow-fuchsia-500/20 transition hover:brightness-110 active:scale-[0.98] disabled:opacity-60"
          >
            {loading ? <Spinner /> : <SearchIcon />}
            {loading ? "Fetching…" : "Fetch"}
          </button>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 px-1 text-xs text-zinc-500">
          <span>Supports</span>
          {Object.values(PLATFORMS).map((p) => (
            <span key={p} className="rounded-full border border-white/10 px-2.5 py-1 text-zinc-400">{p}</span>
          ))}
        </div>
      </form>

      {error && (
        <div role="alert" className="flex items-start gap-3 rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-200">
          <AlertIcon />
          <p className="flex-1">{error}</p>
          <button onClick={() => setError(null)} className="text-red-300/70 hover:text-red-200" aria-label="Dismiss">✕</button>
        </div>
      )}

      {loading && <PreviewSkeleton />}

      {!loading && !info && !error && <EmptyState />}

      {info && !loading && (
        <section className="overflow-hidden rounded-3xl border border-white/10 bg-zinc-900/60 backdrop-blur">
          <div className="flex flex-col sm:flex-row">
            <div className="relative aspect-video w-full shrink-0 bg-zinc-950 sm:w-64">
              {info.thumbnail ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={info.thumbnail} alt="" referrerPolicy="no-referrer" className="h-full w-full object-cover" />
              ) : (
                <div className="flex h-full items-center justify-center text-zinc-700"><PlayIcon /></div>
              )}
              {info.duration ? (
                <span className="absolute bottom-2 right-2 rounded-md bg-black/75 px-1.5 py-0.5 text-xs font-medium tabular-nums text-white">
                  {formatDuration(info.duration)}
                </span>
              ) : null}
            </div>
            <div className="flex min-w-0 flex-1 flex-col justify-center gap-2 p-4 sm:p-5">
              <span className="w-fit rounded-full bg-violet-500/15 px-2.5 py-1 text-xs font-medium text-violet-300">
                {PLATFORMS[info.platform]}
              </span>
              <h2 className="line-clamp-3 text-base font-semibold leading-snug text-zinc-100">{info.title}</h2>
            </div>
          </div>

          <div className="border-t border-white/5 p-4 sm:p-5">
            {job ? (
              <JobPanel job={job} onRetry={() => startJob(job.option)} onReset={reset} />
            ) : (
              <OptionGroups options={info.options} disabled={busy} onPick={startJob} />
            )}
          </div>
        </section>
      )}
    </div>
  );
}

function OptionGroups({ options, disabled, onPick }: { options: MediaOption[]; disabled: boolean; onPick: (o: MediaOption) => void }) {
  const groups = [
    { title: "Video", items: options.filter((o) => o.kind === "video") },
    { title: "Audio", items: options.filter((o) => o.kind === "audio") },
  ].filter((g) => g.items.length);

  return (
    <div className="space-y-5">
      {groups.map((g) => (
        <div key={g.title}>
          <h3 className="mb-2.5 text-xs font-semibold uppercase tracking-wider text-zinc-500">{g.title}</h3>
          <div className="grid grid-cols-1 gap-2 min-[420px]:grid-cols-2">
            {g.items.map((o) => (
              <button
                key={o.id}
                disabled={disabled}
                onClick={() => onPick(o)}
                className="group flex items-center justify-between gap-3 rounded-2xl border border-white/10 bg-zinc-950/60 px-4 py-3 text-left transition hover:border-violet-400/50 hover:bg-violet-500/10 active:scale-[0.98] disabled:opacity-50"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium text-zinc-100">{o.label}</span>
                  <span className="text-xs text-zinc-500">
                    {o.ext.toUpperCase()}
                    {o.size ? ` · ${formatSize(o.size)}` : ""}
                  </span>
                </span>
                <span className="text-zinc-500 transition group-hover:text-violet-300"><DownloadIcon /></span>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function JobPanel({ job, onRetry, onReset }: { job: Job; onRetry: () => void; onReset: () => void }) {
  if (job.status === "error") {
    return (
      <div className="space-y-3">
        <p className="flex items-start gap-2 text-sm text-red-200"><AlertIcon />{job.error ?? "Download failed."}</p>
        <div className="flex gap-2">
          <button onClick={onRetry} className="rounded-xl bg-white/10 px-4 py-2.5 text-sm font-medium text-zinc-100 hover:bg-white/15">Try again</button>
          <button onClick={onReset} className="rounded-xl px-4 py-2.5 text-sm font-medium text-zinc-400 hover:text-zinc-200">New link</button>
        </div>
      </div>
    );
  }

  if (job.status === "done") {
    return (
      <div className="space-y-3">
        <a
          href={fileUrl(job.id)}
          className="flex h-14 w-full items-center justify-center gap-2 rounded-2xl bg-gradient-to-r from-emerald-500 to-teal-500 text-base font-semibold text-white shadow-lg shadow-emerald-500/20 transition hover:brightness-110 active:scale-[0.98]"
        >
          <DownloadIcon /> Download {job.option.ext.toUpperCase()}
        </a>
        <div className="flex items-center justify-between text-xs text-zinc-500">
          <span>{job.option.label} · link expires soon after download</span>
          <button onClick={onReset} className="font-medium text-violet-300 hover:text-violet-200">Download another</button>
        </div>
      </div>
    );
  }

  const label = { queued: "Waiting in queue…", downloading: "Downloading…", processing: "Processing…" }[job.status];
  const indeterminate = job.status !== "downloading" || job.progress === 0;
  return (
    <div className="space-y-3" aria-live="polite">
      <div className="flex items-center justify-between text-sm">
        <span className="flex items-center gap-2 text-zinc-200"><Spinner />{label}</span>
        {!indeterminate && <span className="tabular-nums text-zinc-400">{job.progress}%</span>}
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-white/5">
        <div
          className={`h-full rounded-full bg-gradient-to-r from-violet-500 to-fuchsia-500 transition-[width] duration-500 ${indeterminate ? "w-1/3 animate-[slide_1.2s_ease-in-out_infinite]" : ""}`}
          style={indeterminate ? undefined : { width: `${Math.max(job.progress, 3)}%` }}
        />
      </div>
      <p className="text-xs text-zinc-500">{job.option.label} · keep this tab open</p>
    </div>
  );
}

function EmptyState() {
  const steps = [
    ["Paste", "Copy a video link from TikTok, YouTube or Instagram."],
    ["Choose", "Pick a video quality or MP3 audio."],
    ["Save", "Download the file straight to your device."],
  ];
  return (
    <ol className="grid gap-3 sm:grid-cols-3">
      {steps.map(([title, text], i) => (
        <li key={title} className="rounded-2xl border border-white/5 bg-zinc-900/40 p-4">
          <span className="mb-2 flex h-7 w-7 items-center justify-center rounded-full bg-violet-500/15 text-xs font-semibold text-violet-300">{i + 1}</span>
          <p className="text-sm font-medium text-zinc-200">{title}</p>
          <p className="mt-1 text-xs leading-relaxed text-zinc-500">{text}</p>
        </li>
      ))}
    </ol>
  );
}

function PreviewSkeleton() {
  return (
    <div className="animate-pulse overflow-hidden rounded-3xl border border-white/10 bg-zinc-900/60">
      <div className="flex flex-col sm:flex-row">
        <div className="aspect-video w-full bg-white/5 sm:w-64" />
        <div className="flex-1 space-y-3 p-5">
          <div className="h-5 w-20 rounded-full bg-white/5" />
          <div className="h-4 w-full rounded bg-white/5" />
          <div className="h-4 w-2/3 rounded bg-white/5" />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 border-t border-white/5 p-5">
        {[0, 1, 2, 3].map((i) => <div key={i} className="h-14 rounded-2xl bg-white/5" />)}
      </div>
    </div>
  );
}

const icon = "h-5 w-5 shrink-0";
const Spinner = () => (
  <svg className="h-4 w-4 shrink-0 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden>
    <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity=".25" strokeWidth="4" />
    <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
  </svg>
);
const SearchIcon = () => (
  <svg className={icon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
    <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" strokeLinecap="round" />
  </svg>
);
const DownloadIcon = () => (
  <svg className={icon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M12 4v11m0 0-4-4m4 4 4-4M5 20h14" />
  </svg>
);
const AlertIcon = () => (
  <svg className={icon} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
    <circle cx="12" cy="12" r="9" /><path d="M12 8v5m0 3h.01" />
  </svg>
);
const PlayIcon = () => (
  <svg className="h-10 w-10" viewBox="0 0 24 24" fill="currentColor" aria-hidden><path d="M8 5v14l11-7z" /></svg>
);
