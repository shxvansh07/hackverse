'use client';

import React, { useState, useEffect, useRef } from 'react';
import Link from 'next/link';
import { api, PatientSession, ChatMessage, Prescription, Appointment, ReferralInfo } from '@/lib/api';

const SUPPORTED_LANGUAGES = [
  { code: 'hi', speechCode: 'hi-IN', label: 'हिन्दी (Hindi)' },
  { code: 'kn', speechCode: 'kn-IN', label: 'ಕನ್ನಡ (Kannada)' },
  { code: 'ta', speechCode: 'ta-IN', label: 'தமிழ் (Tamil)' },
  { code: 'te', speechCode: 'te-IN', label: 'తెలుగు (Telugu)' },
  { code: 'bn', speechCode: 'bn-IN', label: 'বাংলা (Bengali)' },
  { code: 'mr', speechCode: 'mr-IN', label: 'मराठी (Marathi)' },
  { code: 'gu', speechCode: 'gu-IN', label: 'ગુજરાતી (Gujarati)' },
  { code: 'en', speechCode: 'en-IN', label: 'Hinglish / English (IN)' },
  { code: 'en', speechCode: 'en-US', label: 'English (US)' },
];

export default function PatientPortal() {
  const [lang, setLang] = useState<string>('hi');
  const [speechLang, setSpeechLang] = useState<string>('hi-IN');
  const [session, setSession] = useState<PatientSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputMsg, setInputMsg] = useState('');
  const [loading, setLoading] = useState(false);
  const [triageStatus, setTriageStatus] = useState<'LOW_RISK' | 'UNCERTAIN' | 'URGENT' | 'COLLECTING'>('COLLECTING');
  const [isComplete, setIsComplete] = useState(false);
  const [caseId, setCaseId] = useState<string | null>(null);
  const [prescription, setPrescription] = useState<Prescription | null>(null);
  const [rxLang, setRxLang] = useState<string>('hi');

  const [appointment, setAppointment] = useState<Appointment | null>(null);
  const [referral, setReferral] = useState<ReferralInfo | null>(null);
  const [bookingLoading, setBookingLoading] = useState(false);
  const [recommendedSpecialty, setRecommendedSpecialty] = useState<string | null>(null);
  const [specialistBookingLoading, setSpecialistBookingLoading] = useState(false);
  const [emergencyBookingLoading, setEmergencyBookingLoading] = useState(false);

  // VAD & Voice Call Engine State & REFS
  const [isCallActive, setIsCallActive] = useState(false);
  const [isAiSpeaking, setIsAiSpeaking] = useState(false);
  const [isPatientSpeaking, setIsPatientSpeaking] = useState(false);
  const [audioVolume, setAudioVolume] = useState(0);

  const isCallActiveRef = useRef<boolean>(false);
  const isAiSpeakingRef = useRef<boolean>(false);
  const isProcessingRef = useRef<boolean>(false);
  const capturedTextRef = useRef<string>('');
  const speechLangRef = useRef<string>('hi-IN');

  const recognitionRef = useRef<any>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const silenceTimerRef = useRef<NodeJS.Timeout | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    speechLangRef.current = speechLang;
  }, [speechLang]);

  const updateAiSpeaking = (val: boolean) => {
    isAiSpeakingRef.current = val;
    setIsAiSpeaking(val);
  };

  const updateCallActive = (val: boolean) => {
    isCallActiveRef.current = val;
    setIsCallActive(val);
  };

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, loading, isAiSpeaking, audioVolume]);

  const getGreetingMessage = (code: string) => {
    switch (code) {
      case 'kn': return 'ನಮಸ್ಕಾರ. ದಯವಿಟ್ಟು ನಿಮ್ಮ ಆರೋಗ್ಯದ ಸಮಸ್ಯೆಗಳನ್ನು ತಿಳಿಸಿ.';
      case 'ta': return 'வணக்கம். உங்கள் சுகாதாரப் பிரச்சினைகளை விவரிக்கவும்.';
      case 'te': return 'నమస్కారం. దయచేసి మీ ఆరోగ్య సమస్యలను తెలియజేయండి.';
      case 'bn': return 'নমস্কার। আপনার স্বাস্থ্য সমস্যা বিস্তারিত জানান।';
      case 'mr': return 'नमस्कार. कृपया तुमच्या आरोग्य समस्येचे वर्णन करा.';
      case 'gu': return 'નમસ્તે. કૃપા કરીને તમારી આરોગ્ય સમસ્યાઓ જણાવો.';
      case 'en': return 'Hello. Please describe the health problem or symptoms you are experiencing today.';
      default: return 'नमस्ते। कृपया अपनी स्वास्थ्य संबंधी समस्या या लक्षणों का विवरण दें।';
    }
  };

  const initSession = async (selectedLang: string) => {
    setLoading(true);
    stopCall();
    try {
      const sess = await api.startSession(selectedLang);
      setSession(sess);
      setMessages([
        {
          sender: 'ai',
          text: getGreetingMessage(selectedLang),
          timestamp: new Date().toISOString()
        }
      ]);
      setTriageStatus('COLLECTING');
      setIsComplete(false);
      setPrescription(null);
      setAppointment(null);
      setReferral(null);
      setRecommendedSpecialty(null);
    } catch (err) {
      console.error('Session start error:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    initSession(lang);
    return () => {
      stopCall();
    };
  }, []);

  const handleLanguageSelect = (langObj: typeof SUPPORTED_LANGUAGES[0]) => {
    setLang(langObj.code);
    setRxLang(langObj.code);
    setSpeechLang(langObj.speechCode);
    speechLangRef.current = langObj.speechCode;
    initSession(langObj.code);
  };

  // Initialize Web Audio VAD Volume Analyzer
  const startAudioAnalyzer = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;

      const audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 64;
      const source = audioCtx.createMediaStreamSource(stream);
      source.connect(analyser);

      audioContextRef.current = audioCtx;
      analyserRef.current = analyser;

      const dataArray = new Uint8Array(analyser.frequencyBinCount);

      const checkVolume = () => {
        if (!isCallActiveRef.current) return;

        analyser.getByteFrequencyData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
          sum += dataArray[i];
        }
        const avg = sum / dataArray.length;
        const normalizedVol = Math.min(100, Math.round((avg / 128) * 100));
        setAudioVolume(normalizedVol);

        if (!isAiSpeakingRef.current && !isProcessingRef.current) {
          if (normalizedVol > 8) {
            setIsPatientSpeaking(true);
            if (silenceTimerRef.current) {
              clearTimeout(silenceTimerRef.current);
              silenceTimerRef.current = null;
            }
          } else if (isPatientSpeaking) {
            if (!silenceTimerRef.current) {
              silenceTimerRef.current = setTimeout(() => {
                setIsPatientSpeaking(false);
                triggerAutoSubmit();
              }, 800);
            }
          }
        }

        animationFrameRef.current = requestAnimationFrame(checkVolume);
      };

      checkVolume();
    } catch (err) {
      console.error('Audio analyzer error:', err);
    }
  };

  const stopAudioAnalyzer = () => {
    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
    }
    if (audioContextRef.current) {
      try { audioContextRef.current.close(); } catch (e) {}
      audioContextRef.current = null;
    }
    setAudioVolume(0);
    setIsPatientSpeaking(false);
  };

  // Continuous Speech Recognition Engine with multi-language dialect support
  const startSpeechRecognition = () => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) return;

    if (recognitionRef.current) {
      try { recognitionRef.current.abort(); } catch (e) {}
    }

    const recognition = new SpeechRecognition();
    recognition.lang = speechLangRef.current;
    recognition.continuous = true;
    recognition.interimResults = true;

    recognition.onresult = (event: any) => {
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        const transcript = result[0].transcript;

        if (transcript && transcript.trim()) {
          capturedTextRef.current = transcript.trim();
          setInputMsg(transcript.trim());

          if (result.isFinal && !isProcessingRef.current && !isAiSpeakingRef.current) {
            triggerAutoSubmit();
          }
        }
      }
    };

    recognition.onerror = () => {
      if (isCallActiveRef.current && !isAiSpeakingRef.current && !isProcessingRef.current) {
        setTimeout(() => startSpeechRecognition(), 400);
      }
    };

    recognition.onend = () => {
      if (capturedTextRef.current.trim() && !isProcessingRef.current && !isAiSpeakingRef.current) {
        triggerAutoSubmit();
      } else if (isCallActiveRef.current && !isAiSpeakingRef.current && !isProcessingRef.current) {
        setTimeout(() => startSpeechRecognition(), 300);
      }
    };

    recognitionRef.current = recognition;
    try {
      recognition.start();
    } catch (e) {}
  };

  // Auto-Submit Transcribed Voice Text to Backend automatically
  const triggerAutoSubmit = () => {
    const textToSubmit = capturedTextRef.current.trim() || inputMsg.trim();
    if (!textToSubmit || isProcessingRef.current || isAiSpeakingRef.current) return;

    capturedTextRef.current = '';
    isProcessingRef.current = true;

    if (recognitionRef.current) {
      try { recognitionRef.current.abort(); } catch (e) {}
    }

    handleSendMessageText(textToSubmit);
  };

  // Speak AI Response Out Loud
  const speakAiResponse = (text: string, onComplete?: () => void) => {
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
      updateAiSpeaking(true);

      if (recognitionRef.current) {
        try { recognitionRef.current.abort(); } catch (e) {}
      }

      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = speechLangRef.current;
      utterance.rate = 1.0;

      utterance.onend = () => {
        updateAiSpeaking(false);
        isProcessingRef.current = false;
        if (isCallActiveRef.current) {
          startSpeechRecognition();
        }
        if (onComplete) onComplete();
      };

      utterance.onerror = () => {
        updateAiSpeaking(false);
        isProcessingRef.current = false;
        if (isCallActiveRef.current) {
          startSpeechRecognition();
        }
        if (onComplete) onComplete();
      };

      window.speechSynthesis.speak(utterance);
    } else {
      isProcessingRef.current = false;
      if (onComplete) onComplete();
    }
  };

  // Toggle Voice Call Mode
  const toggleVoiceCall = () => {
    if (isCallActiveRef.current) {
      stopCall();
    } else {
      updateCallActive(true);
      isProcessingRef.current = false;
      capturedTextRef.current = '';

      startAudioAnalyzer();
      startSpeechRecognition();

      const lastMsg = messages[messages.length - 1];
      if (lastMsg && lastMsg.sender === 'ai') {
        speakAiResponse(lastMsg.text);
      }
    }
  };

  const stopCall = () => {
    updateCallActive(false);
    updateAiSpeaking(false);
    isProcessingRef.current = false;
    capturedTextRef.current = '';

    stopAudioAnalyzer();

    if (recognitionRef.current) {
      try { recognitionRef.current.abort(); } catch (e) {}
    }
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
  };

  // Send Patient Message
  const handleSendMessageText = async (textToSend: string) => {
    if (!textToSend.trim() || !session || loading) return;

    setInputMsg('');
    const newMsg: ChatMessage = {
      sender: 'patient',
      text: textToSend,
      timestamp: new Date().toISOString()
    };
    setMessages((prev) => [...prev, newMsg]);
    setLoading(true);

    try {
      const res = await api.sendMessage(session.session_id, textToSend);
      setTriageStatus(res.triage_status);
      if (res.case_id) setCaseId(res.case_id);

      if (res.auto_booked_appointment) {
        setAppointment(res.auto_booked_appointment);
      }

      if (res.recommended_specialty) {
        setRecommendedSpecialty(res.recommended_specialty);
      }

      const aiText = res.ai_response;
      setMessages((prev) => [
        ...prev,
        {
          sender: 'ai',
          text: aiText,
          timestamp: new Date().toISOString()
        }
      ]);

      if (res.is_complete || res.triage_status === 'URGENT') {
        setIsComplete(true);
      }

      if (isCallActiveRef.current) {
        speakAiResponse(aiText);
      }
    } catch (err) {
      console.error('Send error:', err);
      isProcessingRef.current = false;
    } finally {
      setLoading(false);
    }
  };

  const handleFormSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (inputMsg.trim()) {
      handleSendMessageText(inputMsg.trim());
    }
  };

  useEffect(() => {
    if (!caseId || triageStatus === 'URGENT') return;

    const interval = setInterval(async () => {
      try {
        const detail = await api.getCaseDetail(caseId);
        if (detail.prescription_draft && (detail.prescription_draft.status === 'APPROVED' || detail.prescription_draft.status === 'MODIFIED')) {
          const rxData = await api.getPrescription(detail.prescription_draft.prescription_id, rxLang);
          setPrescription(rxData);
        }
        if (detail.appointment) {
          setAppointment(detail.appointment);
        }
        if (detail.referral) {
          setReferral(detail.referral);
        }
      } catch (err) {
        console.error('Polling error:', err);
      }
    }, 3000);

    return () => clearInterval(interval);
  }, [caseId, rxLang, triageStatus]);

  const handlePrescriptionLangToggle = async (newLang: string) => {
    setRxLang(newLang);
    if (prescription) {
      try {
        const rxData = await api.getPrescription(prescription.prescription_id, newLang);
        setPrescription(rxData);
      } catch (err) {
        console.error('Translation error:', err);
      }
    }
  };

  const handleBookOptionalAppointment = async () => {
    if (!caseId) return;
    setBookingLoading(true);
    try {
      const apt = await api.bookAppointment(caseId);
      setAppointment(apt);
    } catch (err) {
      console.error('Booking error:', err);
    } finally {
      setBookingLoading(false);
    }
  };

  const handleConfirmEmergencyAppointment = async () => {
    if (!caseId) return;
    setEmergencyBookingLoading(true);
    try {
      const apt = await api.bookAppointment(caseId);
      setAppointment(apt);
    } catch (err) {
      console.error('Emergency booking error:', err);
    } finally {
      setEmergencyBookingLoading(false);
    }
  };

  const handleBookSpecialistAppointment = async () => {
    if (!caseId || !recommendedSpecialty) return;
    setSpecialistBookingLoading(true);
    try {
      const apt = await api.bookAppointment(caseId, undefined, undefined, recommendedSpecialty);
      setAppointment(apt);
    } catch (err) {
      console.error('Specialist booking error:', err);
    } finally {
      setSpecialistBookingLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-white text-black flex flex-col justify-between p-4 sm:p-6">
      {/* Header */}
      <header className="max-w-4xl mx-auto w-full flex items-center justify-between py-3 px-4 bg-neutral-50 border border-neutral-300 rounded-lg mb-4">
        <div className="flex items-center space-x-3">
          <Link href="/" className="text-xs text-neutral-600 hover:text-black font-semibold">
            ← Home
          </Link>
          <span className="text-sm font-bold text-black">Patient Intake</span>
        </div>

        <div className="flex items-center space-x-2">
          {/* Top Indian & Global Language Dropdown Selector */}
          <select
            value={speechLang}
            onChange={(e) => {
              const selected = SUPPORTED_LANGUAGES.find(l => l.speechCode === e.target.value);
              if (selected) handleLanguageSelect(selected);
            }}
            className="bg-white border border-neutral-300 text-black rounded px-2.5 py-1 text-xs font-mono font-bold focus:outline-none"
          >
            {SUPPORTED_LANGUAGES.map((l, idx) => (
              <option key={idx} value={l.speechCode}>
                🌐 {l.label}
              </option>
            ))}
          </select>

          {/* Hands-Free Voice Call Button */}
          <button
            onClick={toggleVoiceCall}
            className={`px-3.5 py-1.5 rounded text-xs font-mono font-bold transition-all shadow-sm ${
              isCallActive
                ? 'bg-red-600 text-white animate-pulse border border-red-700'
                : 'bg-black text-white hover:bg-neutral-800'
            }`}
          >
            {isCallActive ? '[End Voice Call]' : '📞 [Start Open-Mic Call]'}
          </button>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-4xl mx-auto w-full flex-1 flex flex-col space-y-4 mb-4">
        {/* Status Header */}
        <div className="bg-neutral-50 p-3 rounded-lg border border-neutral-300 flex justify-between items-center text-xs font-mono text-black">
          <span>
            STATUS: {isCallActive ? `[LIVE CALL - LANGUAGE: ${speechLang}]` : triageStatus === 'URGENT' ? '[EMERGENCY HIGH RISK]' : prescription ? '[PRESCRIPTION APPROVED]' : isComplete ? '[WAITING DOCTOR REVIEW]' : '[COLLECTING DETAILS]'}
          </span>
          <button onClick={() => initSession(lang)} className="text-neutral-600 hover:text-black font-bold">
            [Reset]
          </button>
        </div>

        {/* DYNAMIC REAL-TIME DECIBEL SOUNDWAVE GRAPHIC */}
        {isCallActive && (
          <div className="bg-black text-white p-4 rounded-lg border border-neutral-800 flex items-center justify-between font-mono text-xs shadow-xl">
            <div className="flex items-center space-x-4">
              {/* Dynamic Equalizer Bars */}
              <div className="flex items-end space-x-1.5 h-7">
                <div
                  className="w-1.5 bg-white rounded transition-all duration-75"
                  style={{ height: `${Math.max(6, Math.min(28, (audioVolume * 1.2)))}px` }}
                />
                <div
                  className="w-1.5 bg-white rounded transition-all duration-75"
                  style={{ height: `${Math.max(8, Math.min(28, (audioVolume * 1.6)))}px` }}
                />
                <div
                  className="w-1.5 bg-white rounded transition-all duration-75"
                  style={{ height: `${Math.max(10, Math.min(28, (audioVolume * 2.0)))}px` }}
                />
                <div
                  className="w-1.5 bg-white rounded transition-all duration-75"
                  style={{ height: `${Math.max(8, Math.min(28, (audioVolume * 1.5)))}px` }}
                />
                <div
                  className="w-1.5 bg-white rounded transition-all duration-75"
                  style={{ height: `${Math.max(6, Math.min(28, (audioVolume * 1.1)))}px` }}
                />
              </div>

              <div className="space-y-0.5">
                <div className="font-bold text-white text-xs">
                  {isAiSpeaking
                    ? 'AI Assistant Responding...'
                    : isPatientSpeaking
                      ? 'Voice Activity Detected...'
                      : 'Open-Mic Active (Speak anytime)'}
                </div>
                <div className="text-[10px] text-neutral-400">
                  Language Dialect: {speechLang} | 100% Hands-Free Call Active
                </div>
              </div>
            </div>

            <button onClick={stopCall} className="px-3 py-1 bg-neutral-800 hover:bg-neutral-700 text-white rounded font-mono text-[11px]">
              [Hang Up]
            </button>
          </div>
        )}

        {/* URGENT EMERGENCY ALERT */}
        {triageStatus === 'URGENT' && (
          <div className="bg-red-50 border-2 border-red-600 p-5 rounded-lg text-black space-y-3">
            <h3 className="text-base font-bold uppercase tracking-wider text-red-700">
              ⚠️ URGENT EMERGENCY ALERT
            </h3>
            <p className="text-xs text-red-900 leading-relaxed font-medium">
              High-risk medical symptoms detected. Please proceed immediately to an emergency room or call emergency medical services.
            </p>

            {appointment ? (
              <div className="bg-white p-3 rounded border border-red-300 text-xs font-mono space-y-1">
                <div className="font-bold text-red-700">[EMERGENCY APPOINTMENT CONFIRMED]</div>
                <div>Time Slot: {appointment.slot_time}</div>
                <div>Status: {appointment.status}</div>
                <div>Ref ID: {appointment.appointment_id}</div>
                {(appointment.specialty || recommendedSpecialty) && (
                  <div>Recommended Specialist: {appointment.specialty || recommendedSpecialty}</div>
                )}
              </div>
            ) : (
              <div className="bg-white p-3 rounded border border-red-300 space-y-2">
                {recommendedSpecialty && (
                  <div className="text-xs font-mono text-red-700">Recommended Specialist: {recommendedSpecialty}</div>
                )}
                <button
                  disabled={emergencyBookingLoading}
                  onClick={handleConfirmEmergencyAppointment}
                  className="w-full px-4 py-2 bg-red-600 text-white font-bold rounded text-xs hover:bg-red-700 disabled:opacity-50"
                >
                  {emergencyBookingLoading ? 'Booking...' : 'Confirm & Book Emergency Appointment Now'}
                </button>
              </div>
            )}
          </div>
        )}

        {/* UNCERTAIN — IN-PERSON SPECIALIST EVALUATION PROMPT */}
        {triageStatus === 'UNCERTAIN' && (
          <div className="bg-amber-50 border-2 border-amber-500 p-5 rounded-lg text-black space-y-3">
            <h3 className="text-base font-bold uppercase tracking-wider text-amber-700">
              In-Person Specialist Evaluation Recommended
            </h3>
            <p className="text-xs text-amber-900 leading-relaxed font-medium">
              Your symptoms need an in-person evaluation for an accurate assessment.
              {recommendedSpecialty && ` Recommended specialist: ${recommendedSpecialty}.`}
            </p>

            {!appointment ? (
              <button
                disabled={specialistBookingLoading || !recommendedSpecialty}
                onClick={handleBookSpecialistAppointment}
                className="px-4 py-2 bg-black text-white font-bold rounded text-xs hover:bg-neutral-800 disabled:opacity-50"
              >
                {specialistBookingLoading ? 'Booking...' : 'Book In-Person Appointment'}
              </button>
            ) : (
              <div className="bg-white p-3 rounded border border-amber-300 text-xs font-mono space-y-1">
                <div className="font-bold text-amber-700">[IN-PERSON APPOINTMENT BOOKED]</div>
                <div>Time Slot: {appointment.slot_time}</div>
                <div>Location: {appointment.clinic_location}</div>
                <div>Ref ID: {appointment.appointment_id}</div>
                {appointment.specialty && <div>Specialist: {appointment.specialty}</div>}
              </div>
            )}
          </div>
        )}

        {/* PRESCRIPTION CARD */}
        {prescription && (prescription.status === 'APPROVED' || prescription.status === 'MODIFIED') && (
          <div className="bg-neutral-50 border border-neutral-400 p-5 rounded-lg space-y-4">
            <div className="flex justify-between items-start border-b border-neutral-300 pb-3">
              <div>
                <h3 className="text-sm font-bold text-black uppercase tracking-wider">
                  Doctor-Approved Prescription
                </h3>
                <div className="text-xs text-neutral-600 font-mono">Ref ID: {prescription.prescription_id}</div>
              </div>

              <select
                value={rxLang}
                onChange={(e) => handlePrescriptionLangToggle(e.target.value)}
                className="bg-white border border-neutral-300 rounded px-2 py-1 text-xs font-mono text-black"
              >
                {SUPPORTED_LANGUAGES.map((l, idx) => (
                  <option key={idx} value={l.code}>
                    {l.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Specialist Referral Banner */}
            {referral && (
              <div className="bg-white p-4 rounded border-2 border-black space-y-1 text-xs">
                <div className="font-bold text-black uppercase text-sm flex items-center justify-between border-b border-neutral-200 pb-1">
                  <span>📋 SPECIALIST REFERRAL ISSUED</span>
                  <span className="font-mono text-neutral-500 text-xs">{referral.specialty}</span>
                </div>
                <div className="pt-1"><strong>Referred To:</strong> {referral.specialty} Department</div>
                <div><strong>Doctor Notes:</strong> {referral.referral_notes}</div>
                <div><strong>Issued By:</strong> {referral.doctor_name}</div>
              </div>
            )}

            {/* Medications Table */}
            <div className="space-y-2">
              <div className="text-xs font-bold uppercase text-neutral-700">
                Prescribed Medications
              </div>
              <div className="space-y-2">
                {prescription.medications.map((med, idx) => (
                  <div key={idx} className="bg-white p-3 rounded border border-neutral-300 text-xs space-y-1 text-black">
                    <div className="flex justify-between font-bold text-black">
                      <span>{med.name}</span>
                      <span className="font-mono text-neutral-700">{med.dosage}</span>
                    </div>
                    <div className="grid grid-cols-2 text-neutral-700 text-[11px]">
                      <div><strong>Frequency:</strong> {med.frequency}</div>
                      <div><strong>Duration:</strong> {med.duration}</div>
                    </div>
                    <div className="text-neutral-600 text-[11px]"><strong>Instructions:</strong> {med.instructions}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* Doctor Appointment Details or Booking Button */}
            {!appointment ? (
              <div className="bg-white p-3 rounded border border-neutral-300 flex justify-between items-center text-xs">
                <div>
                  <div className="font-bold text-black">Book Follow-up Doctor Appointment</div>
                  <div className="text-neutral-600 text-[11px]">Optionally reserve a doctor consultation slot.</div>
                </div>
                <button
                  disabled={bookingLoading}
                  onClick={handleBookOptionalAppointment}
                  className="px-3 py-1.5 bg-black text-white font-bold rounded text-xs hover:bg-neutral-800"
                >
                  {bookingLoading ? 'Booking...' : 'Book Slot'}
                </button>
              </div>
            ) : (
              <div className="bg-white p-3.5 rounded border-2 border-neutral-800 text-xs font-mono space-y-1">
                <div className="font-bold text-black text-sm border-b border-neutral-200 pb-1">
                  📅 APPOINTMENT SCHEDULED
                </div>
                <div><strong>Slot Time:</strong> {appointment.slot_time}</div>
                <div><strong>Location:</strong> {appointment.clinic_location}</div>
                <div><strong>Ref ID:</strong> {appointment.appointment_id}</div>
              </div>
            )}
          </div>
        )}

        {/* CHAT MESSAGES */}
        <div className="bg-neutral-50 rounded-lg border border-neutral-300 p-4 flex-1 flex flex-col justify-between min-h-[360px] max-h-[460px]">
          <div className="overflow-y-auto space-y-3 pr-2 flex-1">
            {messages.map((msg, index) => (
              <div
                key={index}
                className={`flex ${msg.sender === 'patient' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={`max-w-[85%] p-3 rounded-lg text-xs leading-relaxed ${
                    msg.sender === 'patient'
                      ? 'bg-black text-white'
                      : 'bg-white text-black border border-neutral-300 shadow-sm'
                  }`}
                >
                  {msg.text}
                  {msg.sender === 'ai' && (
                    <button
                      onClick={() => speakAiResponse(msg.text)}
                      className="ml-2 text-neutral-500 hover:text-black font-mono text-[10px]"
                    >
                      [Listen]
                    </button>
                  )}
                </div>
              </div>
            ))}

            {loading && (
              <div className="text-xs text-neutral-500 font-mono">AI processing...</div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {/* INPUT FORM */}
          {triageStatus !== 'URGENT' && triageStatus !== 'UNCERTAIN' && (
            <form onSubmit={handleFormSubmit} className="mt-3 flex items-center space-x-2">
              <input
                type="text"
                value={inputMsg}
                onChange={(e) => setInputMsg(e.target.value)}
                placeholder="Type or speak symptoms..."
                disabled={loading || (isComplete && !prescription)}
                className="flex-1 bg-white border border-neutral-300 rounded px-3 py-2 text-xs text-black placeholder-neutral-400 focus:outline-none focus:border-black disabled:opacity-50"
              />

              <button
                type="submit"
                disabled={loading || !inputMsg.trim() || (isComplete && !prescription)}
                className="px-4 py-2 bg-black text-white font-bold rounded text-xs hover:bg-neutral-800 disabled:opacity-50"
              >
                Send
              </button>
            </form>
          )}
        </div>
      </main>
    </div>
  );
}
