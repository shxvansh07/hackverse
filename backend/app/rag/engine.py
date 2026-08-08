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

from app.rag.vector_store import SearchHit, VectorStore
from app.shared import knowledge
from app.shared.models import Medication, Prescription, PrescriptionStatus

logger = logging.getLogger(__name__)


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
    ) -> tuple[Prescription, Dict[str, Any]]:
        """Assemble a draft from the top-matching formulary protocol.

        Returns (prescription, grounding) where `grounding` is the retrieval
        provenance shown to the doctor so they can see what the draft was
        based on.

        The returned prescription is always status=DRAFT / is_ai_draft=True.
        Only a doctor decision changes that.
        """
        self.load()
        query = self.build_query(symptoms, associated_symptoms, summary)
        retrieval = self.retrieve(query)

        medications: List[Medication] = []
        instructions_parts: List[str] = []
        icd10 = ""
        condition = ""
        matched_entries: List[Dict[str, Any]] = []

        if retrieval["protocols"]:
            top = retrieval["protocols"][0]
            protocol = top["protocol"]
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
            # No protocol matched. Rather than let a model improvise, emit a
            # deliberately empty draft; the doctor sees that retrieval found
            # nothing and prescribes from scratch.
            instructions_parts.append(
                "No curated protocol matched this presentation. "
                "No medication has been drafted; clinician assessment required."
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
        }
        return prescription, grounding

    def health(self) -> Dict[str, Any]:
        self.load()
        return {
            "protocols_indexed": len(self._protocol_store),
            "guidance_indexed": len(self._guidance_store),
        }


#: Process-wide engine; the index is built once on first use.
rag_engine = ClinicalRAGEngine()
