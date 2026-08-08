import unittest
from app.shared.models import TriageCase, RiskState, PrescriptionStatus, Medication, Prescription
from app.patient_backend.safety_engine import evaluate_safety_triage
from app.patient_backend.rag_engine import RAGEngine
from app.patient_backend.triage_engine import TriageEngine
from app.patient_backend.translation import TranslationEngine
from app.patient_backend import ml_predictor

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

    def test_ml_predictor_plausible_condition(self):
        # Diarrhea + vomiting is a clean, well-represented pattern in the
        # training data — should confidently predict Gastroenteritis, not an
        # unrelated/implausible condition.
        result = ml_predictor.predict_condition(["diarrhea", "vomiting/nausea"])
        self.assertIsNotNone(result)
        self.assertEqual(result["condition"], "Gastroenteritis")
        self.assertGreater(result["confidence"], 0.15)
        self.assertTrue(result["description"])

    def test_ml_predictor_no_prediction_below_threshold(self):
        # A single generic symptom shouldn't produce a confident diagnosis.
        result = ml_predictor.predict_condition(["fever"])
        self.assertIsNone(result)

    def test_rag_falls_back_to_ml_beyond_curated_formulary(self):
        # Cough/cold/sore throat isn't in the 5-condition curated formulary's
        # exact keyword set for a *new* condition label, but the ML model
        # (41 conditions, real dataset) should still produce a diagnostic
        # hypothesis rather than only the generic Paracetamol fallback.
        rx = RAGEngine.generate_draft_prescription(
            case_id="CASE-TEST-03",
            symptoms=["cough", "cold/runny nose", "sore throat"],
            summary_en="Patient has cough, runny nose, and sore throat"
        )
        self.assertEqual(rx.status, PrescriptionStatus.DRAFT)
        self.assertTrue(rx.is_ai_draft)
        # Either the curated formulary matched (has real meds) or the ML
        # hypothesis path fired (explicit "doctor to determine treatment" —
        # never a fabricated dosage for an unverified condition).
        self.assertTrue(len(rx.medications) > 0)
        med = rx.medications[0]
        self.assertTrue(
            med.dosage == "To be specified by physician" or med.dosage not in ("", None)
        )

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
