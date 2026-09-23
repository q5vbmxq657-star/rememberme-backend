"use client";

import type { MicVAD } from "@ricky0123/vad-web";
import { useCallback, useEffect, useRef, useState } from "react";
import { deleteDraft, draftKey, readDraft, saveDraft } from "./interviewDraft";

export type PodcastPrompt = {
  prompt_id: string;
  category: string;
  question: string;
  audio_url: string | null;
};

export type SeniorRecorderState =
  | "IDLE" | "PLAYING_PROMPT" | "RECORDING" | "SILENCE_DETECTED"
  | "CONSENT" | "UPLOADING" | "SUCCESS" | "ERROR" | "RESTORING";

type RecorderOptions = {
  token: string;
  apiBaseURL: string;
  prompts: PodcastPrompt[];
  expiresAt: string;
  completed: boolean;
  silenceMilliseconds?: number;
  noiseThreshold?: number;
  onSuccess: (message: string) => void;
};

const maximumTurnMilliseconds = 12 * 60 * 1000;
const promptPlaybackTimeoutMilliseconds = 45 * 1000;

class NonRetryableUploadError extends Error {}

export function useSeniorVADRecorder({
  token,
  apiBaseURL,
  prompts,
  expiresAt,
  completed,
  silenceMilliseconds = 5000,
  noiseThreshold = 0.018,
  onSuccess
}: RecorderOptions) {
  const [state, setState] = useState<SeniorRecorderState>("RESTORING");
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [currentPromptIndex, setCurrentPromptIndex] = useState(0);
  const [completedTurns, setCompletedTurns] = useState(0);

  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const vadRef = useRef<MicVAD | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const answersRef = useRef<Blob[]>([]);
  const animationRef = useRef<number | null>(null);
  const silenceStartedRef = useRef<number | null>(null);
  const speechHeardRef = useRef(false);
  const stoppingRef = useRef(false);
  const maximumDurationRef = useRef<number | null>(null);
  const currentPromptIndexRef = useRef(0);
  const mediaSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const mountedRef = useRef(true);
  const draftKeyRef = useRef<string | null>(null);
  const promptIdentity = JSON.stringify(prompts.map(({ prompt_id, question }) => [prompt_id, question]));

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const key = await draftKey(apiBaseURL, token);
        if (cancelled) return;
        draftKeyRef.current = key;
        if (completed) {
          await deleteDraft(key);
          return;
        }
        const answers = await readDraft(key, promptIdentity);
        if (cancelled) return;
        if (answers.length > prompts.length) throw new Error("The saved answers do not match this interview.");
        answersRef.current = answers;
        currentPromptIndexRef.current = Math.min(answers.length, prompts.length - 1);
        setCurrentPromptIndex(currentPromptIndexRef.current);
        setCompletedTurns(answers.length);
        setState(answers.length === prompts.length ? "CONSENT" : "IDLE");
      } catch {
        if (!cancelled) {
          setError("Local recording storage is unavailable. Enable browser storage and reload before recording.");
          setState("RESTORING");
        }
      }
    })();
    return () => { cancelled = true; };
  }, [apiBaseURL, token, promptIdentity, prompts.length, completed]);

  const stopTurnMonitoring = useCallback(() => {
    if (animationRef.current !== null) cancelAnimationFrame(animationRef.current);
    animationRef.current = null;
    if (maximumDurationRef.current !== null) window.clearTimeout(maximumDurationRef.current);
    maximumDurationRef.current = null;
    mediaSourceRef.current?.disconnect();
    mediaSourceRef.current = null;
    analyserRef.current?.disconnect();
    analyserRef.current = null;
    setLevel(0);
  }, []);

  const cleanup = useCallback(async () => {
    stopTurnMonitoring();
    window.speechSynthesis?.cancel();
    const recorder = recorderRef.current;
    recorderRef.current = null;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    await vadRef.current?.destroy().catch(() => undefined);
    vadRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    await contextRef.current?.close().catch(() => undefined);
    contextRef.current = null;
  }, [stopTurnMonitoring]);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; void cleanup(); };
  }, [cleanup]);

  useEffect(() => {
    if (!["RECORDING", "SILENCE_DETECTED", "UPLOADING"].includes(state)) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [state]);

  const uploadWithRetry = useCallback(async (
    blobs: Blob[],
    speakerConfirmedSubject: boolean,
    voiceTrainingConsentGranted: boolean
  ) => {
    const backendURL = apiBaseURL.replace(/\/$/, "");
    if (!backendURL) throw new Error("STAY is temporarily unavailable.");
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const form = new FormData();
      blobs.forEach((blob, index) => {
        const extension = blob.type.includes("webm") ? "webm" : blob.type.includes("mp4") ? "m4a" : "wav";
        form.append("files", blob, `antwort-${index + 1}.${extension}`);
      });
      form.append("speaker_confirmed_subject", String(speakerConfirmedSubject));
      form.append("voice_training_consent_granted", String(voiceTrainingConsentGranted));
      try {
        const response = await fetch(`${backendURL}/v1/public/podcast/${encodeURIComponent(token)}/complete`, {
          method: "POST",
          body: form,
          cache: "no-store"
        });
        const payload = await response.json().catch(() => ({})) as {
          detail?: string; message?: string; status?: string; memory_ids?: unknown;
        };
        if (response.ok) {
          const ids = payload.memory_ids;
          if (payload.status !== "completed" || !Array.isArray(ids) || ids.length !== blobs.length
              || new Set(ids).size !== ids.length || !ids.every(id => typeof id === "string"
                && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id))) {
            throw new Error("STAY has not confirmed every answer yet. Please try again.");
          }
          return payload.message ?? "Your story has been saved.";
        }
        if (
          response.status >= 400
          && response.status < 500
          && ![408, 409, 429].includes(response.status)
        ) {
          throw new NonRetryableUploadError(
            payload.detail ?? "This recording could not be processed."
          );
        }
      } catch (uploadError) {
        if (uploadError instanceof NonRetryableUploadError) throw uploadError;
        if (attempt === 3) throw uploadError;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1000 * 2 ** attempt));
    }
    throw new Error("Your story could not be sent. Check your connection and try again.");
  }, [apiBaseURL, token]);

  const submit = useCallback(async (
    speakerConfirmedSubject: boolean,
    voiceTrainingConsentGranted: boolean
  ) => {
    if (answersRef.current.length === 0) {
      setError("Record an answer before saving your story.");
      setState("ERROR");
      return;
    }
    setError(null);
    setState("UPLOADING");
    try {
      const message = await uploadWithRetry(
        answersRef.current,
        speakerConfirmedSubject,
        speakerConfirmedSubject && voiceTrainingConsentGranted
      );
      setState("SUCCESS");
      onSuccess(message);
      answersRef.current = [];
      if (draftKeyRef.current) {
        await deleteDraft(draftKeyRef.current).catch(() => {
          setError("Your story is saved, but this browser could not remove its local recording. Clear this site's data on shared devices.");
        });
      }
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Your story could not be saved.");
      setState("CONSENT");
    }
  }, [onSuccess, uploadWithRetry]);

  const playPromptAndRecord = useCallback(async (index: number) => {
    const context = contextRef.current;
    const stream = streamRef.current;
    const prompt = prompts[index];
    if (!context || !stream || !prompt) throw new Error("The next question is unavailable. Please try again.");

    currentPromptIndexRef.current = index;
    setCurrentPromptIndex(index);
    await vadRef.current?.pause().catch(() => undefined);
    setState("PLAYING_PROMPT");
    let promptWasPlayed = false;
    if (prompt.audio_url) {
      try {
        const response = await fetch(prompt.audio_url, { cache: "no-store" });
        if (!response.ok) throw new Error("Prompt audio unavailable.");
        const buffer = await context.decodeAudioData(await response.arrayBuffer());
        const source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(context.destination);
        await new Promise<void>((resolve, reject) => {
          const timeout = window.setTimeout(() => {
            source.stop();
            reject(new Error("Prompt audio timed out."));
          }, promptPlaybackTimeoutMilliseconds);
          source.onended = () => {
            window.clearTimeout(timeout);
            source.disconnect();
            resolve();
          };
          source.start();
        });
        promptWasPlayed = true;
      } catch {
        // The server recording is an enhancement. The unlocked browser voice
        // remains the truthful, generic fallback for an expiring signed URL.
      }
    }
    if (!promptWasPlayed) {
      await new Promise<void>((resolve, reject) => {
        const utterance = new SpeechSynthesisUtterance(prompt.question);
        const timeout = window.setTimeout(() => {
          window.speechSynthesis.cancel();
          reject(new Error("The question could not be played. Please try again."));
        }, promptPlaybackTimeoutMilliseconds);
        utterance.lang = navigator.language || "de-DE";
        utterance.rate = 0.9;
        utterance.onend = () => {
          window.clearTimeout(timeout);
          resolve();
        };
        utterance.onerror = () => {
          window.clearTimeout(timeout);
          reject(new Error("The question could not be played. Please try again."));
        };
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(utterance);
      });
    }

    if (!mountedRef.current) return;
    chunksRef.current = [];
    speechHeardRef.current = false;
    silenceStartedRef.current = null;
    // Prefer Apple's native container so the returned recording can flow into
    // the canonical iOS voice-preparation pipeline without lossy transcoding.
    const mimeType = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"]
      .find((candidate) => MediaRecorder.isTypeSupported(candidate));
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size > 0) chunksRef.current.push(event.data);
    });
    recorderRef.current = recorder;
    recorder.start(1000);

    const analyser = context.createAnalyser();
    analyser.fftSize = 1024;
    const mediaSource = context.createMediaStreamSource(stream);
    mediaSource.connect(analyser);
    mediaSourceRef.current = mediaSource;
    analyserRef.current = analyser;
    const samples = new Float32Array(analyser.fftSize);
    const monitor = () => {
      analyser.getFloatTimeDomainData(samples);
      let squareSum = 0;
      for (const value of samples) squareSum += value * value;
      const rms = Math.sqrt(squareSum / samples.length);
      setLevel(Math.min(1, rms / 0.16));
      if (rms >= noiseThreshold) {
        speechHeardRef.current = true;
        silenceStartedRef.current = null;
      } else if (speechHeardRef.current) {
        silenceStartedRef.current ??= performance.now();
        if (performance.now() - silenceStartedRef.current >= silenceMilliseconds) {
          window.dispatchEvent(new CustomEvent("stay-podcast-silence"));
          return;
        }
      }
      animationRef.current = requestAnimationFrame(monitor);
    };
    monitor();
    maximumDurationRef.current = window.setTimeout(
      () => window.dispatchEvent(new CustomEvent("stay-podcast-silence")),
      maximumTurnMilliseconds
    );
    await vadRef.current?.start();
    setState("RECORDING");
  }, [noiseThreshold, prompts, silenceMilliseconds]);

  const finish = useCallback(async (pauseAfterAnswer = false) => {
    if (stoppingRef.current || !recorderRef.current) return;
    stoppingRef.current = true;
    setState("SILENCE_DETECTED");
    stopTurnMonitoring();
    await vadRef.current?.pause().catch(() => undefined);
    const recorder = recorderRef.current;
    recorderRef.current = null;
    const blob = recorder.state === "inactive"
      ? new Blob(chunksRef.current, { type: recorder.mimeType })
      : await new Promise<Blob>((resolve) => {
        recorder.addEventListener("stop", () => resolve(
          new Blob(chunksRef.current, { type: recorder.mimeType })
        ), { once: true });
        recorder.stop();
      });
    try {
      if (blob.size < 1500 || !speechHeardRef.current) {
        throw new Error("We could not hear an answer. Please speak a little longer.");
      }
      const key = draftKeyRef.current;
      if (!key) throw new Error("Local recording storage is not ready. Reload and try again.");
      const answers = [...answersRef.current, blob];
      await saveDraft(key, promptIdentity, Date.parse(expiresAt), answers);
      answersRef.current = answers;
      const nextCount = answersRef.current.length;
      setCompletedTurns(nextCount);
      const nextIndex = currentPromptIndexRef.current + 1;
      if (nextIndex < prompts.length) {
        if (pauseAfterAnswer) {
          await cleanup();
          currentPromptIndexRef.current = nextIndex;
          setCurrentPromptIndex(nextIndex);
          setState("IDLE");
        } else {
          await new Promise((resolve) => window.setTimeout(resolve, 650));
          await playPromptAndRecord(nextIndex);
        }
      } else {
        await cleanup();
        setState("CONSENT");
      }
    } catch (finishError) {
      setError(finishError instanceof Error ? finishError.message : "Your answer could not be recorded.");
      setState("ERROR");
    } finally {
      stoppingRef.current = false;
    }
  }, [cleanup, playPromptAndRecord, prompts.length, stopTurnMonitoring, promptIdentity, expiresAt]);

  useEffect(() => {
    const handler = () => { void finish(); };
    window.addEventListener("stay-podcast-silence", handler);
    return () => window.removeEventListener("stay-podcast-silence", handler);
  }, [finish]);

  const start = useCallback(() => {
    setError(null);
    stoppingRef.current = false;
    if (answersRef.current.length === prompts.length) {
      setState("CONSENT");
      return;
    }
    setState("PLAYING_PROMPT");
    const AudioContextClass = window.AudioContext
      ?? (window as typeof window & { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const context = new AudioContextClass();
    contextRef.current = context;
    void context.resume();
    const microphonePromise = navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false
    });

    void (async () => {
      try {
        const stream = await microphonePromise;
        if (!mountedRef.current) {
          stream.getTracks().forEach(track => track.stop());
          await context.close().catch(() => undefined);
          return;
        }
        streamRef.current = stream;
        try {
          const { MicVAD } = await import("@ricky0123/vad-web");
          vadRef.current = await MicVAD.new({
            model: "v5",
            baseAssetPath: "/vad/",
            onnxWASMBasePath: "/ort/",
            startOnLoad: false,
            audioContext: context,
            getStream: async () => stream,
            pauseStream: async () => undefined,
            resumeStream: async () => stream,
            onSpeechStart: () => {
              speechHeardRef.current = true;
              silenceStartedRef.current = null;
            },
            onSpeechEnd: () => {
              if (speechHeardRef.current) silenceStartedRef.current ??= performance.now();
            }
          });
        } catch {
          // RMS monitoring is the canonical offline-capable detector. The
          // WASM model adds accuracy but must never block a senior's answer.
          vadRef.current = null;
        }
        if (!mountedRef.current) { await cleanup(); return; }
        await playPromptAndRecord(answersRef.current.length);
      } catch (startError) {
        await cleanup();
        const denied = startError instanceof DOMException
          && ["NotAllowedError", "SecurityError"].includes(startError.name);
        setError(denied
          ? "Allow microphone access in your browser settings, then try again."
          : startError instanceof Error ? startError.message : "Recording could not start.");
        setState("ERROR");
      }
    })();
  }, [cleanup, playPromptAndRecord, prompts.length]);

  const retry = useCallback(() => {
    setError(null);
    const context = contextRef.current;
    if (!context || !streamRef.current) {
      start();
      return;
    }

    // This runs directly from the recovery tap so Safari can unlock audio.
    void context.resume();
    if (answersRef.current.length === prompts.length) {
      void cleanup();
      setState("CONSENT");
      return;
    }
    void playPromptAndRecord(answersRef.current.length).catch(async (retryError) => {
      await cleanup();
      setError(retryError instanceof Error ? retryError.message : "Recording could not restart.");
      setState("ERROR");
    });
  }, [cleanup, playPromptAndRecord, start, prompts.length]);

  const discard = useCallback(async () => {
    if (!window.confirm("Delete the answers saved on this device? This cannot be undone.")) return;
    try {
      if (draftKeyRef.current) await deleteDraft(draftKeyRef.current);
      answersRef.current = [];
      setCompletedTurns(0);
      currentPromptIndexRef.current = 0;
      setCurrentPromptIndex(0);
      setError(null);
      setState("IDLE");
    } catch {
      setError("The saved answers could not be removed. Please try again.");
    }
  }, []);

  return {
    state,
    level,
    error,
    currentPromptIndex,
    completedTurns,
    totalPrompts: prompts.length,
    start,
    finish,
    pause: () => finish(true),
    discard,
    submit,
    retry
  };
}
