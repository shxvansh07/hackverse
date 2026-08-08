import os
import json
import httpx
import random
from typing import List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")

class AIService:
    @classmethod
    def generate_natural_response(
        cls,
        patient_message: str,
        history: List[Dict[str, str]],
        lang: str = "hi",
        known_facts: Dict[str, Any] = None,
        missing_info: List[str] = None,
        previous_ai_texts: List[str] = None
    ) -> str:
        """
        Generates natural conversational AI responses with ZERO restrictions.
        Can talk about health, general topics, trivia, or anything the user wants.
        """
        if known_facts is None:
            known_facts = {}
        if missing_info is None:
            missing_info = []
        if previous_ai_texts is None:
            previous_ai_texts = []
        symptoms_known = ", ".join(known_facts.get("symptoms", [])) or "Not specified yet"
        duration_known = known_facts.get("duration", "") or "Not specified yet"
        severity_known = known_facts.get("severity", "") or "Not specified yet"

        system_prompt = (
            "You are a professional, warm, and empathetic AI Clinical Intake Assistant conducting a medical intake consultation for a physician.\n\n"
            "CRITICAL BOUNDARIES & INSTRUCTIONS:\n"
            "1. Focus STRICTLY on medical intake, patient symptoms, medical history, and health concerns.\n"
            "2. If the user asks non-medical off-topic questions (e.g., math calculations like 'whats 5*4', general trivia like 'who is the president of india', politics, sports, coding, etc.), DO NOT perform the calculation or answer the trivia. Politely state that you are a clinical assistant focused on their health consultation, and ask them about their symptoms or health concerns.\n"
            "3. Converse naturally, empathetically, and concisely (1-2 brief sentences).\n"
            f"4. ALREADY KNOWN FACTS: Symptoms = [{symptoms_known}], Duration = [{duration_known}], Severity = [{severity_known}]. Do NOT repeat questions for facts already collected.\n"
            f"5. You MUST respond in the native script of language code '{lang}'."
        )

        # 1. Groq API (High Speed LPU Llama-3.3 70B)
        if GROQ_API_KEY:
            try:
                messages = [{"role": "system", "content": system_prompt}]
                for h in history[-6:]:
                    messages.append({
                        "role": "user" if h.get("sender") == "patient" else "assistant",
                        "content": h.get("text", "")
                    })
                messages.append({"role": "user", "content": patient_message})

                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
                payload = {"model": GROQ_MODEL, "messages": messages, "temperature": 0.3, "max_tokens": 150}
                with httpx.Client(timeout=8.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        reply = resp.json()["choices"][0]["message"]["content"].strip()
                        if reply and reply not in previous_ai_texts:
                            return reply
            except Exception as e:
                print(f"Groq API Exception: {e}")

        # 2. Google Gemini API
        if GEMINI_API_KEY:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
                
                conversation_text = system_prompt + "\n\nConversation History:\n"
                for h in history[-6:]:
                    role = "User" if h.get("sender") == "patient" else "AI"
                    conversation_text += f"{role}: {h.get('text', '')}\n"
                conversation_text += f"User: {patient_message}\nAI:"

                payload = {
                    "contents": [{"parts": [{"text": conversation_text}]}],
                    "generationConfig": {"temperature": 0.3, "maxOutputTokens": 150}
                }
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        reply = data['candidates'][0]['content']['parts'][0]['text'].strip()
                        if reply and reply not in previous_ai_texts:
                            return reply
            except Exception as e:
                print(f"Gemini API Exception: {e}")

        # 3. OpenAI API
        if OPENAI_API_KEY:
            try:
                messages = [{"role": "system", "content": system_prompt}]
                for h in history[-6:]:
                    messages.append({
                        "role": "user" if h.get("sender") == "patient" else "assistant",
                        "content": h.get("text", "")
                    })
                messages.append({"role": "user", "content": patient_message})

                url = "https://api.openai.com/v1/chat/completions"
                headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
                payload = {"model": OPENAI_MODEL, "messages": messages, "temperature": 0.3, "max_tokens": 150}
                with httpx.Client(timeout=8.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        reply = resp.json()["choices"][0]["message"]["content"].strip()
                        if reply and reply not in previous_ai_texts:
                            return reply
            except Exception as e:
                print(f"OpenAI API Exception: {e}")

        # 4. Human-like Dynamic Conversational Fallback Engine
        return cls._get_dynamic_conversational_fallback(patient_message, lang, previous_ai_texts)

    @classmethod
    def _get_dynamic_conversational_fallback(cls, msg: str, lang: str, previous_ai_texts: List[str]) -> str:
        """
        Provides friendly, professional, clinical intake fallbacks
        that politely redirect non-medical queries back to health intake.
        """
        msg_lower = msg.lower()

        # Natural Human Conversational Pools for each language
        pools = {
            "hi": {
                "greetings": [
                    "नमस्ते! मैं आपका क्लिनिकल असिस्टेंट हूँ। आज आपको क्या स्वास्थ्य संबंधी समस्या हो रही है?",
                    "नमस्ते! बताइए आज आपको क्या लक्षण या तकलीफ महसूस हो रही है?"
                ],
                "symptoms": [
                    "समझ गया। यह तकलीफ आपको कब से हो रही है?",
                    "क्या इसके अलावा कोई अन्य लक्षण या परेशानी महसूस हो रही है?"
                ],
                "general": [
                    "मैं एक क्लिनिकल मेडिकल असिस्टेंट हूँ और केवल आपके स्वास्थ्य परामर्श में सहायता कर सकता हूँ। कृपया अपने लक्षणों के बारे में बताएं।",
                    "आइए आपके स्वास्थ्य परामर्श पर ध्यान दें। आज आपको क्या शारीरिक समस्या महसूस हो रही है?"
                ]
            },
            "kn": {
                "greetings": [
                    "ನಮಸ್ಕಾರ! ನಾನು ನಿಮ್ಮ ಕ್ಲಿನಿಕಲ್ ಸಹಾಯಕ. ಇಂದು ನಿಮಗಿರುವ ಆರೋಗ್ಯ ಸಮಸ್ಯೆ ಏನು?",
                    "ನಮಸ್ಕಾರ! ನಿಮ್ಮ ದೇಹದಲ್ಲಿ ಏನು ತೊಂದರೆ ಕಾಣಿಸಿಕೊಳ್ಳುತ್ತಿದೆ?"
                ],
                "symptoms": [
                    "ಅರ್ಥವಾಯಿತು. ಈ ತೊಂದರೆ ಎಷ್ಟು ದಿನದಿಂದ ಇದೆ?",
                    "ಇದರ ಹೊರತಾಗಿ ಬೇರೆ ಯಾವುದೇ ರೋಗಲಕ್ಷಣಗಳಿವೆಯೇ?"
                ],
                "general": [
                    "ನಾನು ನಿಮ್ಮ ವೈದ್ಯಕೀಯ ಆರೋಗ್ಯ ಸಹಾಯಕರಾಗಿದ್ದು, ನಿಮ್ಮ ಆರೋಗ್ಯ ವಿಷಯಗಳಿಗೆ ಮಾತ್ರ ಉತ್ತರಿಸಬಲ್ಲೆ. ದಯವಿಟ್ಟು ನಿಮ್ಮ ರೋಗಲಕ್ಷಣಗಳನ್ನು ತಿಳಿಸಿ.",
                    "ನಿಮ್ಮ ಆರೋಗ್ಯ ಸಮಾಲೋಚನೆಯತ್ತ ಗಮನ ಹರಿಸೋಣ. ಇವತ್ತು ನಿಮಗೇನು ತೊಂದರೆಯಾಗಿದೆ?"
                ]
            },
            "te": {
                "greetings": [
                    "నమస్కారం! నేను మీ క్లినికల్ అసిస్టెంట్‌ని. ఈ రోజు మీ ఆరోగ్య సమస్య ఏమిటి?",
                    "నమస్కారం! మీకు ఎలాంటి లక్షణాలు కనిపిస్తున్నాయి?"
                ],
                "symptoms": [
                    "అర్థమైంది. ఈ సమస్య ఎన్ని రోజుల నుండి ఉంది?",
                    "దీనితో పాటు ఇంకేమైనా ఇబ్బంది ఉందా?"
                ],
                "general": [
                    "నేను క్లినికల్ మెడికಲ್ అసిస్టెంట్‌ని, మీ ఆరోగ్య విషయాలపై మాత్రమే సహాయం చేయగలను. దయచేసి మీ ఆరోగ్య సమస్యల గురించి చెప్పండి.",
                    "మీ ఆరోగ్య సమస్యలపై దృష్టి పెడదాం. ఈ రోజు మీ శరీరం ఎలా అనిపిస్తోంది?"
                ]
            },
            "ta": {
                "greetings": [
                    "வணக்கம்! நான் உங்கள் மருத்துவ உதவியாளர். இன்று உங்களுக்கு என்ன சுகாதார பிரச்சனை?",
                    "வணக்கம்! உங்களுக்கு என்ன அறிகுறிகள் உள்ளன?"
                ],
                "symptoms": [
                    "புரிந்தது. இந்த பிரச்சனை எத்தனை நாட்களாக இருக்கிறது?",
                    "இது தவிர வேறு ஏதேனும் அறிகுறிகள் உள்ளதா?"
                ],
                "general": [
                    "நான் உங்கள் மருத்துவ உதவியாளர், உங்கள் உடல்நலம் தொடர்பான விஷயங்களுக்கு மட்டுமே உதவ முடியும். தயவுசெய்து உங்கள் அறிகுறிகளைக் கூறுங்கள்.",
                    "உங்கள் சுகாதார ஆலோசனையில் கவனம் செலுத்துவோம். உங்களுக்கு என்ன பிரச்சனை?"
                ]
            },
            "en": {
                "greetings": [
                    "Hello! Welcome to our clinic. What symptoms or health concerns are you experiencing today?",
                    "Hi there! How can I assist you with your health consultation today?"
                ],
                "symptoms": [
                    "Understood. How long have you been experiencing these symptoms?",
                    "Thank you for sharing. Are you noticing any other symptoms alongside this?"
                ],
                "general": [
                    "I am a medical clinical assistant focused strictly on your health consultation. Please tell me about any symptoms or health concerns you have.",
                    "Let's focus on your medical consultation today. What physical symptoms or health issues can I help evaluate for the doctor?"
                ]
            }
        }

        lang_pool = pools.get(lang, pools["en"])
        
        # Determine category based on message content
        if any(w in msg_lower for w in ["hi", "hello", "hey", "namaste", "namaskara", "नमस्ते", "ಹಲೋ"]):
            options = lang_pool["greetings"]
        elif any(w in msg_lower for w in ["fever", "pain", "dard", "bukhar", "headache", "sick", "unwell", "ತೊಂದರೆ", "ಬಾದೆ", "दर्द", "fall", "injury"]):
            options = lang_pool["symptoms"]
        else:
            options = lang_pool["general"]

        # Pick an option that hasn't been used yet
        available = [opt for opt in options if opt not in previous_ai_texts]
        if available:
            return random.choice(available)
        
        return random.choice(options)"
                ],
                "symptoms": [
                    "ஐயோ, இது கஷ்டமா தான் இருக்கும். இது எத்தனை நாளா இருக்கு சொல்லுங்க?",
                    "ஓ சரி, புரிஞ்சது. இதோட வேற ஏதாச்சும் பிரச்சனை இருக்கா?",
                    "ஒன்னும் பயப்படாதீங்க. வேற என்ன சொல்ல விரும்புறீங்க?"
                ],
                "general": [
                    "ஆமா கண்டிப்பா! நீங்க சொல்றது புரியுது.",
                    "வாஹ், நல்ல விஷயம்! அப்புறம் என்ன விசேஷம்?",
                    "கண்டிப்பா, நீங்க எதை பத்தி வேணா என்கிட்ட பேசலாம்!"
                ]
            },
            "en": {
                "greetings": [
                    "Hey there! How are you doing today? What's on your mind?",
                    "Hello! Always great to chat with you. How can I help?",
                    "Hi! Hope you're having a good day so far. Tell me what's up!"
                ],
                "symptoms": [
                    "Oh no, that sounds uncomfortable! How long has that been bothering you?",
                    "Got it, I hear you. Is there anything else you're feeling alongside this?",
                    "Don't worry at all, I've got your back! Tell me more about what's going on."
                ],
                "general": [
                    "Totally agree with you! Tell me more about that.",
                    "Haha, that's awesome! Feel free to ask or share whatever you like.",
                    "That's so interesting! I'm here for whatever you want to chat about."
                ]
            }
        }

        lang_pool = pools.get(lang, pools["en"])
        
        # Determine category based on message content
        if any(w in msg_lower for w in ["hi", "hello", "hey", "namaste", "namaskara", "नमस्ते", "ಹಲೋ"]):
            options = lang_pool["greetings"]
        elif any(w in msg_lower for w in ["fever", "pain", "dard", "bukhar", "headache", "sick", "unwell", "ತೊಂದರೆ", "ಬಾದೆ", "दर्द"]):
            options = lang_pool["symptoms"]
        else:
            options = lang_pool["general"]

        # Pick an option that hasn't been used yet
        available = [opt for opt in options if opt not in previous_ai_texts]
        if available:
            return random.choice(available)
        
        return random.choice(options)
