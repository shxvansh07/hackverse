from typing import Dict, Any, List
from app.shared.models import Prescription, Medication

FREQUENCY_MAP = {
    "hi": {
        "Once daily": "दिन में एक बार (Once daily)",
        "Twice daily": "दिन में दो बार (Twice daily)",
        "Thrice daily": "दिन में तीन बार (Thrice daily)",
        "As needed": "आवश्यकतानुसार (As needed)"
    },
    "kn": {
        "Once daily": "ದಿನಕ್ಕೆ ಒಮ್ಮೆ (Once daily)",
        "Twice daily": "ದಿನಕ್ಕೆ ಎರಡು ಬಾರಿ (Twice daily)",
        "Thrice daily": "ದಿನಕ್ಕೆ ಮೂರು ಬಾರಿ (Thrice daily)",
        "As needed": "ಅಗತ್ಯವಿದ್ದಾಗ (As needed)"
    },
    "ta": {
        "Once daily": "நாளுக்கு ஒரு முறை (Once daily)",
        "Twice daily": "நாளுக்கு இரு முறை (Twice daily)",
        "Thrice daily": "நாளுக்கு மூன்று முறை (Thrice daily)",
        "As needed": "தேவைப்படும் போது (As needed)"
    },
    "te": {
        "Once daily": "రోజుకు ఒకసారి (Once daily)",
        "Twice daily": "రోజుకు రెండుసార్లు (Twice daily)",
        "Thrice daily": "రోజుకు మూడుసార్లు (Thrice daily)",
        "As needed": "అవసరమైనప్పుడు (As needed)"
    },
    "bn": {
        "Once daily": "দিনে একবার (Once daily)",
        "Twice daily": "দিনে দুইবার (Twice daily)",
        "Thrice daily": "দিনে তিনবার (Thrice daily)",
        "As needed": "প্রয়োজন অনুযায়ী (As needed)"
    },
    "mr": {
        "Once daily": "दिवसातून एकदा (Once daily)",
        "Twice daily": "दिवसातून दोनदा (Twice daily)",
        "Thrice daily": "दिवसातून तीनदा (Thrice daily)",
        "As needed": "गरजेनुसार (As needed)"
    },
    "gu": {
        "Once daily": "દિવસમાં એક વાર (Once daily)",
        "Twice daily": "દિવસમાં બે વાર (Twice daily)",
        "Thrice daily": "દિવસમાં ત્રણ વાર (Thrice daily)",
        "As needed": "જરૂરિયાત મુજબ (As needed)"
    }
}

INSTRUCTIONS_MAP = {
    "hi": {
        "Take with water after food": "खाना खाने के बाद पानी के साथ लें।",
        "Take 30 minutes before breakfast": "सुबह खाली पेट नाश्ते से 30 मिनट पहले लें।"
    },
    "kn": {
        "Take with water after food": "ಊಟದ ನಂತರ ನೀರಿನೊಂದಿಗೆ ತೆಗೆದುಕೊಳ್ಳಿ.",
        "Take 30 minutes before breakfast": "ತಿಂಡಿಗೆ 30 ನಿಮಿಷಗಳ ಮೊದಲು ತೆಗೆದುಕೊಳ್ಳಿ."
    },
    "ta": {
        "Take with water after food": "உணவுக்கு பின் தண்ணீருடன் எடுத்துக் கொள்ளவும்.",
        "Take 30 minutes before breakfast": "காலை உணவுக்கு 30 நிமிடங்களுக்கு முன் எடுத்துக் கொள்ளவும்."
    },
    "te": {
        "Take with water after food": "భోజనం తర్వాత నీటితో తీసుకోండి.",
        "Take 30 minutes before breakfast": "టిఫిన్‌కు 30 నిమిషాల ముందు తీసుకోండి."
    },
    "bn": {
        "Take with water after food": "খাবারের পর জলের সাথে খান।",
        "Take 30 minutes before breakfast": "প্রাতরাশের ৩০ মিনিট আগে খাবেন।"
    },
    "mr": {
        "Take with water after food": "जेवणानंतर पाण्यासोबत घ्या.",
        "Take 30 minutes before breakfast": "नाश्त्यापूर्वी ३० मिनिटे घ्या."
    },
    "gu": {
        "Take with water after food": "જમ્યા પછી પાણી સાથે લેવું.",
        "Take 30 minutes before breakfast": "નાસ્તાના ૩૦ મિનિટ પહેલાં લેવું."
    }
}

class TranslationEngine:
    @classmethod
    def get_translated_prescription(cls, prescription: Prescription, lang: str = "en") -> Dict[str, Any]:
        """
        Translates canonical doctor-approved prescription into requested language.
        Preserves original medication name and clinical dosages.
        """
        if lang not in FREQUENCY_MAP:
            return {
                "prescription_id": prescription.prescription_id,
                "language": "en",
                "status": prescription.status,
                "medications": [med.dict() for med in prescription.medications],
                "instructions": prescription.instructions,
                "doctor_notes": prescription.doctor_notes,
                "approved_at": prescription.approved_at,
                "is_ai_draft": prescription.is_ai_draft
            }

        freq_dict = FREQUENCY_MAP.get(lang, {})
        inst_dict = INSTRUCTIONS_MAP.get(lang, {})

        translated_meds = []
        for med in prescription.medications:
            translated_freq = freq_dict.get(med.frequency, med.frequency)
            translated_inst = inst_dict.get(med.instructions, med.instructions)

            translated_meds.append({
                "name": med.name,  # MUST preserve medical drug name
                "dosage": med.dosage,
                "frequency": translated_freq,
                "duration": med.duration,
                "instructions": translated_inst
            })

        translated_instructions = prescription.instructions
        for en_phrase, target_phrase in inst_dict.items():
            translated_instructions = translated_instructions.replace(en_phrase, target_phrase)

        return {
            "prescription_id": prescription.prescription_id,
            "language": lang,
            "status": prescription.status,
            "medications": translated_meds,
            "instructions": translated_instructions,
            "doctor_notes": prescription.doctor_notes,
            "approved_at": prescription.approved_at,
            "is_ai_draft": prescription.is_ai_draft
        }
