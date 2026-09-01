"use client";

import type { MicVAD } from "@ricky0123/vad-web";
import { useCallback, useEffect, useRef, useState } from "react";

export type PodcastPrompt = {
  prompt_id: string;
  category: string;
  question: string;
  audio_url: string | null;
};

export type SeniorRecorderState =
  | "IDLE" | "PLAYING_PROMPT" | "RECORDING" | "SILENCE_DETECTED"
  | "CONSENT" | "UPLOADING" | "SUCCESS" | "ERROR";

type RecorderOptions = {
  token: string;
  apiBaseURL: string;
  prompts: PodcastPrompt[];
  silenceMilliseconds?: number;
  noiseThreshold?: number;
  onSuccess: (message: string) => void;
};

const maximumTurnMilliseconds = 12 * 60 * 1000;

class NonRetryableUploadError extends Error {}

export function useSeniorVADRecorder({
  token,
  apiBaseURL,
  prompts,
  silenceMilliseconds = 5000,
  noiseThreshold = 0.018,
  onSuccess
}: RecorderOptions) {
  const [state, setState] = useState<SeniorRecorderState>("IDLE");
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

  const stopTurnMonitoring = useCallback(() => {
    if (animationRef.current !== null) cancelAnimationFrame(animationRef.current);
    animationRef.current = null;
    if (maximumDurationRef.current !== null) window.clearTimeout(maximumDurationRef.current);
    maximumDurationRef.current = null;
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

  useEffect(() => () => { void cleanup(); }, [cleanup]);

  const uploadWithRetry = useCallback(async (
    blobs: Blob[],
    speakerConfirmedSubject: boolean,
    voiceTrainingConsentGranted: boolean
  ) => {
    const backendURL = apiBaseURL.replace(/\/$/, "");
    if (!backendURL) throw new Error("STAY ist gerade nicht erreichbar.");
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
        const payload = await response.json().catch(() => ({})) as { detail?: string; message?: string };
        if (response.ok) return payload.message ?? "Vielen Dank! Deine Geschichte wurde sicher gespeichert.";
        if (
          response.status >= 400
          && response.status < 500
          && ![408, 409, 429].includes(response.status)
        ) {
          throw new NonRetryableUploadError(
            payload.detail ?? "Die Aufnahme konnte nicht verarbeitet werden."
          );
        }
      } catch (uploadError) {
        if (uploadError instanceof NonRetryableUploadError) throw uploadError;
        if (attempt === 3) throw uploadError;
      }
      await new Promise((resolve) => window.setTimeout(resolve, 1000 * 2 ** attempt));
    }
    throw new Error("Die Verbindung war zu schwach. Bitte versuche es noch einmal.");
  }, [apiBaseURL, token]);

  const submit = useCallback(async (
    speakerConfirmedSubject: boolean,
    voiceTrainingConsentGranted: boolean
  ) => {
    if (answersRef.current.length === 0) {
      setError("Es wurde noch keine Antwort aufgenommen.");
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
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Die Geschichte konnte nicht gespeichert werden.");
      setState("CONSENT");
    }
  }, [onSuccess, uploadWithRetry]);

  const playPromptAndRecord = useCallback(async (index: number) => {
    const context = contextRef.current;
    const stream = streamRef.current;
    const prompt = prompts[index];
    if (!context || !stream || !prompt) throw new Error("Die nächste Frage ist nicht verfügbar.");

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
        await new Promise<void>((resolve) => { source.onended = () => resolve(); source.start(); });
        promptWasPlayed = true;
      } catch {
        // The server recording is an enhancement. The unlocked browser voice
        // remains the truthful, generic fallback for an expiring signed URL.
      }
    }
    if (!promptWasPlayed) {
      await new Promise<void>((resolve, reject) => {
        const utterance = new SpeechSynthesisUtterance(prompt.question);
        utterance.lang = navigator.language || "de-DE";
        utterance.rate = 0.9;
        utterance.onend = () => resolve();
        utterance.onerror = () => reject(new Error("Die Frage konnte nicht vorgelesen werden."));
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(utterance);
      });
    }

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
    context.createMediaStreamSource(stream).connect(analyser);
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

  const finish = useCallback(async () => {
    if (stoppingRef.current || !recorderRef.current) return;
    stoppingRef.current = true;
    setState("SILENCE_DETECTED");
    stopTurnMonitoring();
    await vadRef.current?.pause().catch(() => undefined);
    const recorder = recorderRef.current;
    recorderRef.current = null;
    const stopped = new Promise<Blob>((resolve) => {
      recorder.addEventListener("stop", () => resolve(
        new Blob(chunksRef.current, { type: recorder.mimeType })
      ), { once: true });
    });
    if (recorder.state !== "inactive") recorder.stop();
    const blob = await stopped;
    try {
      if (blob.size < 1500 || !speechHeardRef.current) {
        throw new Error("Ich konnte noch keine Antwort hören. Bitte sprich etwas länger.");
      }
      answersRef.current.push(blob);
      const nextCount = answersRef.current.length;
      setCompletedTurns(nextCount);
      const nextIndex = currentPromptIndexRef.current + 1;
      if (nextIndex < prompts.length) {
        await new Promise((resolve) => window.setTimeout(resolve, 650));
        await playPromptAndRecord(nextIndex);
      } else {
        await cleanup();
        setState("CONSENT");
      }
    } catch (finishError) {
      setError(finishError instanceof Error ? finishError.message : "Die Antwort konnte nicht aufgenommen werden.");
      setState("ERROR");
    } finally {
      stoppingRef.current = false;
    }
  }, [cleanup, playPromptAndRecord, prompts.length, stopTurnMonitoring]);

  useEffect(() => {
    const handler = () => { void finish(); };
    window.addEventListener("stay-podcast-silence", handler);
    return () => window.removeEventListener("stay-podcast-silence", handler);
  }, [finish]);

  const start = useCallback(() => {
    setError(null);
    stoppingRef.current = false;
    answersRef.current = [];
    setCompletedTurns(0);
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
        streamRef.current = stream;
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
        await playPromptAndRecord(0);
      } catch (startError) {
        await cleanup();
        const denied = startError instanceof DOMException
          && ["NotAllowedError", "SecurityError"].includes(startError.name);
        setError(denied
          ? "Bitte erlaube den Mikrofonzugriff in den Browser-Einstellungen und tippe erneut auf Start."
          : startError instanceof Error ? startError.message : "Die Aufnahme konnte nicht gestartet werden.");
        setState("ERROR");
      }
    })();
  }, [cleanup, playPromptAndRecord]);

  const retry = useCallback(() => {
    setError(null);
    const context = contextRef.current;
    if (!context || !streamRef.current) {
      start();
      return;
    }

    // This runs directly from the recovery tap so Safari can unlock audio.
    void context.resume();
    void playPromptAndRecord(currentPromptIndexRef.current).catch(async (retryError) => {
      await cleanup();
      setError(retryError instanceof Error ? retryError.message : "Die Aufnahme konnte nicht neu gestartet werden.");
      setState("ERROR");
    });
  }, [cleanup, playPromptAndRecord, start]);

  return {
    state,
    level,
    error,
    currentPromptIndex,
    completedTurns,
    totalPrompts: prompts.length,
    start,
    finish,
    submit,
    retry
  };
}
