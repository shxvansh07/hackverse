/**
 * Thin wrapper over the browser Web Speech API.
 *
 * Support is uneven — Safari and Firefox lack SpeechRecognition entirely, and
 * Indic voice availability for SpeechSynthesis varies by platform. Everything
 * here degrades silently: `isRecognitionSupported()` lets the UI hide the
 * microphone rather than offer a button that does nothing.
 */

type RecognitionHandlers = {
  onResult: (transcript: string, isFinal: boolean) => void;
  onError: (message: string) => void;
  onEnd: () => void;
};

function getRecognitionCtor(): any {
  if (typeof window === 'undefined') return null;
  return (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition || null;
}

export function isRecognitionSupported(): boolean {
  return getRecognitionCtor() !== null;
}

export function isSynthesisSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window;
}

export class SpeechInput {
  private recognition: any = null;
  private active = false;

  start(languageTag: string, handlers: RecognitionHandlers): boolean {
    const Ctor = getRecognitionCtor();
    if (!Ctor) {
      handlers.onError('Voice input is not supported in this browser.');
      return false;
    }

    this.stop();

    const recognition = new Ctor();
    recognition.lang = languageTag;
    recognition.continuous = false;
    // Interim results let the UI show speech as it is recognised, which
    // matters when a patient is unsure whether the mic is working.
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onresult = (event: any) => {
      let interim = '';
      let final = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (result.isFinal) final += result[0].transcript;
        else interim += result[0].transcript;
      }
      if (final) handlers.onResult(final.trim(), true);
      else if (interim) handlers.onResult(interim.trim(), false);
    };

    recognition.onerror = (event: any) => {
      const messages: Record<string, string> = {
        'not-allowed': 'Microphone access was blocked. Please allow it in your browser settings.',
        'no-speech': 'I did not hear anything. Please try again.',
        'audio-capture': 'No microphone was found.',
        network: 'Voice recognition needs a network connection.',
      };
      handlers.onError(messages[event.error] || 'Voice input failed. Please type instead.');
      this.active = false;
    };

    recognition.onend = () => {
      this.active = false;
      handlers.onEnd();
    };

    try {
      recognition.start();
      this.recognition = recognition;
      this.active = true;
      return true;
    } catch {
      handlers.onError('Could not start voice input.');
      return false;
    }
  }

  stop(): void {
    if (this.recognition && this.active) {
      try {
        this.recognition.stop();
      } catch {
        /* already stopped */
      }
    }
    this.active = false;
  }

  get listening(): boolean {
    return this.active;
  }
}

/** Speak text aloud. No-op where synthesis or a matching voice is unavailable. */
export function speak(text: string, languageTag: string): void {
  if (!isSynthesisSupported() || !text.trim()) return;

  window.speechSynthesis.cancel();

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = languageTag;
  utterance.rate = 0.95; // slightly slow: this is clinical instruction, not chat
  utterance.pitch = 1;

  const voices = window.speechSynthesis.getVoices();
  const base = languageTag.split('-')[0];
  const match =
    voices.find((v) => v.lang === languageTag) ||
    voices.find((v) => v.lang.startsWith(base));
  if (match) utterance.voice = match;

  window.speechSynthesis.speak(utterance);
}

export function stopSpeaking(): void {
  if (isSynthesisSupported()) window.speechSynthesis.cancel();
}
