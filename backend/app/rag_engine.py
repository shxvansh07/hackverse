from typing import List, Optional, Dict, Any
from app.models import Medication, Prescription, PrescriptionStatus

# Curated Medical Formulary & Clinical Guidelines (Hackathon Knowledge Base)
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
    def retrieve_clinical_guidelines(symptoms: List[str], text_summary: str) -> List[Dict[str, Any]]:
        """
        Retrieves grounded clinical guidelines based on symptom matches.
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

        # Sort by match relevance score
        matched_protocols.sort(key=lambda x: x[0], reverse=True)
        return [p[1] for p in matched_protocols]

    @classmethod
    def generate_draft_prescription(
        cls,
        case_id: str,
        symptoms: List[str],
        summary_en: str
    ) -> Prescription:
        """
        Generates RAG-grounded draft prescription.
        Marked clearly as is_ai_draft = True and status = DRAFT.
        """
        matches = cls.retrieve_clinical_guidelines(symptoms, summary_en)
        meds: List[Medication] = []
        instructions_list = []

        if matches:
            top_match = matches[0]
            meds = top_match["medications"]
            instructions_list.append(top_match["instructions"])
        else:
            # General fallback supportive care
            meds = [
                Medication(
                    name="Supportive Care & Symptom Relief (Paracetamol)",
                    dosage="500 mg",
                    frequency="As needed (max 3 times/day)",
                    duration="3 days",
                    instructions="Take after food. Stay well hydrated."
                )
            ]
            instructions_list.append("Drink plenty of fluids, rest, and follow up with doctor if symptoms worsen.")

        return Prescription(
            case_id=case_id,
            status=PrescriptionStatus.DRAFT,
            medications=meds,
            instructions=" ".join(instructions_list),
            is_ai_draft=True
        )
