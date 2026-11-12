import { useEffect, useRef, useState } from "react";

interface Props {
  disabled: boolean;
  onRecorded: (audio: Blob) => void;
}

type RecorderState = "idle" | "recording";

export function VoiceRecorder({ disabled, onRecorded }: Props) {
  const [state, setState] = useState<RecorderState>("idle");
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const discardRef = useRef(false);

  const unsupported =
    typeof window === "undefined" ||
    !navigator.mediaDevices?.getUserMedia ||
    typeof window.MediaRecorder === "undefined";

  const stopStream = () => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  };

  useEffect(() => {
    return () => {
      discardRef.current = true;
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") recorder.stop();
      stopStream();
    };
  }, []);

  const start = async () => {
    setError(null);
    discardRef.current = false;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"].find(
        (type) => MediaRecorder.isTypeSupported?.(type),
      );
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onerror = () => {
        setError("The browser reported a recording error. No audio was saved.");
      };
      recorder.onstop = () => {
        const chunks = chunksRef.current;
        chunksRef.current = [];
        const type = recorder.mimeType || "audio/webm";
        recorderRef.current = null;
        stopStream();
        setState("idle");
        if (discardRef.current || chunks.length === 0) return;
        const blob = new Blob(chunks, { type });
        if (blob.size === 0) {
          setError("The recording was empty, so nothing was saved.");
          return;
        }
        onRecorded(blob);
      };
      recorderRef.current = recorder;
      recorder.start();
      setState("recording");
    } catch (err) {
      stopStream();
      setState("idle");
      const name = err instanceof DOMException ? err.name : "";
      if (name === "NotAllowedError" || name === "SecurityError") {
        setError(
          "Microphone access was denied. Allow microphone permission for this page, or import an audio file instead.",
        );
      } else if (name === "NotFoundError") {
        setError("No microphone was found. Import an audio file instead.");
      } else {
        setError("Could not start recording. Check microphone permissions and try again.");
      }
    }
  };

  const stop = () => {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
  };

  const cancel = () => {
    discardRef.current = true;
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== "inactive") recorder.stop();
    stopStream();
    recorderRef.current = null;
    chunksRef.current = [];
    setState("idle");
    setError(null);
  };

  return (
    <section className="voice-recorder" aria-labelledby="voice-recorder-heading">
      <div>
        <h3 id="voice-recorder-heading">Record a voice note</h3>
        <p className="voice-hint">
          Recordings stay on this machine. Audio is saved locally and never sent to a
          cloud service.
        </p>
      </div>
      {unsupported ? (
        <p className="voice-note" role="status">
          This browser does not support microphone recording. Import an audio file
          instead.
        </p>
      ) : state === "recording" ? (
        <div className="voice-recorder-actions">
          <span className="voice-recording-indicator" role="status">
            <span aria-hidden="true" className="voice-recording-dot" /> Recording…
          </span>
          <button type="button" className="btn btn-primary" onClick={stop} disabled={disabled}>
            Stop and save
          </button>
          <button type="button" className="btn btn-secondary" onClick={cancel}>
            Discard
          </button>
        </div>
      ) : (
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => void start()}
          disabled={disabled}
        >
          Start recording
        </button>
      )}
      {error && (
        <p className="voice-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
