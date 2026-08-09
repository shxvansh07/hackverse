"""Retrieval-augmented grounding for prescription drafts.

The flow the spec asks for:

    structured case -> clinical query -> vector retrieval -> approved
    knowledge -> LLM draft generation -> doctor review

with one hard constraint layered on top: **retrieval is grounding, never
authorization**. Concretely, medications are copied verbatim out of the
curated formulary. The LLM writes the rationale prose and nothing else. There
is no path by which a model can introduce a drug, a dose or a duration that a
human did not put in knowledge/formulary.json.

Callers must still pass the result through safety.guards before it is stored.
This module does not know a case's risk state and must not be trusted to gate.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from app.patient_backend.ml_predictor import predict_condition
from app.rag.vector_store import SearchHit, VectorStore
from app.shared import knowledge
from app.shared.models import Medication, Prescription, PrescriptionStatus

logger = logging.getLogger(__name__)

#: Minimum top-1 retrieval score to trust a formulary match enough to draft
#: from it. This is a TF-IDF cosine score, not a calibrated probability — the
#: number was picked empirically against the ~34-protocol corpus (see
#: backend/tests/test_rag.py), not derived analytically, and would need
#: re-checking if the formulary's size or keyword style changes materially.
MIN_DRAFT_CONFIDENCE = 0.15

#: If the second-best match scores above this fraction of the top match's
#: score, the two are too close to call — this is exactly the failure mode
#: that used to make nearly every case draft the same generic protocol
#: (see PROTO-FEVER-VIRAL's old keyword overlap). Trust neither over the
#: other; fall through to the no-confident-match path instead.
MAX_AMBIGUITY_RATIO = 0.70


def _ml_corroborates(protocol: Dict[str, Any], ml_hypothesis: Optional[Dict[str, Any]]) -> bool:
    """True if the ML classifier's condition name overlaps a formulary
    candidate's own condition name.

    Used only to pick between two already-curated, human-written protocols
    when TF-IDF alone can't tell them apart — never to introduce a
    medication of its own. If this returns False, or ml_hypothesis is None,
    the caller must not draft from `protocol` on this basis.
    """
    if not ml_hypothesis:
        return False
    ml_condition = ml_hypothesis.get("condition", "").lower()
    proto_condition = protocol.get("condition", "").lower()
    if not ml_condition or not proto_condition:
        return False
    ml_words = {w for w in ml_condition.replace("(", " ").replace(")", " ").split() if len(w) > 3}
    proto_words = {w for w in proto_condition.replace("(", " ").replace(")", " ").split() if len(w) > 3}
    return bool(ml_words & proto_words)


class ClinicalRAGEngine:
    """Two indexes: protocols (what to prescribe) and guidance (why)."""

    def __init__(self) -> None:
        self._protocol_store = VectorStore()
        self._guidance_store = VectorStore()
        self._protocols_by_id: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    # ------------------------------------------------------------- indexing

    def load(self, force: bool = False) -> None:
        if self._loaded and not force:
            return

        self._protocol_store = VectorStore()
        self._guidance_store = VectorStore()
        self._protocols_by_id = {}

        for protocol in knowledge.protocols():
            proto_id = protocol.get("id") or protocol.get("icd10", "")
            if not proto_id:
                continue
            self._protocols_by_id[proto_id] = protocol

            # Keywords are repeated so patient vocabulary outweighs prose in
            # the vector — the query is patient language, not textbook English.
            keywords = " ".join(protocol.get("keywords", []))
            searchable = " ".join(
                [
                    protocol.get("condition", ""),
                    protocol.get("presentation", ""),
                    keywords, keywords, keywords,
                    " ".join(m.get("name", "") for m in protocol.get("medications", [])),
                ]
            )
            self._protocol_store.add(proto_id, searchable, {"protocol": protocol})

        for passage in knowledge.guidance_passages():
            passage_id = passage.get("id")
            if not passage_id:
                continue
            searchable = " ".join(
                [passage.get("topic", ""), passage.get("topic", ""), passage.get("text", "")]
            )
            self._guidance_store.add(passage_id, searchable, {"passage": passage})

        self._loaded = True
        logger.info(
            "Clinical RAG index built: %d protocols, %d guidance passages",
            len(self._protocol_store), len(self._guidance_store),
        )

    # ------------------------------------------------------------ retrieval

    @staticmethod
    def build_query(
        symptoms: Sequence[str],
        associated_symptoms: Sequence[str] = (),
        summary: str = "",
        transcript_terms: str = "",
    ) -> str:
        """Compose the retrieval query from structured state.

        Symptoms are weighted above narrative because the formulary is indexed
        on symptom vocabulary.
        """
        symptom_text = " ".join(symptoms)
        return " ".join(
            [symptom_text, symptom_text, " ".join(associated_symptoms), summary, transcript_terms]
        ).strip()

    def retrieve_protocols(self, query: str, top_k: int = 3) -> List[SearchHit]:
        self.load()
        return self._protocol_store.search(query, top_k=top_k, min_score=0.01)

    def retrieve_guidance(self, query: str, top_k: int = 3) -> List[SearchHit]:
        self.load()
        return self._guidance_store.search(query, top_k=top_k, min_score=0.01)

    def retrieve(self, query: str) -> Dict[str, Any]:
        """Everything needed to ground one draft, plus provenance for the UI."""
        protocol_hits = self.retrieve_protocols(query)
        guidance_hits = self.retrieve_guidance(query)

        return {
            "query": query,
            "protocols": [
                {
                    "id": hit.document.doc_id,
                    "score": round(hit.score, 4),
                    "condition": hit.metadata["protocol"].get("condition", ""),
                    "icd10": hit.metadata["protocol"].get("icd10", ""),
                    "protocol": hit.metadata["protocol"],
                }
                for hit in protocol_hits
            ],
            "guidance": [
                {
                    "id": hit.document.doc_id,
                    "score": round(hit.score, 4),
                    "topic": hit.metadata["passage"].get("topic", ""),
                    "text": hit.metadata["passage"].get("text", ""),
                }
                for hit in guidance_hits
            ],
        }

    # ------------------------------------------------------------- drafting

    def build_draft(
        self,
        case_id: str,
        symptoms: Sequence[str],
        associated_symptoms: Sequence[str] = (),
        summary: str = "",
        rationale: Optional[str] = None,
        history_context: str = "",
    ) -> tuple[Prescription, Dict[str, Any]]:
        """Assemble a draft from the top-matching formulary protocol.

        Returns (prescription, grounding) where `grounding` is the retrieval
        provenance shown to the doctor so they can see what the draft was
        based on.

        `history_context` — a returning patient's prior-visit digest (see
        RecordService.build_history_digest) — only ever widens the retrieval
        query so a recurring condition is more likely to match the right
        protocol. It can never introduce a medication of its own; that still
        comes exclusively from the curated formulary/ML fallback below.

        The returned prescription is always status=DRAFT / is_ai_draft=True.
        Only a doctor decision changes that.
        """
        self.load()
        query = self.build_query(symptoms, associated_symptoms, summary, history_context)
        retrieval = self.retrieve(query)

        medications: List[Medication] = []
        instructions_parts: List[str] = []
        icd10 = ""
        condition = ""
        matched_entries: List[Dict[str, Any]] = []
        ml_hypothesis: Optional[Dict[str, Any]] = None

        protocols = retrieval["protocols"]
        confident_protocol: Optional[Dict[str, Any]] = None

        if protocols:
            top = protocols[0]
            second = protocols[1] if len(protocols) > 1 else None
            ambiguous = second is not None and second["score"] > top["score"] * MAX_AMBIGUITY_RATIO

            if top["score"] >= MIN_DRAFT_CONFIDENCE and not ambiguous:
                confident_protocol = top["protocol"]
            elif top["score"] >= MIN_DRAFT_CONFIDENCE and ambiguous:
                # Two candidates are too close for TF-IDF alone to call. Let
                # the broader ML classifier (patient_backend/ml_predictor.py)
                # act as a tie-break between them — it may only pick between
                # protocols a human already wrote, never supply a medication
                # of its own.
                ml_hypothesis = predict_condition(
                    symptoms=list(symptoms),
                    associated_symptoms=list(associated_symptoms),
                    free_text=summary,
                )
                if _ml_corroborates(top["protocol"], ml_hypothesis):
                    confident_protocol = top["protocol"]
                elif _ml_corroborates(second["protocol"], ml_hypothesis):
                    confident_protocol = second["protocol"]

        if confident_protocol is not None:
            protocol = confident_protocol
            matched_entries.append(protocol)
            icd10 = protocol.get("icd10", "")
            condition = protocol.get("condition", "")

            for med in protocol.get("medications", []):
                medications.append(
                    Medication(
                        name=med["name"],
                        dosage=med["dosage"],
                        frequency=med["frequency"],
                        duration=med["duration"],
                        instructions=med["instructions"],
                    )
                )
            if protocol.get("instructions"):
                instructions_parts.append(protocol["instructions"])
        else:
            # No protocol matched confidently. Rather than let a model
            # improvise a medication, or trust a weak/ambiguous TF-IDF match,
            # the draft stays empty — but a broader classifier (trained on a
            # real 41-condition public dataset, see
            # patient_backend/ml_predictor.py) may still offer the doctor a
            # diagnostic hypothesis. This NEVER adds a medication: only a
            # condition name, confidence, description and precautions,
            # surfaced as text for the doctor to read, exactly like the
            # rationale prose already is.
            instructions_parts.append(
                "No curated protocol matched this presentation with enough confidence. "
                "No medication has been drafted; clinician assessment required."
            )
            if ml_hypothesis is None:
                ml_hypothesis = predict_condition(
                    symptoms=list(symptoms),
                    associated_symptoms=list(associated_symptoms),
                    free_text=summary,
                )
            if ml_hypothesis:
                instructions_parts.append(
                    f"For reference only, a statistical classifier trained on a public "
                    f"symptom dataset suggests this presentation may be consistent with "
                    f"{ml_hypothesis['condition']} ({ml_hypothesis['confidence'] * 100:.0f}% "
                    f"model confidence). This is not a diagnosis and has not informed any "
                    f"medication choice — clinician assessment required."
                )

        prescription = Prescription(
            case_id=case_id,
            status=PrescriptionStatus.DRAFT,
            medications=medications,
            instructions=" ".join(instructions_parts).strip(),
            is_ai_draft=True,
            icd10_code=icd10,
            icd10_title=knowledge.icd10_title(icd10) if icd10 else "",
            matched_condition=condition,
            rationale=rationale or "",
            grounding_sources=[p["id"] for p in retrieval["protocols"]]
            + [g["id"] for g in retrieval["guidance"]],
        )

        grounding = {
            "query": query,
            "protocols": [
                {k: v for k, v in p.items() if k != "protocol"} for p in retrieval["protocols"]
            ],
            "guidance": retrieval["guidance"],
            "matched_entries": matched_entries,
            "ml_hypothesis": ml_hypothesis,
        }
        if history_context:
            grounding["history_context"] = history_context
        return prescription, grounding

    def health(self) -> Dict[str, Any]:
        self.load()
        return {
            "protocols_indexed": len(self._protocol_store),
            "guidance_indexed": len(self._guidance_store),
        }


#: Process-wide engine; the index is built once on first use.
rag_engine = ClinicalRAGEngine()
