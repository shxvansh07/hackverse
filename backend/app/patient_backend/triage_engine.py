import re
from typing import Dict, Any, List, Tuple
from app.shared.models import TriageCase, ChatMessage, RiskState
from app.patient_backend.safety_engine import evaluate_safety_triage, recommend_specialty
from app.patient_backend.ai_service import AIService
from app.shared.database import db

# Negative confirmation words (Hindi & English)
NEGATIVE_WORDS = [
    "no", "nothing", "nope", "none", "that's all", "thats all", "no more", "no other", "nothing else",
    "nahi", "nahin", "nahi hai", "nahin hai", "नहीं", "नहीं है", "कुछ नहीं", "बस", "बस इतना", "और कुछ नहीं",
    "nahi nahi", "नहीं नहीं", "na", "ना"
]

DEVANAGARI_DIGITS_MAP = str.maketrans("०१२३४५६७८९", "0123456789")

class TriageEngine:
    @staticmethod
    def extract_structured_fields(patient_text: str, current_case: TriageCase) -> TriageCase:
        text_normalized = patient_text.translate(DEVANAGARI_DIGITS_MAP)
        text_lower = text_normalized.lower()

        # 1. Symptoms Extraction
        symptom_keywords = {
            "fever": ["fever", "bukhar", "बुखार", "temperature", "high temp", "feverish"],
            "body ache": ["body ache", "body pain", "badan dard", "अंगों में दर्द", "muscles ache"],
            "cough": ["cough", "khansi", "खांसी"],
            "cold/runny nose": ["cold", "runny nose", "sneeze", "chink", "जुकाम", "छींक"],
            "sore throat": ["sore throat", "gala dard", "गले में खराश"],
            "headache": ["headache", "head pain", "sir dard", "sar dard", "सिरदर्द"],
            "acidity/heartburn": ["acidity", "heartburn", "gas", "pet me jaln", "पेट में जलन", "biryani"],
            "stomach ache": ["stomach ache", "stomach pain", "pet me dard", "पेट दर्द", "pet kharab"],
            "diarrhea": ["diarrhea", "loose motion", "dast", "दस्त"],
            "vomiting/nausea": ["vomiting", "nausea", "ulti", "उल्टी", "जी मिचलाना"],
            "dizziness": ["dizziness", "chakkan", "chakkar", "चक्कर"],
            "chest pain": ["chest pain", "seene me dard", "छाती में दर्द"],
            "breathing difficulty": ["shortness of breath", "breathing difficulty", "saans lene me dikkat", "सांस फूलना"],
            "feeling unwell": ["tabiyat kharab", "तबीयत खराब", "तबीअत ख़राब", "unwell", "sick", "not feeling well", "bimar", "बीमार"]
        }

        found_symptoms = list(current_case.symptoms)
        for sym, kw_list in symptom_keywords.items():
            if any(kw in text_lower for kw in kw_list):
                if sym not in found_symptoms:
                    found_symptoms.append(sym)
        
        # If symptoms list is still empty, set generic symptom so intake doesn't stall
        if not found_symptoms and len(current_case.transcript) >= 1:
            found_symptoms.append("Clinical symptom consultation (स्वास्थ्य परामर्श)")

        current_case.symptoms = found_symptoms

        # 2. Smart Duration Extraction
        duration_patterns = [
            r'(\d+\s*(?:day|days|din|dino|dnon|dinon|hours|ghante|ghanta|week|weeks|hafte|month|months|saal|years|दिन|दिनों|घंटे|घंटों|हफ्ते|हफ़्तों|महीने|साल))',
            r'(since\s+(?:yesterday|morning|today|last night|2 days|3 days|4 days))',
            r'((?:kal|aaj|subah|कल|आज|सुबह)\s*(?:se|से|night|raat)?)',
            r'((?:one|two|three|four|five|six|seven|ek|do|teen|char|paanch|एक|दो|तीन|चार|पांच)\s*(?:day|days|din|dinon|दिन|दिनों))',
            r'(\d+\s*(?:से|se))'
        ]
        for pattern in duration_patterns:
            match = re.search(pattern, text_lower)
            if match and not current_case.duration:
                current_case.duration = match.group(1)
                break

        if not current_case.duration:
            if any(w in text_lower for w in ["din", "days", "day", "ghante", "hours", "दिन", "घंटे", "se", "से", "aaj", "कल"]):
                current_case.duration = patient_text.strip()
            else:
                current_case.duration = "Recent onset"

        # 3. Severity Extraction
        if any(w in text_lower for w in ["severe", "bahut tez", "गंभीर", "बहुत तेज"]):
            current_case.severity = "Severe"
        elif any(w in text_lower for w in ["moderate", "thoda tez", "मध्यम"]):
            current_case.severity = "Moderate"
        elif any(w in text_lower for w in ["mild", "halka", "हल्का"]):
            current_case.severity = "Mild"

        # 4. Medical History
        if any(w in text_lower for w in ["diabetes", "bp", "hypertension", "thyroid", "asthma", "sugar"]):
            if patient_text not in current_case.medical_history:
                current_case.medical_history.append(patient_text)

        # 5. Allergies
        if any(w in text_lower for w in ["allergy", "allergic", "reaction", "एलर्जी"]):
            if patient_text not in current_case.allergies:
                current_case.allergies.append(patient_text)

        current_case.missing_information = []
        return current_case

    @classmethod
    def is_negative_confirmation(cls, text: str) -> bool:
        text_lower = text.lower().strip()
        for word in NEGATIVE_WORDS:
            if word in text_lower:
                return True
        return False

    @classmethod
    def process_message(
        cls,
        current_case: TriageCase,
        patient_text: str,
        lang: str = "en"
    ) -> Tuple[TriageCase, str, bool, Any]:
        """
        Processes message naturally using Groq LLM without repeating questions.
        If patient says 'no' / 'nahi' / 'nahin', intake COMPLETES IMMEDIATELY.
        """
        sess = db.sessions.get(current_case.session_id)
        current_case.transcript.append(ChatMessage(sender="patient", text=patient_text))
        current_case = cls.extract_structured_fields(patient_text, current_case)

        risk_state, red_flags = evaluate_safety_triage(
            symptoms=current_case.symptoms,
            associated_symptoms=current_case.associated_symptoms,
            patient_text_raw=patient_text,
            allergies=current_case.allergies,
            missing_info=current_case.missing_information
        )
        current_case.triage_status = risk_state
        current_case.red_flags = red_flags

        # 1. Urgent Red Flag Handling — do NOT auto-book. Warn immediately and
        # ask the patient to give a final confirmation before an emergency
        # appointment is reserved.
        if risk_state == RiskState.URGENT:
            current_case.recommended_specialty = recommend_specialty(red_flags)

            ai_reply = (
                "⚠️ ध्यान दें: आपके बताए लक्षणों में तुरंत डॉक्टर देखभाल की आवश्यकता है। "
                f"अनुशंसित विशेषज्ञ: {current_case.recommended_specialty}। "
                "कृपया बिना देरी किए आपातकालीन विभाग या डॉक्टर से तुरंत संपर्क करें। "
                "कृपया नीचे पुष्टि करें ताकि हम आपके लिए एक आपातकालीन अपॉइंटमेंट आरक्षित कर सकें।"
                if lang == "hi" else
                "⚠️ Warning: The symptoms you entered indicate a high-risk condition. "
                f"Recommended specialist: {current_case.recommended_specialty}. "
                "Please proceed immediately to the nearest emergency room or contact a doctor without delay. "
                "Please confirm below so we can reserve an emergency appointment for you."
            )

            current_case.transcript.append(ChatMessage(sender="ai", text=ai_reply))
            current_case.summary_en = cls.build_english_summary(current_case)
            return current_case, ai_reply, True, None

        # 2. Uncertain / Ambiguous Cases: stop the conversation and point the
        # patient at an in-person specialist instead of auto-booking or
        # continuing to chat indefinitely.
        if risk_state == RiskState.UNCERTAIN:
            current_case.recommended_specialty = recommend_specialty(red_flags)

            ai_reply = (
                "आपके बताए लक्षणों को सही आकलन के लिए व्यक्तिगत जांच की आवश्यकता है। "
                f"कृपया {current_case.recommended_specialty} विशेषज्ञ के साथ व्यक्तिगत अपॉइंटमेंट बुक करें।"
                if lang == "hi" else
                "Your symptoms need an in-person evaluation for an accurate assessment. "
                f"Please book an in-person appointment with a {current_case.recommended_specialty} specialist."
            )

            current_case.transcript.append(ChatMessage(sender="ai", text=ai_reply))
            current_case.summary_en = cls.build_english_summary(current_case)
            return current_case, ai_reply, True, None

        # 3. IMMEDIATE COMPLETION CHECK (If patient says 'nahi' / 'no' at ANY step after turn 1)
        if len(current_case.transcript) >= 2 and cls.is_negative_confirmation(patient_text):
            ai_reply = (
                "धन्यवाद! आपकी सभी जानकारी दर्ज कर ली गई है और डॉक्टर की समीक्षा के लिए भेज दी गई है। समीक्षा के बाद आपका प्रिस्क्रिप्शन यहाँ दिखेगा।"
                if lang == "hi" else
                "Thank you! All your details have been recorded and submitted for doctor review. Your prescription will appear once reviewed."
            )
            current_case.transcript.append(ChatMessage(sender="ai", text=ai_reply))
            current_case.summary_en = cls.build_english_summary(current_case)
            return current_case, ai_reply, True, None

        # 4. Dynamic Natural Conversation using LLM (Groq Llama-3.3-70b)
        previous_ai_texts = [m.text for m in current_case.transcript if m.sender == "ai"]
        history_list = [{"sender": m.sender, "text": m.text} for m in current_case.transcript]
        known_facts = {
            "symptoms": current_case.symptoms,
            "duration": current_case.duration,
            "severity": current_case.severity,
            "history": current_case.medical_history,
            "allergies": current_case.allergies
        }

        ai_reply = AIService.generate_natural_response(
            patient_message=patient_text,
            history=history_list,
            lang=lang,
            known_facts=known_facts,
            missing_info=current_case.missing_information,
            previous_ai_texts=previous_ai_texts
        )

        current_case.transcript.append(ChatMessage(sender="ai", text=ai_reply))
        current_case.summary_en = cls.build_english_summary(current_case)
        return current_case, ai_reply, False, None

    @staticmethod
    def build_english_summary(case: TriageCase) -> str:
        symptoms_str = ", ".join(case.symptoms) if case.symptoms else "Unspecified symptoms"
        duration_str = case.duration if case.duration else "Not specified"
        severity_str = case.severity if case.severity else "Not specified"
        history_str = "; ".join(case.medical_history) if case.medical_history else "None reported"
        allergies_str = "; ".join(case.allergies) if case.allergies else "None reported"
        red_flags_str = ", ".join(case.red_flags) if case.red_flags else "None"

        return (
            f"Patient presents with {symptoms_str} (Severity: {severity_str}) lasting for {duration_str}. "
            f"Medical History: {history_str}. Allergies: {allergies_str}. Red Flags: {red_flags_str}."
        )
