"""Rendering a doctor-finalised prescription in the patient's language.

The governing rule: **changing language must never regenerate or reinterpret
the prescription**. What happens here is presentation, not clinical work.

Concretely:
  * medication name, dosage and duration are copied verbatim and are never
    sent to a translator at all
  * frequency and instructions may be translated, and the English original is
    always retained alongside the translation
  * every translation is checked by safety.guards.verify_translation before it
    is used; a failed check falls back to English rather than displaying text
    we cannot vouch for
  * a final integrity assertion compares the rendered medication against the
    canonical record field by field
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.ai.service import ai_service
from app.safety import guards
from app.shared.database import db
from app.shared.languages import resolve
from app.shared.models import (
    Medication,
    PresentedMedication,
    PresentedPrescription,
    Prescription,
)

logger = logging.getLogger(__name__)

#: Deterministic renderings for the handful of frequency phrases the curated
#: formulary actually uses. Tried before the LLM: for fixed clinical phrases a
#: reviewed table beats a model, and it works with no providers configured.
FREQUENCY_TABLE: Dict[str, Dict[str, str]] = {
    "hi": {
        "Once daily": "दिन में एक बार",
        "Twice daily": "दिन में दो बार",
        "Once daily at bedtime": "रात को सोते समय दिन में एक बार",
        "Every 6 to 8 hours as needed": "ज़रूरत पड़ने पर हर 6 से 8 घंटे में",
        "Twice daily as needed": "ज़रूरत पड़ने पर दिन में दो बार",
        "As needed after meals": "ज़रूरत पड़ने पर खाने के बाद",
        "Sip continuously through the day": "दिन भर थोड़ा-थोड़ा करके पीते रहें",
        "2-3 times daily": "दिन में 2-3 बार",
        "Daily": "रोज़",
    },
    "bn": {
        "Once daily": "দিনে একবার",
        "Twice daily": "দিনে দুইবার",
        "Once daily at bedtime": "রাতে ঘুমানোর সময় দিনে একবার",
        "Every 6 to 8 hours as needed": "প্রয়োজনে প্রতি 6 থেকে 8 ঘণ্টা পর",
        "Twice daily as needed": "প্রয়োজনে দিনে দুইবার",
        "As needed after meals": "প্রয়োজনে খাবারের পর",
        "Sip continuously through the day": "সারা দিন অল্প অল্প করে পান করুন",
        "2-3 times daily": "দিনে 2-3 বার",
        "Daily": "প্রতিদিন",
    },
    "mr": {
        "Once daily": "दिवसातून एकदा",
        "Twice daily": "दिवसातून दोनदा",
        "Once daily at bedtime": "रात्री झोपताना दिवसातून एकदा",
        "Every 6 to 8 hours as needed": "गरज असल्यास दर 6 ते 8 तासांनी",
        "Twice daily as needed": "गरज असल्यास दिवसातून दोनदा",
        "As needed after meals": "गरज असल्यास जेवणानंतर",
        "Sip continuously through the day": "दिवसभर थोडे थोडे प्या",
        "2-3 times daily": "दिवसातून 2-3 वेळा",
        "Daily": "दररोज",
    },
    "ta": {
        "Once daily": "நாளுக்கு ஒரு முறை",
        "Twice daily": "நாளுக்கு இரு முறை",
        "Once daily at bedtime": "இரவு உறங்கும் முன் நாளுக்கு ஒரு முறை",
        "Every 6 to 8 hours as needed": "தேவைப்படும்போது ஒவ்வொரு 6 முதல் 8 மணி நேரத்திற்கும்",
        "Twice daily as needed": "தேவைப்படும்போது நாளுக்கு இரு முறை",
        "As needed after meals": "தேவைப்படும்போது உணவுக்குப் பின்",
        "Sip continuously through the day": "நாள் முழுவதும் சிறிது சிறிதாக அருந்தவும்",
        "2-3 times daily": "நாளுக்கு 2-3 முறை",
        "Daily": "தினமும்",
    },
    "te": {
        "Once daily": "రోజుకు ఒకసారి",
        "Twice daily": "రోజుకు రెండుసార్లు",
        "Once daily at bedtime": "రాత్రి పడుకునే ముందు రోజుకు ఒకసారి",
        "Every 6 to 8 hours as needed": "అవసరమైనప్పుడు ప్రతి 6 నుండి 8 గంటలకు",
        "Twice daily as needed": "అవసరమైనప్పుడు రోజుకు రెండుసార్లు",
        "As needed after meals": "అవసరమైనప్పుడు భోజనం తర్వాత",
        "Sip continuously through the day": "రోజంతా కొద్దికొద్దిగా తాగుతూ ఉండండి",
        "2-3 times daily": "రోజుకు 2-3 సార్లు",
        "Daily": "ప్రతిరోజూ",
    },
    "gu": {
        "Once daily": "દિવસમાં એક વાર",
        "Twice daily": "દિવસમાં બે વાર",
        "Once daily at bedtime": "રાત્રે સૂતી વખતે દિવસમાં એક વાર",
        "Every 6 to 8 hours as needed": "જરૂર પડ્યે દર 6 થી 8 કલાકે",
        "Twice daily as needed": "જરૂર પડ્યે દિવસમાં બે વાર",
        "As needed after meals": "જરૂર પડ્યે જમ્યા પછી",
        "Sip continuously through the day": "આખો દિવસ થોડું થોડું પીતા રહો",
        "2-3 times daily": "દિવસમાં 2-3 વાર",
        "Daily": "દરરોજ",
    },
    "kn": {
        "Once daily": "ದಿನಕ್ಕೆ ಒಮ್ಮೆ",
        "Twice daily": "ದಿನಕ್ಕೆ ಎರಡು ಬಾರಿ",
        "Once daily at bedtime": "ರಾತ್ರಿ ಮಲಗುವ ಮುನ್ನ ದಿನಕ್ಕೆ ಒಮ್ಮೆ",
        "Every 6 to 8 hours as needed": "ಅಗತ್ಯವಿದ್ದಾಗ ಪ್ರತಿ 6 ರಿಂದ 8 ಗಂಟೆಗಳಿಗೊಮ್ಮೆ",
        "Twice daily as needed": "ಅಗತ್ಯವಿದ್ದಾಗ ದಿನಕ್ಕೆ ಎರಡು ಬಾರಿ",
        "As needed after meals": "ಅಗತ್ಯವಿದ್ದಾಗ ಊಟದ ನಂತರ",
        "Sip continuously through the day": "ದಿನವಿಡೀ ಸ್ವಲ್ಪ ಸ್ವಲ್ಪ ಕುಡಿಯುತ್ತಿರಿ",
        "2-3 times daily": "ದಿನಕ್ಕೆ 2-3 ಬಾರಿ",
        "Daily": "ಪ್ರತಿದಿನ",
    },
}


class PrescriptionService:
    @classmethod
    async def _localise(cls, source: str, lang: str) -> tuple[str, bool]:
        """Translate one string, verifying it preserved clinical content.

        Returns (text, ok). On failure the source is returned unchanged so the
        caller can display English and say so.
        """
        if not source.strip():
            return "", True

        translated = await ai_service.translate(source, lang)
        if not translated:
            return source, False

        check = guards.verify_translation(source, translated)
        if not check.allowed:
            logger.warning(
                "Translation to %s rejected by guard (%s); falling back to English",
                lang, check.reason,
            )
            db.record_audit(
                "TRANSLATION_REJECTED", actor="system",
                detail=f"Guard {check.reason} for language {lang}",
                metadata={"details": check.details},
            )
            return source, False

        return translated, True

    @classmethod
    async def present(
        cls, prescription: Prescription, requested_lang: str,
    ) -> PresentedPrescription:
        """Render a finalised prescription for the patient.

        Callers must have already passed the record through
        guards.may_release_to_patient. This method assumes that check has
        happened and does not repeat it.
        """
        language = resolve(requested_lang)
        cached = prescription.translations.get(language.code)
        if cached:
            try:
                return PresentedPrescription.model_validate(cached)
            except Exception:  # noqa: BLE001 - stale cache shape; rebuild below
                logger.info("Discarding unreadable translation cache for %s", language.code)

        all_ok = True
        presented_meds: List[PresentedMedication] = []

        for med in prescription.medications:
            # Name, dosage and duration are never sent to a translator.
            frequency_localised = med.frequency
            instructions_localised = med.instructions

            if language.code != "en":
                table = FREQUENCY_TABLE.get(language.code, {})
                if med.frequency in table:
                    frequency_localised = table[med.frequency]
                else:
                    frequency_localised, ok = await cls._localise(med.frequency, language.code)
                    all_ok = all_ok and ok

                instructions_localised, ok = await cls._localise(med.instructions, language.code)
                all_ok = all_ok and ok

            presented = PresentedMedication(
                name=med.name,
                dosage=med.dosage,
                duration=med.duration,
                frequency=med.frequency,
                instructions=med.instructions,
                frequency_localised=frequency_localised if language.code != "en" else "",
                instructions_localised=instructions_localised if language.code != "en" else "",
            )

            # Final assertion: the rendered record must match the canonical one
            # on every load-bearing field. Belt and braces over the guard above.
            integrity = guards.verify_medication_preserved(med, presented.model_dump())
            if not integrity.allowed:
                logger.error("Medication integrity check failed: %s", integrity.details)
                presented = PresentedMedication(
                    name=med.name, dosage=med.dosage, duration=med.duration,
                    frequency=med.frequency, instructions=med.instructions,
                )
                all_ok = False

            presented_meds.append(presented)

        instructions_localised = prescription.instructions
        if language.code != "en":
            instructions_localised, ok = await cls._localise(
                prescription.instructions, language.code
            )
            all_ok = all_ok and ok

        notice: Optional[str] = None
        if language.code != "en" and not all_ok:
            notice = (
                "Some parts of this prescription are shown in English because a verified "
                "translation was not available. The medicines and doses are unchanged."
            )

        result = PresentedPrescription(
            prescription_id=prescription.prescription_id,
            case_id=prescription.case_id,
            status=prescription.status,
            language=language.code,
            requested_language=language.code,
            translation_applied=(language.code != "en" and all_ok),
            translation_notice=notice,
            medications=presented_meds,
            instructions=prescription.instructions,
            instructions_localised=(
                instructions_localised if language.code != "en" else ""
            ),
            doctor_name=prescription.doctor_name,
            doctor_notes=prescription.doctor_notes,
            approved_at=prescription.approved_at,
            icd10_code=prescription.icd10_code,
            is_ai_draft=prescription.is_ai_draft,
        )

        # Cache only fully verified renderings; a partial fallback should be
        # retried next time in case the provider has recovered.
        if all_ok:
            prescription.translations[language.code] = result.model_dump()
            db.save_prescription(prescription)

        return result
