import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

import biotite.structure as struc
import biotite.structure.io as strucio
from sqlalchemy.orm import Session

from app import crud
from app.core.config import settings
from app.services.biophysics import get_interface_motif
from app.services.folddisco_search import run_folddisco
from app.services.foldseek_search import run_foldseek


async def handle_foldseek_request(
    db: Session, accession: str, chain: Optional[str], keep_all: bool
) -> Dict[str, str]:
    """
    Orchestrates the Foldseek process: extracts the PDB, calls the API,
    and formats the resulting Pandas DataFrame into an HTML table.
    Raises ValueError on client errors.
    """
    PROB_THRESHOLD = 0.4
    DATABASE = "pdb100"

    comp = crud.get_complex_by_accession(db, accession)
    if not comp:
        raise ValueError("Complex not found")

    cif_path = Path(settings.storage_root) / comp.file_path / "model.cif"
    if not cif_path.exists():
        raise ValueError("model.cif missing")

    search_file = str(cif_path)
    tmp_file_obj = None
    mode = "complex-3diaa"
    target_info = "Complex"

    # Extract specific chain to temporary PDB
    if chain:
        try:
            structure = strucio.load_structure(str(cif_path))
            if isinstance(structure, struc.AtomArrayStack):
                structure = structure[0]

            mask = (structure.chain_id == chain)
            chain_struct = structure[mask]

            if len(chain_struct) == 0:
                raise ValueError(f"Chain {chain} is empty")

            tmp_file_obj = tempfile.NamedTemporaryFile(suffix=".pdb", delete=False)
            strucio.save_structure(tmp_file_obj.name, chain_struct)
            tmp_file_obj.close()

            search_file = tmp_file_obj.name
            mode = "3diaa"
            target_info = f"Chain {chain}"

        except ValueError as ve:
            raise ve
        except Exception as e:
            if tmp_file_obj and os.path.exists(tmp_file_obj.name):
                os.remove(tmp_file_obj.name)
            raise ValueError(f"Failed to extract chain: {e}")

    try:
        ticket_or_error, df = await run_foldseek(
            search_file, keep_all, PROB_THRESHOLD, database=DATABASE, mode=mode
        )
    finally:
        if tmp_file_obj and os.path.exists(tmp_file_obj.name):
            try:
                os.remove(tmp_file_obj.name)
            except Exception:
                pass

    if ticket_or_error.startswith("ERROR"):
        error_msg = ticket_or_error.replace("ERROR:", "").strip()
        raise ValueError(f"Foldseek API Error: {error_msg}")

    # Build HTML Result
    if df.empty:
        msg = (
            f'<div class="alert alert-warning mb-0" role="alert">'
            f'<h6 class="alert-heading">No results for {target_info}</h6>'
            f'<p class="mb-0">Database: <strong>{DATABASE}</strong> | '
            f'Min. Prob: <strong>{PROB_THRESHOLD}</strong></p></div>'
        )
        return {"ticket": ticket_or_error, "html": msg}

    df["RCSB PDB"] = df["RCSB PDB"].apply(lambda url: f'<a href="{url}" target="_blank">PDB link</a>')

    table_html = df.to_html(
        classes="table table-sm table-striped table-hover",
        index=False, escape=False, justify="center", table_id="foldseek-table"
    )

    table_html += (
        f'<p class="small text-muted mt-2">'
        f'Results for <strong>{target_info}</strong> | Database: <strong>{DATABASE}</strong> | '
        f'Min. Prob: <strong>{PROB_THRESHOLD}</strong><br>'
        f'Powered by <a href="https://foldseek.com" target="_blank">Foldseek API</a>.</p>'
    )

    return {"ticket": ticket_or_error, "html": table_html}

async def handle_folddisco_request(
    db: Session, accession: str, database: List[str], mode_select: str,
    threshold: float, custom_motif: Optional[str]
) -> Dict[str, str]:
    """
    Orchestrates the FoldDisco process and generates a link to the official UI.
    """
    comp = crud.get_complex_by_accession(db, accession)
    if not comp:
        raise ValueError("Complex not found")

    cif_path = Path(settings.storage_root) / comp.file_path / "model.cif"
    if not cif_path.exists():
        raise ValueError("model.cif missing")

    try:
        if mode_select == "manual" and custom_motif:
            motif_str = custom_motif.strip()
        else:
            motif_str = get_interface_motif(cif_path, threshold=threshold)
    except Exception as e:
        raise ValueError(f"Failed to process structure motif: {e}")

    if not motif_str:
        return {
            "ticket": "ERROR",
            "html": '<div class="alert alert-warning">No residues found for the selected criteria.</div>'
        }


    ticket_or_error, result_url = await run_folddisco(str(cif_path), motif_str, database)

    if ticket_or_error.startswith("ERROR"):
        msg = ticket_or_error.replace("ERROR:", "").strip()
        return {"ticket": "ERROR", "html": f'<div class="alert alert-danger">Folddisco failed: {msg}</div>'}

    res_list = motif_str.split(',')
    preview = ",".join(res_list[:5]) + (", ..." if len(res_list) > 5 else "")


    html_output = (
        f'<div class="mt-3 pt-2 border-top">'
        f'  <p class="text-muted small mb-2">'
        f'    <strong>Motif ({mode_select}, {len(res_list)} residues):</strong> {preview}'
        f'  </p>'
        f'  <div class="alert alert-success d-flex flex-wrap align-items-center justify-content-between gap-3">'
        f'    <div>'
        f'      <strong>Job submitted successfully!</strong><br>'
        f'      <small>The Foldseek server is processing your query.</small>'
        f'    </div>'
        f'    <a href="{result_url}" target="_blank" class="btn btn-success fw-bold text-nowrap">'
        f'      View Results on FoldDisco ↗'
        f'    </a>'
        f'  </div>'
        f'  <p class="text-muted small mb-0">Databases: {", ".join(database)}</p>'
        f'</div>'
    )

    return {"ticket": ticket_or_error, "html": html_output}