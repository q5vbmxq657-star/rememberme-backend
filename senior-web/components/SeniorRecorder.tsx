"use client";

import { useCallback, useEffect, useState } from "react";
import { AudioWave } from "@/components/AudioWave";
import { type PodcastPrompt, useSeniorVADRecorder } from "@/hooks/useSeniorVADRecorder";

type Metadata = {
  requester_name: string;
  subject_name: string;
  prompt: string;
  prompt_audio_url: string | null;
  status: "pending" | "recording" | "uploaded" | "processing" | "completed" | "retryable_failed" | "expired";
  prompts: PodcastPrompt[];
  expires_at: string;
};

export function SeniorRecorder({
  token,
  apiBaseURL,
  metadata: initialMetadata
}: {
  token: string;
  apiBaseURL: string;
  metadata: Metadata;
}) {
  const [metadata, setMetadata] = useState(initialMetadata);
  const [statusError, setStatusError] = useState<string | null>(null);
  const processing = ["recording", "uploaded", "processing"].includes(metadata.status);
  useEffect(() => {
    if (!processing) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController | undefined;
    const refresh = async () => {
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 10000);
      try {
        const response = await fetch(`${apiBaseURL.replace(/\/$/, "")}/v1/public/podcast/${encodeURIComponent(token)}`,
          { cache: "no-store", signal: controller.signal });
        if (cancelled) return;
        if (response.status === 404) {
          setMetadata(previous => ({ ...previous, status: "expired" }));
          return;
        }
        if (!response.ok) throw new Error("Status unavailable");
        const update = await response.json() as Metadata;
        if (!["pending", "recording", "uploaded", "processing", "completed", "retryable_failed", "expired"].includes(update.status)) {
          throw new Error("Invalid status");
        }
        if (!cancelled) {
          setMetadata(previous => ({ ...previous, status: update.status }));
          setStatusError(null);
        }
      } catch {
        if (!cancelled) setStatusError("We cannot check progress right now. Reconnecting automatically.");
      } finally {
        clearTimeout(timeout);
        if (!cancelled) timer = setTimeout(refresh, 5000);
      }
    };
    void refresh();
    return () => { cancelled = true; clearTimeout(timer); controller?.abort(); };
  }, [processing, apiBaseURL, token]);
  const [successMessage, setSuccessMessage] = useState("");
  const [speakerConfirmedSubject, setSpeakerConfirmedSubject] = useState(false);
  const [voiceTrainingConsent, setVoiceTrainingConsent] = useState(false);
  const onSuccess = useCallback((message: string) => setSuccessMessage(message), []);
  const prompts = metadata.prompts.length > 0 ? metadata.prompts : [{
    prompt_id: "legacy_prompt",
    category: "life_story",
    question: metadata.prompt,
    audio_url: metadata.prompt_audio_url
  }];
  const recorder = useSeniorVADRecorder({ token, apiBaseURL, prompts, onSuccess,
    expiresAt: metadata.expires_at, completed: metadata.status === "completed" });
  const currentPrompt = prompts[recorder.currentPromptIndex] ?? prompts[0];
  const busy = ["PLAYING_PROMPT", "RECORDING", "SILENCE_DETECTED", "UPLOADING"].includes(recorder.state);

  if (metadata.status === "expired") {
    return <main className="flex min-h-[100svh] items-center justify-center px-6 py-10 text-center">
      <section className="w-full max-w-md"><h1 className="text-3xl font-bold">This interview link is no longer available.</h1>
        <p className="mt-5 text-xl">Ask the person who invited you for a new link.</p></section>
    </main>;
  }

  if (metadata.status === "completed") {
    return (
      <main className="flex min-h-[100svh] items-center justify-center bg-[#fffaf9] px-6 py-10 text-center">
        <section className="w-full max-w-md" aria-live="polite">
          <div className="mx-auto mb-8 grid h-28 w-28 place-items-center rounded-full bg-emerald-100 text-6xl text-emerald-700" aria-hidden="true">✓</div>
          <p className="text-lg font-bold text-[#d85048]">Saved</p>
          <h1 className="mt-3 text-4xl font-bold text-zinc-950">Your story is already saved.</h1>
          <p className="mt-5 text-2xl font-semibold leading-snug text-zinc-700">There is nothing else you need to do.</p>
          {recorder.error && <p role="alert" className="mt-5 text-lg text-red-800">{recorder.error}</p>}
        </section>
      </main>
    );
  }

  if (["recording", "uploaded", "processing"].includes(metadata.status)) {
    return (
      <main className="flex min-h-[100svh] items-center justify-center bg-[#fffaf9] px-6 py-10 text-center">
        <section className="w-full max-w-md" aria-live="polite">
          <div className="mx-auto mb-8 h-16 w-16 animate-spin rounded-full border-4 border-rose-100 border-t-rose-500 motion-reduce:animate-none" aria-hidden="true" />
          <h1 className="mt-3 text-3xl font-bold text-zinc-950">{metadata.status === "recording" ? "Receiving your answers" : "Preparing your memories"}</h1>
          <p className="mt-5 text-xl leading-relaxed text-zinc-700">This page updates automatically when your story is saved.</p>
          {statusError && <p role="status" className="mt-5 text-lg text-zinc-700">{statusError}</p>}
        </section>
      </main>
    );
  }

  if (recorder.state === "SUCCESS") {
    return (
      <main className="flex min-h-[100svh] items-center justify-center bg-[#fffaf9] px-6 py-10 text-center">
        <section className="w-full max-w-md" aria-live="polite">
          <div className="mx-auto mb-8 grid h-28 w-28 place-items-center rounded-full bg-emerald-100 text-6xl text-emerald-700" aria-hidden="true">✓</div>
          <p className="text-lg font-bold text-[#d85048]">Saved</p>
          <h1 className="mt-3 text-4xl font-bold text-zinc-950">Thank you for your story.</h1>
          <p className="mt-5 text-2xl font-semibold leading-snug text-zinc-700">{successMessage}</p>
          {recorder.error && <p role="alert" className="mt-5 text-lg text-red-800">{recorder.error}</p>}
          {voiceTrainingConsent && (
            <p className="mt-6 rounded-3xl bg-white p-5 text-xl font-semibold leading-snug text-zinc-700 shadow-sm">
              You have given permission to use this recording for your avatar's voice.
            </p>
          )}
        </section>
      </main>
    );
  }

  if (recorder.state === "CONSENT") {
    return (
      <main className="flex min-h-[100svh] items-center justify-center bg-[#fffaf9] px-6 py-8">
        <section className="w-full max-w-md">
          <div className="mx-auto grid h-24 w-24 place-items-center rounded-full bg-emerald-100 text-5xl font-bold text-emerald-700" aria-hidden="true">✓</div>
          <p className="mt-7 text-center text-lg font-bold text-[#d85048]">{recorder.completedTurns} answers recorded</p>
          <h1 className="mt-3 text-center text-4xl font-bold text-zinc-950">Your story is ready.</h1>
          <p className="mt-4 text-center text-xl leading-relaxed text-zinc-600">
            Your answers will be saved as memories for {metadata.subject_name}.
          </p>

          <div className="mt-8 rounded-3xl border-2 border-zinc-200 bg-white p-5 shadow-sm">
            <label className="flex min-h-16 cursor-pointer items-center gap-4 text-xl font-bold text-zinc-900">
              <input
                type="checkbox"
                checked={speakerConfirmedSubject}
                onChange={(event) => {
                  setSpeakerConfirmedSubject(event.target.checked);
                  if (!event.target.checked) setVoiceTrainingConsent(false);
                }}
                className="h-8 w-8 shrink-0 accent-[#e85650]"
              />
              I am {metadata.subject_name}.
            </label>

            <div className="my-4 h-px bg-zinc-200" />

            <label className={`flex min-h-20 items-start gap-4 text-xl font-bold ${speakerConfirmedSubject ? "cursor-pointer text-zinc-900" : "text-zinc-400"}`}>
              <input
                type="checkbox"
                checked={voiceTrainingConsent}
                disabled={!speakerConfirmedSubject}
                onChange={(event) => setVoiceTrainingConsent(event.target.checked)}
                className="mt-1 h-8 w-8 shrink-0 accent-[#e85650]"
              />
              <span>
                Use my recording to create my avatar's voice.
                <span className="mt-2 block text-base font-medium leading-relaxed text-zinc-500">Optional. Your memories can be saved without this permission.</span>
              </span>
            </label>
          </div>

          {recorder.error && (
            <p className="mt-5 rounded-3xl bg-red-50 p-5 text-center text-lg font-bold leading-relaxed text-red-800" role="alert">
              {recorder.error}
            </p>
          )}

          <button
            type="button"
            onClick={() => void recorder.submit(speakerConfirmedSubject, voiceTrainingConsent)}
            className="mt-7 min-h-20 w-full rounded-3xl bg-[#ef6558] px-8 text-2xl font-bold text-white shadow-[0_12px_30px_rgba(201,64,54,0.24)] active:scale-[0.98] focus:outline-none focus-visible:ring-4 focus-visible:ring-[#8f211f]"
          >
            Save my story
          </button>
          <button type="button" onClick={() => void recorder.discard()} className="mt-4 min-h-12 w-full px-6 text-lg text-red-800">
            Delete saved answers
          </button>
        </section>
      </main>
    );
  }

  return (
    <main className="flex min-h-[100svh] items-center justify-center bg-[#fffaf9] px-6 py-8 text-center">
      <section className="flex w-full max-w-md flex-col items-center">
        <div className="mb-5 grid h-24 w-24 place-items-center rounded-full bg-[#ffe8e2] text-4xl font-bold text-[#a42d2a]" aria-hidden="true">
          {metadata.requester_name.trim().charAt(0).toUpperCase()}
        </div>
        <p className="text-xl font-semibold text-zinc-600">{metadata.requester_name} invited you to a private conversation</p>

        {recorder.state === "IDLE" || recorder.state === "ERROR" ? (
          <>
            <h1 className="mt-5 text-4xl font-bold leading-tight text-zinc-950">Tell your story.</h1>
            <p className="mt-4 text-xl leading-relaxed text-zinc-600">{prompts.length} questions. Take your time.</p>
          </>
        ) : (
          <>
            <p className="mt-5 text-lg font-bold uppercase tracking-[0.16em] text-[#d85048]">
              Question {recorder.currentPromptIndex + 1} of {recorder.totalPrompts}
            </p>
            <h1 className="mt-3 text-[clamp(1.75rem,7vw,2.5rem)] font-bold leading-tight tracking-tight text-zinc-950">{currentPrompt.question}</h1>
          </>
        )}

        <div className="mt-7 flex w-full gap-2" aria-label={`${recorder.completedTurns} of ${recorder.totalPrompts} answers recorded`}>
          {prompts.map((prompt, index) => (
            <div
              key={prompt.prompt_id}
              className={`h-2 flex-1 rounded-full transition-colors ${index < recorder.completedTurns ? "bg-emerald-500" : index === recorder.currentPromptIndex && busy ? "bg-[#ef6558]" : "bg-zinc-200"}`}
            />
          ))}
        </div>

        <div className="mt-6 h-24 w-full">
          {busy && <AudioWave level={recorder.level} active={recorder.state === "PLAYING_PROMPT" || recorder.state === "RECORDING"} />}
        </div>

        <p className="min-h-16 text-2xl font-bold text-zinc-800" aria-live="polite">
          {recorder.state === "RESTORING" && (recorder.error ?? "Checking saved answers...")}
          {recorder.state === "IDLE" && recorder.completedTurns > 0 && `${recorder.completedTurns} answers saved on this device.`}
          {recorder.state === "PLAYING_PROMPT" && "Getting your question ready..."}
          {recorder.state === "RECORDING" && "Listening..."}
          {recorder.state === "SILENCE_DETECTED" && "Saving your answer..."}
          {recorder.state === "UPLOADING" && "Saving your story..."}
          {recorder.state === "ERROR" && recorder.error}
        </p>

        {(recorder.state === "IDLE" || recorder.state === "ERROR") && (
          <button
            type="button"
            onClick={recorder.state === "ERROR" ? recorder.retry : recorder.start}
            className="mt-4 min-h-24 w-full rounded-3xl bg-[#ef6558] px-8 text-3xl font-bold text-white shadow-[0_12px_30px_rgba(201,64,54,0.28)] active:scale-[0.98] focus:outline-none focus-visible:ring-4 focus-visible:ring-[#8f211f] focus-visible:ring-offset-4"
          >
            {recorder.state === "ERROR" ? "Try again" : recorder.completedTurns > 0 ? "Continue interview" : "Start interview"}
          </button>
        )}

        {recorder.state === "IDLE" && recorder.completedTurns > 0 && (
          <button type="button" onClick={() => void recorder.discard()} className="mt-4 min-h-12 px-6 text-lg text-red-800">
            Delete saved answers
          </button>
        )}

        {recorder.state === "RESTORING" && recorder.error && (
          <button type="button" onClick={() => window.location.reload()} className="mt-4 min-h-12 px-6 text-xl font-semibold">
            Try again
          </button>
        )}

        {recorder.state === "RECORDING" && (
          <>
          <button
            type="button"
            onClick={() => void recorder.finish()}
            className="mt-4 min-h-20 w-full rounded-3xl border-4 border-zinc-900 bg-white px-8 text-2xl font-bold text-zinc-950 active:scale-[0.98] focus:outline-none focus-visible:ring-4 focus-visible:ring-[#8f211f]"
          >
            Finish answer
          </button>
          <button type="button" onClick={() => void recorder.pause()} className="mt-4 min-h-12 px-6 text-lg font-semibold">
            Save this answer and pause
          </button>
          </>
        )}
      </section>
    </main>
  );
}
