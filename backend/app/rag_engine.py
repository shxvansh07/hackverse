import os
import json
import httpx
from typing import List, Optional, Dict, Any
from app.models import Medication, Prescription, PrescriptionStatus
from app.ai_service import GEMINI_API_KEY, GROQ_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY, NVIDIA_API_KEY
from app.ai_service import GEMINI_MODEL, GROQ_MODEL, OPENAI_MODEL, DEEPSEEK_MODEL, NVIDIA_MODEL

# Safe Over-The-Counter (OTC) medication whitelist. 
# Only medications matching this list (case-insensitive) are allowed to be drafted.
OTC_WHITELIST = {
    "paracetamol", "acetaminophen", "crocin", "calpol", "dolo", "dolo 650", "dolo-650",
    "cetirizine", "allegra", "fexofenadine", "levocetirizine",
    "pantoprazole", "pantocid", "omeprazole", "ranitidine", "famotidine",
    "ors", "oral rehydration salts", "electral", "hydration",
    "cough lozenges", "strepsils", "koflet",
    "cough syrup (dextromethorphan)", "grilinctus",
    "magaldrate", "simethicone", "digene", "gelusil",
    "loperamide", "imodium",
    "spasmonil", "dicyclomine",
    "ibuprofen", "combiflam",
    "multivitamin", "zinc", "vitamin c", "limcee",
    "warm saline gargle & steam inhalation", "warm saline gargle", "steam inhalation"
}

# Curated Medical Formulary & Clinical Guidelines (Fallback Knowledge Base)
CLINICAL_KNOWLEDGE_BASE = [
    {
        "icd10": "R50.9",
        "condition": "Fever / Acute Viral Syndrome / Body Ache",
        "keywords": ["fever", "bukhar", "bukhar hai", "body pain", "body ache", "sar dard", "headache", "chills", "badan dard"],
        "medications": [
            Medication(
                name="Paracetamol (Acetaminophen)",
                dosage="500 mg",
                frequency="Every 6 to 8 hours as needed",
                duration="3-5 days",
                instructions="Take with water after food. Do not exceed 4,000 mg per day. Rest and maintain fluid intake."
            )
        ],
        "instructions": "Maintain adequate oral hydration with water and ORS. Avoid heavy physical exertion. Seek immediate doctor care if fever exceeds 102°F or lasts >4 days."
    },
    {
        "icd10": "J00",
        "condition": "Acute Nasopharyngitis (Common Cold / Runny Nose)",
        "keywords": ["cold", "cough", "runny nose", "sneeze", "sneezing", "chink", "gala dard", "sore throat", "khansi"],
        "medications": [
            Medication(
                name="Cetirizine HCl",
                dosage="10 mg",
                frequency="Once daily at bedtime",
                duration="5 days",
                instructions="May cause mild drowsiness. Avoid driving or operating machinery after taking."
            ),
            Medication(
                name="Warm Saline Gargle & Steam Inhalation",
                dosage="2-3 times daily",
                frequency="Daily",
                duration="5-7 days",
                instructions="Inhale warm steam for 5-10 minutes and gargle with warm salt water."
            )
        ],
        "instructions": "Drink warm liquids. Rest well and monitor for signs of breathing difficulty or chest tightness."
    },
    {
        "icd10": "K21.9",
        "condition": "Gastroesophageal Reflux / Dyspepsia (Acidity)",
        "keywords": ["acidity", "heartburn", "stomach burn", "gas", "pet me jaln", "pet me dard mild", "indigestion", "sour burps"],
        "medications": [
            Medication(
                name="Pantoprazole",
                dosage="40 mg",
                frequency="Once daily",
                duration="7 days",
                instructions="Take 30 minutes before breakfast on an empty stomach with plain water."
            ),
            Medication(
                name="Antacid Oral Gel (Magaldrate + Simethicone)",
                dosage="10 ml",
                frequency="As needed after meals",
                duration="5 days",
                instructions="Shake well before use. Take 1-2 teaspoons when experiencing acid burn."
            )
        ],
        "instructions": "Avoid spicy, oily, and fried foods. Eat small frequent meals. Do not lie down immediately after eating."
    },
    {
        "icd10": "G44.2",
        "condition": "Tension-type Headache",
        "keywords": ["headache", "head pain", "sir dard", "sar me dard", "stress headache"],
        "medications": [
            Medication(
                name="Paracetamol",
                dosage="500 mg",
                frequency="Twice daily as needed",
                duration="3 days",
                instructions="Take after light food with water. Stay hydrated."
            )
        ],
        "instructions": "Ensure adequate rest, hydration (2-3L water/day), and reduce screen exposure."
    },
    {
        "icd10": "K59.1",
        "condition": "Mild Diarrhea / Loose Stools",
        "keywords": ["diarrhea", "loose motion", "dast", "loose stool", "stomach upset"],
        "medications": [
            Medication(
                name="Oral Rehydration Salts (ORS)",
                dosage="1 sachet in 1 Litre water",
                frequency="Sip continuously throughout the day",
                duration="3 days",
                instructions="Dissolve 1 sachet completely in 1L clean drinking water. Drink after every loose motion."
            ),
            Medication(
                name="Probiotic Spores (Bacillus clausii)",
                dosage="5 ml mini-bottle",
                frequency="Twice daily",
                duration="5 days",
                instructions="Drink directly or dilute in small amount of water."
            )
        ],
        "instructions": "Eat light bland diet (banana, rice, yogurt). Avoid dairy, milk, greasy or fried items."
    }
]

