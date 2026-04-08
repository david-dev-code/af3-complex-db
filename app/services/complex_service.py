from typing import Dict, Any, Optional
from fastapi.datastructures import FormData
from sqlalchemy.orm import Session

from app import crud, models


def get_edit_context(db: Session, accession: str) -> Optional[Dict[str, Any]]:
    """Fetches data required to render the complex edit page."""
    comp = crud.get_complex_by_accession(db, accession)
    if not comp:
        return None

    collections = crud.list_collections(db, limit=1000)

    return {
        "c": comp,
        "collections": collections
    }


def process_edit_form(db: Session, accession: str, form_data: FormData) -> Optional[models.Complex]:
    """Parses form data and updates the complex and its chain metadata."""
    description = form_data.get("description")
    collection_id = form_data.get("collection_id")
    new_col_name = form_data.get("new_collection_name")

    # Parse Chain Updates (Format: chain_{id}_{field} -> value)
    chain_updates = {}
    for key, value in form_data.items():
        if key.startswith("chain_"):
            parts = key.split("_")
            if len(parts) >= 3:
                try:
                    cid = int(parts[1])
                    field_key = "_".join(parts[2:])

                    if cid not in chain_updates:
                        chain_updates[cid] = {}

                    chain_updates[cid][field_key] = value
                except ValueError:
                    continue

    updated_comp = crud.update_complex_full(
        db,
        accession,
        description=str(description) if description else "",
        collection_id=collection_id,
        new_collection_name=str(new_col_name) if new_col_name else None,
        chain_updates=chain_updates
    )

    return updated_comp


def get_complex_detail_context(db: Session, accession: str) -> Optional[Dict[str, Any]]:
    """
    Fetches the complex and structures its metadata and chains
    for the complex detail template.
    """
    comp = crud.get_complex_by_accession(db, accession)
    if not comp:
        return None

    # Compute summaries
    comp.summary_names = crud.compute_summary_names(comp)
    comp.summary_genes = crud.compute_summary_genes(comp)

    # Format chain sections with UniProt entries
    chain_sections: list[dict] = []
    for idx, ch in enumerate(comp.chains, start=1):
        entries = ch.uniparc.accessions if ch.uniparc else []
        if not entries:
            continue

        chain_sections.append(
            {
                "idx": idx,
                "letter": chr(64 + idx),  # Converts 1->A, 2->B, etc.
                "entries": entries,
            }
        )

    return {
        "c": comp,
        "chain_sections": chain_sections
    }