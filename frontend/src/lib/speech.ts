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

/** Chrome (and others) return an empty voice list on the first call in a page
 *  session — the list loads asynchronously and `getVoices()` doesn't wait for
 *  it. Calling speak() before that load finishes silently picks no voice at
 *  all, which is why a page's very first utterance can be inaudible even
 *  though every later one works fine. Resolved once, cached, reused. */
let voicesReady: Promise<SpeechSynthesisVoice[]> | null = null;

function loadVoices(): Promise<SpeechSynthesisVoice[]> {
  if (!isSynthesisSupported()) return Promise.resolve([]);

  const existing = window.speechSynthesis.getVoices();
  if (existing.length > 0) return Promise.resolve(existing);

  if (!voicesReady) {
    voicesReady = new Promise((resolve) => {
      const onChange = () => {
        const voices = window.speechSynthesis.getVoices();
        if (voices.length > 0) {
          window.speechSynthesis.removeEventListener('voiceschanged', onChange);
          resolve(voices);
        }
      };
      window.speechSynthesis.addEventListener('voiceschanged', onChange);
      // Some browsers never fire voiceschanged reliably; don't wait forever.
      setTimeout(() => resolve(window.speechSynthesis.getVoices()), 1000);
    });
  }
  return voicesReady;
}

/** Speak text aloud. Resolves to whether a voice matching `languageTag` was
 *  actually found — callers that need to know playback might have been
 *  inaudible (e.g. no voice installed for that language) can check this
 *  instead of assuming silence means success. */
export async function speak(text: string, languageTag: string): Promise<boolean> {
  if (!isSynthesisSupported() || !text.trim()) return false;

  window.speechSynthesis.cancel();

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = languageTag;
  utterance.rate = 0.95; // slightly slow: this is clinical instruction, not chat
  utterance.pitch = 1;

  const voices = await loadVoices();
  const base = languageTag.split('-')[0];
  const match =
    voices.find((v) => v.lang === languageTag) ||
    voices.find((v) => v.lang.startsWith(base));
  if (match) utterance.voice = match;
  else if (voices.length > 0) {
    // Voices did load — there just isn't one for this language. Speaking
    // anyway would use whatever default voice Chrome falls back to, which
    // usually can't render non-Latin script and produces no audible output.
    console.warn(`No speech voice available for "${languageTag}". Playback may be silent.`);
  }

  window.speechSynthesis.speak(utterance);
  return Boolean(match);
}

export function stopSpeaking(): void {
  if (isSynthesisSupported()) window.speechSynthesis.cancel();
}
