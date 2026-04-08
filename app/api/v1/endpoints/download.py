from __future__ import annotations
import csv
import io
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload
from starlette.background import BackgroundTask

from app import crud, models
from app.core.config import settings
from app.core.database import get_db

router = APIRouter(prefix="/download", tags=["download"])


@router.get("/complex_table.csv")
def download_complex_table(db: Session = Depends(get_db)):
    """
    Streams the entire 'complex' table as a CSV file.
    Includes columns for accession, scores, seeds, and UniParc IDs.
    """
    # Eager load complexes with collection name and UniParc IDs
    complexes = (
        db.query(models.Complex)
        .options(
            joinedload(models.Complex.collection),
            joinedload(models.Complex.chains)
            .joinedload(models.Chain.uniparc)
        )
        .order_by(models.Complex.accession.asc())
        .all()
    )

    # Write CSV to memory buffer
    buf = io.StringIO()
    cols = [
        "accession", "description", "created_at", "version",
        "collection_name", "iptm", "ptm", "ranking_score",
        "fraction_disordered", "has_clash", "mean_plddt",
        "submitted_seeds", "submitted_models_per_seed",
        "uniparc_ids",
    ]
    writer = csv.DictWriter(buf, fieldnames=cols)
    writer.writeheader()

    for c in complexes:
        upis = [
            ch.uniparc.upi for ch in c.chains if ch.uniparc is not None
        ]
        writer.writerow({
            "accession": c.accession,
            "description": c.description or "",
            "created_at": c.created_at.isoformat(sep=" ", timespec="seconds"),
            "version": c.version,
            "collection_name": c.collection.name if c.collection else "",
            "iptm": c.iptm,
            "ptm": c.ptm,
            "ranking_score": c.ranking_score,
            "fraction_disordered": c.fraction_disordered,
            "has_clash": c.has_clash,
            "mean_plddt": c.mean_plddt,
            "submitted_seeds": c.submitted_seeds,
            "submitted_models_per_seed": c.submitted_models_per_seed,
            "uniparc_ids": ";".join(upis),
        })

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="complex_table.csv"'},
    )


# --- Helper Functions ---

def _add_file(zf: ZipFile, src: Path, arcname: str):
    """Adds a file to the ZIP archive."""
    if src.exists():
        zf.write(src, arcname)
    else:
        raise FileNotFoundError(src)


def _add_complex(zf: ZipFile, accession: str, part: str):
    """
    Adds the requested files (cif, confidences, or all) for a specific complex to the ZIP.
    Always includes the Terms of Use.
    """
    folder = Path(settings.storage_root) / accession
    if not folder.is_dir():
        raise FileNotFoundError(folder)

    if part in ("cif", "all"):
        _add_file(zf, folder / "model.cif", f"{accession}/model.cif")

    if part in ("confidences", "all"):
        _add_file(zf, folder / "confidences.json", f"{accession}/confidences.json")

    # Include Terms of Use
    _add_file(zf, Path(settings.storage_root) / "static/TERMS_OF_USE.md", f"{accession}/TERMS_OF_USE.md")


def _build_zip(accessions: list[str], part: str) -> tuple[str, str]:
    """
    Creates a temporary ZIP file containing data for the given accessions.
    Returns the target filename and the path to the temporary file.
    """
    tmp = NamedTemporaryFile(prefix="af3_", suffix=".zip", delete=False)
    try:
        with ZipFile(tmp, "w", ZIP_DEFLATED) as zf:
            for ac in accessions:
                _add_complex(zf, ac, part)
        tmp.close()

        if len(accessions) > 1:
            name = f"multi_{part}.zip"
        elif accessions:
            name = f"{accessions[0]}_{part}.zip"
        else:
            name = "empty.zip"

        return name, tmp.name
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise


def _stream_zip(file_path: str, out_name: str) -> StreamingResponse:
    """Streams the ZIP file and deletes it from disk afterwards."""
    return StreamingResponse(
        open(file_path, "rb"),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
        background=BackgroundTask(lambda: os.unlink(file_path)),
    )


# --- Endpoints ---

@router.get("/collection/{collection_name}")
def download_collection(
        collection_name: str,
        part: str = Query("all", enum=["cif", "confidences", "all"]),
        db: Session = Depends(get_db),
):
    """
    Downloads all complexes within a specific collection as a ZIP file.
    """
    accs = crud.accessions_in_collection(db, collection_name)
    if not accs:
        raise HTTPException(404, "Collection not found or empty")

    out_name, tmp_path = _build_zip(accs, part)
    return _stream_zip(tmp_path, f"collection_{collection_name}_{part}.zip")


@router.get("/search", include_in_schema=False)
def download_search(
        request: Request,
        part: str = Query("all", enum=["cif", "confidences", "all"]),
        db: Session = Depends(get_db),
):
    """
    Downloads all complexes matching the search query parameters (Quick or Advanced) as a ZIP file.
    """
    qp = request.query_params

    if "q" in qp:
        # Quick Search
        accs = crud.accessions_for_quick_search(db, qp["q"])
    else:
        # Advanced Search
        accs = crud.accessions_for_advanced_search(db, qp)

    if not accs:
        raise HTTPException(404, "No matches for query")

    out_name, tmp_path = _build_zip(accs, part)
    return _stream_zip(tmp_path, f"search_{part}.zip")


@router.get("/everything")
def download_everything(
        part: str = Query("all", enum=["cif", "confidences", "all"]),
        db: Session = Depends(get_db),
):
    """
    Downloads the entire database content as a ZIP file.
    """
    accs = crud.all_accessions(db)
    out_name, tmp_path = _build_zip(accs, part)
    return _stream_zip(tmp_path, f"af3db_full_{part}.zip")


@router.get("/{accession}")
def download_single(
        accession: str,
        part: str = Query("all", enum=["cif", "confidences", "all"]),
        db: Session = Depends(get_db),
):
    """
    Downloads a single complex as a ZIP file.
    """
    if not crud.get_complex_by_accession(db, accession):
        raise HTTPException(404, "Complex not found")

    out_name, tmp_path = _build_zip([accession], part)
    return _stream_zip(tmp_path, out_name)
