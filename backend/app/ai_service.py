import os
import json
import httpx
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
    @staticmethod
    def generate_natural_response(
        patient_message: str,
        history: List[Dict[str, str]],
        lang: str = "hi",
        known_facts: Dict[str, Any] = {},
        missing_info: List[str] = [],
        previous_ai_texts: List[str] = []
    ) -> str:
        """
        Generates natural conversational clinical intake responses.
        Prioritizes Google Gemini API Key if configured.
        """
        symptoms_known = ", ".join(known_facts.get("symptoms", [])) or "Not specified yet"
        duration_known = known_facts.get("duration", "") or "Not specified yet"
        severity_known = known_facts.get("severity", "") or "Not specified yet"

        system_prompt = (
            "You are a smart, caring clinical intake assistant speaking with a patient.\n\n"
            "CRITICAL MEDICAL GUARDRAILS:\n"
            "1. You are strictly a clinical assistant. You are forbidden to answer questions unrelated to health, medicine, symptoms, or clinical care.\n"
            "2. If the user asks off-topic questions (e.g. math equations, programming, history, translations, writing code, general knowledge), you MUST refuse politely and ask them to describe their symptoms.\n"
            "   Refusal English Template: 'I can only assist with health-related questions. Please tell me about your symptoms or medical concern.'\n"
            "   Refusal Hindi Template: 'मैं केवल स्वास्थ्य संबंधी सवालों के जवाब दे सकता हूँ। कृपया मुझे अपने लक्षणों या स्वास्थ्य संबंधी चिंताओं के बारे में बताएं।'\n\n"
            "CONVERSATIONAL GUIDELINES:\n"
            "1. Behave naturally and converse empathetically. Answer patient's health questions or clarify their medical concerns.\n"
            f"2. ALREADY KNOWN FACTS: Symptoms = [{symptoms_known}], Duration = [{duration_known}], Severity = [{severity_known}]. Do NOT ask for information already provided.\n"
            "3. Keep your response very brief (1-2 sentences) so it can be spoken aloud easily. Ask one relevant follow-up question if clinical details are missing.\n"
            f"4. You MUST respond natively in the language matching this language code: '{lang}'. For example, if lang='kn', respond in Kannada script. If lang='ta', respond in Tamil script."
        )

        # 1. Google Gemini API
        if GEMINI_API_KEY:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
                
                conversation_text = system_prompt + "\n\nConversation History:\n"
                for h in history[-4:]:
                    role = "Patient" if h.get("sender") == "patient" else "AI"
                    conversation_text += f"{role}: {h.get('text', '')}\n"
                conversation_text += f"Patient: {patient_message}\nAI:"

                payload = {
                    "contents": [{"parts": [{"text": conversation_text}]}],
                    "generationConfig": {"temperature": 0.2, "maxOutputTokens": 120}
                }
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        reply = data['candidates'][0]['content']['parts'][0]['text'].strip()
                        if reply and reply not in previous_ai_texts:
                            return reply
                    else:
                        print(f"Gemini API Error {resp.status_code}: {resp.text}")
            except Exception as e:
                print(f"Gemini API Exception: {e}")

        # 2. Groq API
        if GROQ_API_KEY:
            try:
                messages = [{"role": "system", "content": system_prompt}]
                for h in history[-4:]:
                    messages.append({
                        "role": "user" if h.get("sender") == "patient" else "assistant",
                        "content": h.get("text", "")
                    })
                messages.append({"role": "user", "content": patient_message})

                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
                payload = {"model": GROQ_MODEL, "messages": messages, "temperature": 0.2, "max_tokens": 120}
                with httpx.Client(timeout=8.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        reply = resp.json()["choices"][0]["message"]["content"].strip()
                        if reply and reply not in previous_ai_texts:
                            return reply
            except Exception as e:
                print(f"Groq API Exception: {e}")

        # 3. OpenAI API
        if OPENAI_API_KEY:
            try:
                messages = [{"role": "system", "content": system_prompt}]
                for h in history[-4:]:
                    messages.append({
                        "role": "user" if h.get("sender") == "patient" else "assistant",
                        "content": h.get("text", "")
                    })
                messages.append({"role": "user", "content": patient_message})

                url = "https://api.openai.com/v1/chat/completions"
                headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
                payload = {"model": OPENAI_MODEL, "messages": messages, "temperature": 0.2, "max_tokens": 120}
                with httpx.Client(timeout=8.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        reply = resp.json()["choices"][0]["message"]["content"].strip()
                        if reply and reply not in previous_ai_texts:
                            return reply
            except Exception as e:
                print(f"OpenAI API Exception: {e}")

        # 4. DeepSeek API
        if DEEPSEEK_API_KEY:
            try:
                messages = [{"role": "system", "content": system_prompt}]
                for h in history[-4:]:
                    messages.append({
                        "role": "user" if h.get("sender") == "patient" else "assistant",
                        "content": h.get("text", "")
                    })
                messages.append({"role": "user", "content": patient_message})

                url = "https://api.deepseek.com/chat/completions"
                headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
                payload = {"model": DEEPSEEK_MODEL, "messages": messages, "temperature": 0.2, "max_tokens": 120}
                with httpx.Client(timeout=8.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        reply = resp.json()["choices"][0]["message"]["content"].strip()
                        if reply and reply not in previous_ai_texts:
                            return reply
            except Exception as e:
                print(f"DeepSeek API Exception: {e}")

        # 5. NVIDIA API
        if NVIDIA_API_KEY:
            try:
                messages = [{"role": "system", "content": system_prompt}]
                for h in history[-4:]:
                    messages.append({
                        "role": "user" if h.get("sender") == "patient" else "assistant",
                        "content": h.get("text", "")
                    })
                messages.append({"role": "user", "content": patient_message})

                url = "https://integrate.api.nvidia.com/v1/chat/completions"
                headers = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Content-Type": "application/json"}
                payload = {"model": NVIDIA_MODEL, "messages": messages, "temperature": 0.2, "max_tokens": 120}
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        reply = resp.json()["choices"][0]["message"]["content"].strip()
                        if reply and reply not in previous_ai_texts:
                            return reply
            except Exception as e:
                print(f"NVIDIA API Exception: {e}")

        # 6. Fallback Rule Engine
        fallback_maps = {
            "hi": {
                "q_symptoms": "कृपया बताएं कि आपको क्या मुख्य तकलीफ या लक्षण महसूस हो रहे हैं (जैसे बुखार या सिरदर्द)?",
                "q_duration": "आपकी समस्या दर्ज कर ली गई है। यह तकलीफ आपको कितने दिनों या घंटों से हो रही है?",
                "q_associated": "आपकी जानकारी दर्ज कर ली गई है। क्या आपको इसके अलावा कोई और लक्षण भी महसूस हो रहे हैं?",
                "q_history": "क्या आपको किसी दवा से एलर्जी है, या कोई पुरानी बीमारी (जैसे बीपी या शुगर) है?",
                "q_default": "क्या आपकी कोई नियमित दवाएं चल रही हैं या कोई अन्य स्वास्थ्य संबंधी बात बताना चाहते हैं?"
            },
            "kn": {
                "q_symptoms": "ದಯವಿಟ್ಟು ನಿಮ್ಮ ಮುಖ್ಯ ರೋಗಲಕ್ಷಣಗಳನ್ನು ತಿಳಿಸಿ (ಉದಾಹರಣೆಗೆ ಜ್ವರ, ತಲೆನೋವು, ಅಥವಾ ಕೆಮ್ಮು)?",
                "q_duration": "ಧನ್ಯವಾದಗಳು. ಈ ಲಕ್ಷಣಗಳು ಎಷ್ಟು ದಿನಗಳಿಂದ ಅಥವಾ ಸಮಯದಿಂದ ಇವೆ?",
                "q_associated": "ಧನ್ಯವಾದಗಳು. ಇದರ ಜೊತೆಗೆ ನಿಮಗೆ ಬೇರೆ ಏನಾದರೂ ಲಕ್ಷಣಗಳಿವೆಯೇ?",
                "q_history": "ನಿಮಗೆ ಯಾವುದೇ ಔಷಧಿಗಳ ಅಲರ್ಜಿ ಇದೆಯೇ ಅಥವಾ ಬೇರೆ ಯಾವುದೇ ಆರೋಗ್ಯ ಸಮಸ್ಯೆಗಳಿವೆಯೇ?",
                "q_default": "ನೀವು ಪ್ರಸ್ತುತ ಬೇರೆ ಯಾವುದೇ ಔಷಧಿಗಳನ್ನು ತೆಗೆದುಕೊಳ್ಳುತ್ತಿದ್ದೀರಾ?"
            },
            "te": {
                "q_symptoms": "దయచేసి మీ ప్రధాన లక్షణాలను తెలుపగలరా (ఉదాహరణకు జ్వరం, తలనొప్పి, లేదా దగ్గు)?",
                "q_duration": "ధన్యవాదాలు. ఈ లక్షణాలు ఎన్ని రోజుల నుండి ఉన్నాయి?",
                "q_associated": "ధన్యవాదాలు. మీకు దీనికి తోడుగా ఇంకేమైనా ఇతర లక్షణాలు ఉన్నాయా?",
                "q_history": "మీకు మందులతో ఏమైనా అలర్జీలు ఉన్నాయా లేదా గతంలో ఇతర ఆరోగ్య సమస్యలు ఉన్నాయా?",
                "q_default": "మీరు ప్రస్తుతం ఏమైనా మందులు వాడుతున్నారా?"
            },
            "ta": {
                "q_symptoms": "உங்கள் முக்கிய அறிகுறிகளை குறிப்பிடவும் (எ.கா: காய்ச்சல், தலைவலி அல்லது இருமல்)?",
                "q_duration": "நன்றி. இந்த அறிகுறிகள் எத்தனை நாட்களாக இருக்கின்றன?",
                "q_associated": "நன்றி. உங்களுக்கு வேறு ஏதேனும் அறிகுறிகள் உள்ளதா?",
                "q_history": "உங்களுக்கு ஏதேனும் மருந்து ஒவ்வாமை அல்லது வேறு உடல்நலப் பிரச்சனைகள் உள்ளதா?",
                "q_default": "நீங்கள் தற்போது ஏதேனும் மருந்துகள் எடுத்துக் கொள்கிறீர்களா?"
            },
            "bn": {
                "q_symptoms": "অনুগ্রহ করে আপনার প্রধান লক্ষণগুলি বলুন (যেমন জ্বর, মাথাব্যথা, বা কাশি)?",
                "q_duration": "ধন্যবাদ। এই লক্ষণগুলি কতদিন ধরে আছে?",
                "q_associated": "ধন্যবাদ। আপনার কি অন্য কোনো লক্ষণ আছে?",
                "q_history": "আপনার কি কোনো ওষুধের অ্যালার্জি বা অন্য কোনো স্বাস্থ্য সমস্যা আছে?",
                "q_default": "আপনি কি বর্তমানে অন্য কোনো ওষুধ খাচ্ছেন?"
            },
            "mr": {
                "q_symptoms": "कृपया तुमची मुख्य लक्षणे सांगा (उदा. ताप, डोकेदुखी, किंवा खोकला)?",
                "q_duration": "धन्यवाद. ही लक्षणे तुम्हाला किती दिवसांपासून आहेत?",
                "q_associated": "धन्यवाद. तुम्हाला याव्यतिरिक्त आणखी काही लक्षणे आहेत का?",
                "q_history": "तुम्हाला कोणत्याही औषधाची ऍलर्जी आहे का, किंवा इतर कोणतेही आजार आहेत का?",
                "q_default": "तुम्ही सध्या कोणतीही औषधे घेत आहात का?"
            },
            "gu": {
                "q_symptoms": "કૃપા કરીને તમારા મુખ્ય લક્ષણો જણાવો (જેમ કે તાવ, માથાનો દુખાવો, અથવા ખાંસી)?",
                "q_duration": "આભાર. આ લક્ષણો તમને કેટલા સમયથી છે?",
                "q_associated": "આભાર. શું તમને અન્ય કોઈ લક્ષણો છે?",
                "q_history": "શું તમને કોઈ દવાની એલર્જી છે અથવા અન્ય કોઈ સ્વાસ્થ્ય સમસ્યા છે?",
                "q_default": "શું તમે હાલમાં કોઈ દવાઓ લઈ રહ્યા છો?"
            },
            "en": {
                "q_symptoms": "Could you please specify your main symptoms (e.g., fever, headache, or cough)?",
                "q_duration": "Thank you. How long have you been experiencing these symptoms?",
                "q_associated": "Thank you. Are you experiencing any accompanying symptoms?",
                "q_history": "Do you have any known medication allergies or existing medical conditions?",
                "q_default": "Are you currently taking any regular prescription medications?"
            }
        }

        lang_map = fallback_maps.get(lang, fallback_maps["en"])
        q_symptoms = lang_map["q_symptoms"]
        q_duration = lang_map["q_duration"]
        q_associated = lang_map["q_associated"]
        q_history = lang_map["q_history"]

        if not symptoms_known or symptoms_known == "Not specified yet" or symptoms_known == "Feeling unwell (तबीयत खराब)":
            if q_symptoms not in previous_ai_texts:
                return q_symptoms
        if not duration_known or duration_known == "Not specified yet":
            if q_duration not in previous_ai_texts:
                return q_duration
        if q_associated not in previous_ai_texts:
            return q_associated
        if q_history not in previous_ai_texts:
            return q_history
        return lang_map["q_default"]
