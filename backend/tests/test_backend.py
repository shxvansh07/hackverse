import unittest
from app.shared.models import TriageCase, RiskState, PrescriptionStatus, Medication, Prescription
from app.patient_backend.safety_engine import evaluate_safety_triage
from app.patient_backend.rag_engine import RAGEngine
from app.patient_backend.triage_engine import TriageEngine
from app.patient_backend.translation import TranslationEngine

class TestBackendComponents(unittest.TestCase):
    def test_safety_urgent_chest_pain(self):
        state, flags = evaluate_safety_triage(
            symptoms=["chest pain"],
            associated_symptoms=[],
            patient_text_raw="Mujhe 2 ghante se seene me dard ho raha hai"
        )
        self.assertEqual(state, RiskState.URGENT)
        self.assertTrue(len(flags) > 0)

    def test_safety_low_risk_fever(self):
        state, flags = evaluate_safety_triage(
            symptoms=["fever"],
            associated_symptoms=["headache"],
            patient_text_raw="Mild fever for 2 days"
        )
        self.assertEqual(state, RiskState.LOW_RISK)
        self.assertEqual(len(flags), 0)

    def test_rag_draft_prescription(self):
        rx = RAGEngine.generate_draft_prescription(
            case_id="CASE-TEST-01",
            symptoms=["fever", "body ache"],
            summary_en="Patient has fever and body ache"
        )
        self.assertEqual(rx.status, PrescriptionStatus.DRAFT)
        self.assertTrue(rx.is_ai_draft)
        self.assertTrue(len(rx.medications) > 0)
        self.assertIn("Paracetamol", rx.medications[0].name)

    def test_translation_hindi_preserves_med_name(self):
        rx = Prescription(
            case_id="CASE-TEST-02",
            status=PrescriptionStatus.APPROVED,
            medications=[
                Medication(
                    name="Paracetamol (Acetaminophen)",
                    dosage="500 mg",
                    frequency="Twice daily",
                    duration="3 days",
                    instructions="Take with water after food"
                )
            ],
            instructions="Rest well",
            is_ai_draft=False
        )
        translated = TranslationEngine.get_translated_prescription(rx, lang="hi")
        self.assertEqual(translated["language"], "hi")
        self.assertEqual(translated["medications"][0]["name"], "Paracetamol (Acetaminophen)")
        self.assertIn("Twice daily", translated["medications"][0]["frequency"])

if __name__ == "__main__":
    unittest.main()