class RAGEngine:
    @staticmethod
    def retrieve_clinical_guidelines_fallback(symptoms: List[str], text_summary: str) -> List[Dict[str, Any]]:
        """
        Retrieves local clinical guidelines based on keyword matching (fallback mode).
        """
        matched_protocols = []
        combined = f"{' '.join(symptoms)} {text_summary}".lower()

        for protocol in CLINICAL_KNOWLEDGE_BASE:
            score = 0
            for kw in protocol["keywords"]:
                if kw in combined:
                    score += 1
            if score > 0:
                matched_protocols.append((score, protocol))

        matched_protocols.sort(key=lambda x: x[0], reverse=True)
        return [p[1] for p in matched_protocols]

    @classmethod
    def call_llm_for_prescription(cls, symptoms: List[str], summary_en: str) -> Optional[List[Dict[str, str]]]:
        """
        Queries active LLM to generate a clinical draft prescription in JSON format.
        """
        system_prompt = (
            "You are a clinical decision support assistant drafting a safe over-the-counter (OTC) prescription.\n"
            "Generate appropriate supportive OTC medications based on the patient's symptoms.\n"
            "Return ONLY a valid JSON array of objects representing the draft medications, with no other text, markdown formatting blocks (like ```json), or explanations.\n"
            "JSON structure:\n"
            "[\n"
            "  {\n"
            "    \"name\": \"Medication Name (e.g. Paracetamol)\",\n"
            "    \"dosage\": \"Dosage (e.g. 500 mg)\",\n"
            "    \"frequency\": \"Frequency (e.g. Twice daily)\",\n"
            "    \"duration\": \"Duration (e.g. 3 days)\",\n"
            "    \"instructions\": \"Instructions (e.g. Take with water after meals)\"\n"
            "  }\n"
            "]"
        )
        user_prompt = f"Patient Symptoms: {', '.join(symptoms)}\nCase Summary: {summary_en}"

        # 1. Try Gemini
        if GEMINI_API_KEY:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
                payload = {
                    "contents": [{"parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}],
                    "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}
                }
                with httpx.Client(timeout=8.0) as client:
                    resp = client.post(url, json=payload)
                    if resp.status_code == 200:
                        raw_text = resp.json()['candidates'][0]['content']['parts'][0]['text'].strip()
                        return json.loads(raw_text)
            except Exception as e:
                print(f"RAG Gemini Exception: {e}")

        # 2. Try Groq
        if GROQ_API_KEY:
            try:
                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
                payload = {
                    "model": GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"}
                }
                with httpx.Client(timeout=8.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        raw_text = resp.json()["choices"][0]["message"]["content"].strip()
                        data = json.loads(raw_text)
                        if isinstance(data, dict) and "medications" in data:
                            return data["medications"]
                        if isinstance(data, list):
                            return data
            except Exception as e:
                print(f"RAG Groq Exception: {e}")

        # 3. Try OpenAI
        if OPENAI_API_KEY:
            try:
                url = "https://api.openai.com/v1/chat/completions"
                headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
                payload = {
                    "model": OPENAI_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"}
                }
                with httpx.Client(timeout=8.0) as client:
                    resp = client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        raw_text = resp.json()["choices"][0]["message"]["content"].strip()
                        data = json.loads(raw_text)
                        if isinstance(data, list):
                            return data
                        if isinstance(data, dict) and "medications" in data:
                            return data["medications"]
            except Exception as e:
                print(f"RAG OpenAI Exception: {e}")

        return None

    @classmethod
    def generate_draft_prescription(
        cls,
        case_id: str,
        symptoms: List[str],
        summary_en: str
    ) -> Prescription:
        """
        Generates RAG-grounded draft prescription dynamically using LLM
        filtered through a strict safety whitelist checker.
        """
        meds: List[Medication] = []
        instructions_list = []

        # Try dynamic generation first
        llm_draft = cls.call_llm_for_prescription(symptoms, summary_en)
        
        if llm_draft and isinstance(llm_draft, list):
            for item in llm_draft:
                name = item.get("name", "").strip()
                # Case-insensitive safety whitelist check
                is_safe = False
                name_lower = name.lower()
                for whitelisted in OTC_WHITELIST:
                    if whitelisted in name_lower or name_lower in whitelisted:
                        is_safe = True
                        break

                if is_safe:
                    meds.append(Medication(
                        name=name,
                        dosage=item.get("dosage", "As directed"),
                        frequency=item.get("frequency", "Once daily"),
                        duration=item.get("duration", "3 days"),
                        instructions=item.get("instructions", "Take with water")
                    ))
            
            if meds:
                instructions_list.append("Supportive care and OTC medication draft. Please follow doctor's advice and maintain hydration.")

        # Fallback to local rule engine if LLM fails or whitelists all generated items
        if not meds:
            matches = cls.retrieve_clinical_guidelines_fallback(symptoms, summary_en)
            if matches:
                top_match = matches[0]
                meds = top_match["medications"]
                instructions_list.append(top_match["instructions"])
            else:
                meds = [
                    Medication(
                        name="Supportive Care & Symptom Relief (Paracetamol)",
                        dosage="500 mg",
                        frequency="As needed (max 3 times/day)",
                        duration="3 days",
                        instructions="Take after food. Stay well hydrated."
                    )
                ]
                instructions_list.append("Drink plenty of fluids, rest, and follow up with a doctor if symptoms worsen.")

        return Prescription(
            case_id=case_id,
            status=PrescriptionStatus.DRAFT,
            medications=meds,
            instructions=" ".join(instructions_list),
            is_ai_draft=True
        )
