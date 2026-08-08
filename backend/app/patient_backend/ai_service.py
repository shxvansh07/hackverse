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
            "You are a dedicated AI Clinical Assistant conducting a medical intake for a doctor.\n\n"
            "STRICT CLINICAL INSTRUCTIONS:\n"
            "1. Focus ONLY on patient health, clinical symptoms, and medical intake.\n"
            "2. If the user asks non-medical questions (e.g. math calculations like 5*4, general knowledge/trivia like 'who is the president', sports, politics, etc.), DO NOT answer the non-medical question. Politely decline and redirect the patient back to describing their health symptoms or medical concerns.\n"
            "3. Converse naturally, empathetically, and professionally like a real clinical intake assistant.\n"
            "4. Listen attentively to symptoms and ask relevant follow-up questions if useful details are missing.\n"
            f"5. ALREADY KNOWN FACTS: Symptoms = [{symptoms_known}], Duration = [{duration_known}], Severity = [{severity_known}]. Do NOT repeat questions for facts already mentioned.\n"
            "6. Keep your responses brief (1-2 sentences) so it reads smoothly and speaks aloud nicely.\n"
            f"7. You MUST respond natively in the language corresponding to language code '{lang}'."
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
                payload = {"model": GROQ_MODEL, "messages": messages, "temperature": 0.5, "max_tokens": 120}
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
                    "generationConfig": {"temperature": 0.5, "maxOutputTokens": 120}
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
                payload = {"model": OPENAI_MODEL, "messages": messages, "temperature": 0.5, "max_tokens": 120}
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
        Provides friendly, varied, natural human conversational fallbacks
        instead of repeating fixed template questions.
        """
        msg_lower = msg.lower()

        # Natural Human Conversational Pools for each language
        pools = {
            "hi": {
                "greetings": [
                    "नमस्ते! मैं आपका क्लिनिकल असिस्टेंट हूँ। आज आपको क्या स्वास्थ्य समस्या महसूस हो रही है?",
                    "नमस्ते! कृपया अपनी सेहत से जुड़े लक्षण या समस्या बताइए。"
                ],
                "symptoms": [
                    "यह तकलीफ कब से हो रही है आपको? क्या इसके अलावा कोई और लक्षण भी है?",
                    "मैं समझ रहा हूँ। क्या आपको इसके साथ बुखार या दर्द भी महसूस हो रहा है?"
                ],
                "general": [
                    "मैं आपका स्वास्थ्य सहायक हूँ और केवल चिकित्सीय प्रश्नों में मदद कर सकता हूँ। कृपया अपने स्वास्थ्य लक्षणों के बारे में बताएं।",
                    "आइए आपकी सेहत पर ध्यान दें। क्या आपको आज शरीर में कोई दर्द, बुखार या अन्य समस्या है?"
                ]
            },
            "kn": {
                "greetings": [
                    "ನಮಸ್ಕಾರ! ನಾನು ನಿಮ್ಮ ಕ್ಲಿನಿಿಕಲ್ ಸಹಾಯಕ. ನಿಮ್ಮ ಆರೋಗ್ಯದ ಸಮಸ್ಯೆಯನ್ನು ತಿಳಿಸಿ.",
                    "ಹಲೋ! ಇಂದು ನಿಮಗೆ ಯಾವ ಆರೋಗ್ಯ ತೊಂದರೆ ಇದೆ?"
                ],
                "symptoms": [
                    "ಇದು ಎಷ್ಟು ದಿನದಿಂದ ಇದೆ ಹೇಳಿ? ಬೇರೆ ತೊಂದರೆ ಇದೆಯಾ?",
                    "ಅರ್ಥ ಆಯ್ತು. ಇದರ ಜೊತೆಗೆ ಜ್ವರ ಅಥವಾ ನೋವು ಇದೆಯಾ?"
                ],
                "general": [
                    "ನಾನು ನಿಮ್ಮ ಆರೋಗ್ಯ ಸಹಾಯಕ, ದಯವಿಟ್ಟು ನಿಮ್ಮ ಆರೋಗ್ಯದ ಬಗ್ಗೆ ಮಾತ್ರ ಮಾತನಾಡಿ. ಯಾವ ತೊಂದರೆ ಇದೆ ತಿಳಿಸಿ?",
                    "ದಯವಿಟ್ಟು ನಿಮ್ಮ ಆರೋಗ್ಯದ ಲಕ್ಷಣಗಳನ್ನು ತಿಳಿಸಿ. ನಿಮಗೆ ಇಂದು ಹೇಗೆ ಅನ್ನಿಸುತ್ತಿದೆ?"
                ]
            },
            "te": {
                "greetings": [
                    "హలో! ఎలా ఉన్నారు? నేను మీకు ఎలా సహాయపడగలను?",
                    "నమస్కారం! చెప్పండి విశేషాలు ఏంటి?",
                    "హాయ్! మీతో మాట్లాడటం చాలా సంతోషంగా ఉంది."
                ],
                "symptoms": [
                    "అయ్యో, ఇది కొంచెం ఇబ్బందిగానే ఉంటుంది. ఇది ఎన్ని రోజుల నుండి ఉంది చెప్పండి?",
                    "ఓహో అర్థమైంది. దీంతో పాటు ఇంకేమైనా ఇబ్బంది ఉందా?",
                    "ఏమీ కంగారు పడకండి. ఇంకా ఏమైనా చెప్పాలనుకుంటున్నారా?"
                ],
                "general": [
                    "అవును ఖచ్చితంగా! మీరు చెప్పింది నాకర్థమైంది.",
                    "వావ్, ఇది చాలా బాగుంది! ఇంకా చెప్పండి?",
                    "ఖచ్చితంగా, మీరు ఏ విషయం గురించైనా నాతో మాట్లాడవచ్చు!"
                ]
            },
            "ta": {
                "greetings": [
                    "ஹலோ! எப்படி இருக்கீங்க? நான் உங்களுக்கு எப்படி உதவட்டும்?",
                    "வணக்கம்! சொல்லுங்க, என்ன விசேஷம்?",
                    "ஹாய்! உங்ககிட்ட பேசுறதுல ரொம்ப சந்தோஷம்."
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
                    "I am a clinical intake assistant focused on your health. Please tell me about any medical symptoms or health concerns you have today.",
                    "Let's focus on your medical consultation. Are you experiencing any pain, fever, or health discomfort right now?"
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
