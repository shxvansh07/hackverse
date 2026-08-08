"""Clinic/doctor letterhead identity for the printable prescription.

Same pattern as auth._credentials(): read from env so nothing identifying is
hardcoded or committed, with sane demo defaults so the app still runs
out of the box. This is print/display metadata only — it has no bearing on
any clinical decision and is not authenticated (a patient needs it to render
their own prescription for printing).
"""

from __future__ import annotations

import os
from typing import Dict


def letterhead() -> Dict[str, str]:
    return {
        "hospital_name": os.getenv("HOSPITAL_NAME", "Clinical Assistant General Hospital"),
        "hospital_address": os.getenv(
            "HOSPITAL_ADDRESS", "123 Health Street, Bengaluru, Karnataka 560001"
        ),
        "hospital_phone": os.getenv("HOSPITAL_PHONE", "+91 80 1234 5678"),
        "hospital_registration_no": os.getenv("HOSPITAL_REGISTRATION_NO", "KA/HOSP/2026/00000"),
        "doctor_name": os.getenv("DOCTOR_NAME", "Dr. Sharma, MD"),
        "doctor_qualification": os.getenv("DOCTOR_QUALIFICATION", "MBBS, MD (General Medicine)"),
        "doctor_registration_no": os.getenv("DOCTOR_REGISTRATION_NO", "KMC/00000/2026"),
    }
