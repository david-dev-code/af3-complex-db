import io
import json
import logging
import math
import os
import re
import shutil
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path
from typing import List, Optional

import numpy as np
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, Response, Request, Body, Query, \
    BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crud, schemas, models
from app.api.deps import get_current_admin
from app.core.config import settings
from app.core.database import get_db
from app.local_alphafold_parser.alphafold_parser import AlphaFoldParser as LocalAlphaFoldParser
from app.server_alphafold_parser.alphafold_parser import AlphaFoldParser as ServerAlphaFoldParser

router = APIRouter()
log = logging.getLogger(__name__)



class BulkCollectionUpdate(BaseModel):
    accessions: List[str]
    new_collection_name: Optional[str] = None
    existing_collection_id: Optional[int] = None
    move_items: bool = True




@router.delete("/{accession}", status_code=204, summary="Delete a complex")
def delete_complex(
        accession: str,
        db: Session = Depends(get_db),
        admin_user: str = Depends(get_current_admin)
):
    """
    Deletes a complex and its files. Requires Admin Authentication.
    Automatically removes the collection if it becomes empty after deletion.
    """
    comp = crud.get_complex_by_accession(db, accession)
    if not comp:
        raise HTTPException(status_code=404, detail="Complex not found")


    coll_id_to_check = comp.collection_id

    # Remove files from disk
    full_path = settings.storage_root / comp.file_path
    if full_path.exists() and full_path.is_dir():
        try:
            shutil.rmtree(full_path)
        except Exception as e:
            print(f"[DELETE] Error removing folder {full_path}: {e}")

    # Delete from database
    db.delete(comp)
    db.commit()


    if coll_id_to_check:
        remaining = db.query(models.Complex).filter(models.Complex.collection_id == coll_id_to_check).count()
        if remaining == 0:
            db.query(models.Collection).filter(models.Collection.id == coll_id_to_check).delete()
            db.commit()

    return Response(status_code=204)


@router.post("/", response_model=list[schemas.ComplexOut], status_code=201, include_in_schema=False)
async def submit_complex(
        response: Response,
        request: Request,
        background_tasks: BackgroundTasks,
        submitted_from: str = Form(...),
        description: str | None = Form(None),
        collection_name: str | None = Form(None),
        filename_pattern: str | None = Form(None),
        mapping_fallback_only: bool = Form(False),
        bundle: UploadFile = File(...),
        admin_user: str = Depends(get_current_admin),
        db: Session = Depends(get_db),
):
    """
    Internal logic to process the uploaded ZIP bundle.
    Extracts files, creates DB entries, and delegates heavy parsing to a background task.
    """
    print(f"[API] submit_complex: from={submitted_from!r} bundle={getattr(bundle, 'filename', None)!r} fallback_only={mapping_fallback_only}", flush=True)

    # Determine temporary storage location
    tmp_base = getattr(settings, "upload_tmp_dir", None)
    if not tmp_base:
        tmp_base = Path(getattr(settings, "storage_root", ".")) / "tmp"
    tmp_base = Path(tmp_base)
    tmp_base.mkdir(parents=True, exist_ok=True)

    # Check content length
    try:
        expected_len = int(request.headers.get("content-length", "0"))
    except Exception:
        expected_len = 0

    print(f"[UPLOAD] from={submitted_from!r} collection={collection_name!r} file={bundle.filename!r} content_length={expected_len}", flush=True)

    # Check for sufficient disk space
    try:
        usage = shutil.disk_usage(tmp_base)
        need = max(expected_len, 0)
        need_with_headroom = (need or 0) * 2 + 2 * 1024 ** 3
        print(f"[UPLOAD] tmp_base={tmp_base} free={usage.free} need≈{need_with_headroom}", flush=True)
        if expected_len and usage.free < need_with_headroom:
            raise HTTPException(status_code=507, detail="Insufficient storage on temp volume")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[UPLOAD] Free-space check failed: {e}", flush=True)

    tmp_zip = None
    tmpdir = None
    try:
        # Stream ZIP to disk
        tmp_zip = tempfile.NamedTemporaryFile(delete=False, dir=tmp_base, suffix=".zip")
        written = 0
        CHUNK = 8 * 1024 * 1024
        while True:
            chunk = await bundle.read(CHUNK)
            if not chunk:
                break
            tmp_zip.write(chunk)
            written += len(chunk)
            if written % (1024 ** 3) < CHUNK:
                print(f"[UPLOAD] written≈{written / 1024 ** 3:.1f} GiB", flush=True)
        tmp_zip.flush()
        os.fsync(tmp_zip.fileno())
        tmp_zip.close()
        print(f"[UPLOAD] saved_zip={tmp_zip.name} size={written}", flush=True)

        # Extract ZIP
        tmpdir = Path(tempfile.mkdtemp(dir=tmp_base))
        print(f"[UPLOAD] Extracting to {tmpdir}", flush=True)
        try:
            with zipfile.ZipFile(tmp_zip.name) as zf:
                namelist = zf.namelist()
                print(f"[UPLOAD] Zip entries={len(namelist)}", flush=True)
                zf.extractall(tmpdir)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Bad ZIP file (corrupted or incomplete)")
        except Exception as e:
            print(f"[UPLOAD] Extract failed: {repr(e)}", flush=True)
            raise HTTPException(status_code=400, detail=f"Unzip failed: {e}")

        # Helper function to process a single AlphaFold folder
        def process_one(path: Path, kind: str) -> schemas.ComplexOut:
            print(f"[PARSE] kind={kind} path={path}", flush=True)

            custom_chain_map = {}
            if filename_pattern and filename_pattern.strip():
                try:
                    regex_str = re.sub(r'\{([a-zA-Z0-9]+)\}', r'(?P<\1>[a-zA-Z0-9\-:,.]+)', filename_pattern.strip())
                    match = re.search(regex_str, path.name, re.IGNORECASE)
                    if match:
                        custom_chain_map = {k: v.upper() for k, v in match.groupdict().items()}
                except Exception as e:
                    print(f"[MAP] Error parsing regex: {e}", flush=True)

            if kind == "local":
                parser = LocalAlphaFoldParser(path)
                cif_name = next(path.glob("*_model.cif"), None)
                conf_name = next((p for p in Path(path).glob("*_confidences.json") if not p.name.endswith("_summary_confidences.json")), None)
                version = f"{parser.get_dialect()} - {parser.get_version()}"
            elif kind == "server":
                parser = ServerAlphaFoldParser(path)
                version = "alphafold-server"
                cif_name = next(path.glob("*_model_0.cif"), None)
                conf_name = next(path.glob("*_data_0.json"), None)
            else:
                raise ValueError(f"Unknown kind {kind}")

            if cif_name is None:
                raise ValueError(f"No .cif found in {path}")

            cif_bytes = cif_name.read_bytes()
            conf_bytes = conf_name.read_bytes() if conf_name else None

            iptm_val = parser.get_iptm()
            mean_scores = parser.get_mean_scores() if hasattr(parser, "get_mean_scores") else {"mean_iptm": None, "mean_ptm": None}
            num_seeds, models_per_seed = parser.get_num_seeds_and_samples()

            summary = dict(
                iptm=iptm_val, ptm=parser.get_ptm(), ranking_score=parser.get_ranking_score(),
                fraction_disordered=parser.get_fraction_disordered(), has_clash=parser.get_has_clash(),
                mean_iptm=mean_scores.get("mean_iptm"), mean_ptm=mean_scores.get("mean_ptm"),
            )

            meta = dict(
                submitted_from=submitted_from, version=version, submitted_seeds=num_seeds,
                submitted_models_per_seed=models_per_seed, description=description or None,
                mean_plddt=round(float(np.mean(parser.get_plddt_vector())), 2),
            )

            chains_payload: list[dict] = []
            for idx, cid in enumerate(parser.get_chain_ids()):
                sequence = parser.get_sequence(idx)
                chains_payload.append(dict(
                    sequence=sequence, sequence_length=len(sequence),
                    chain_iptm=np.round(parser.get_chain_iptm(idx), 2).tolist(),
                    chain_ptm=np.round(parser.get_chain_ptm(idx), 2).tolist(),
                    chain_pair_iptm=np.round(parser.get_chain_pair_iptm(idx), 2).tolist(),
                    chain_pair_pae_min=np.round(parser.get_chain_pair_pae_min(idx), 2).tolist(),
                    chain_mean_plddt=round(float(parser.get_chain_plddt(idx).mean()), 2),
                ))

            # Fast initial creation
            one_complex = crud.create_complex_initial(
                db, meta=meta, summary=summary, cif_bytes=cif_bytes, conf_bytes=conf_bytes, collection_name=collection_name
            )

            # Background tasks
            background_tasks.add_task(
                crud.process_complex_background,
                db=db, complex_id=one_complex.id, chains=chains_payload,
                custom_map=custom_chain_map, mapping_fallback_only=mapping_fallback_only
            )

            print(f"[PARSE] Created accession={one_complex.accession} (Processing in background)", flush=True)
            return one_complex

        # Scan for Output Folders
        output_dirs: list[tuple[Path, str]] = []
        root_local = (tmpdir / "ranking_scores.csv").exists()
        root_server = list(tmpdir.glob("*_full_data_0.json"))
        print(f"[SCAN] root_local={root_local} root_server={bool(root_server)}", flush=True)

        if root_local:
            output_dirs.append((tmpdir, "local"))
        elif root_server:
            output_dirs.append((tmpdir, "server"))
        else:
            # Recursive scan
            cnt = 0
            for p in tmpdir.rglob("*"):
                if p.is_dir():
                    if (p / "ranking_scores.csv").exists():
                        output_dirs.append((p, "local"))
                        cnt += 1
                    elif list(p.glob("*_full_data_0.json")):
                        output_dirs.append((p, "server"))
                        cnt += 1
            print(f"[SCAN] Found candidates={cnt}", flush=True)

        if not output_dirs:
            raise HTTPException(
                status_code=400,
                detail="No valid AlphaFold output found in ZIP: full_data_0.json or ranking_scores.csv missing",
            )

        complexes: list[schemas.ComplexOut] = []
        skipped = 0
        for od, kind in output_dirs:
            try:
                comp = process_one(od, kind)
                complexes.append(comp)
            except HTTPException:
                raise
            except Exception as e:
                skipped += 1
                print(f"[PARSE] ERROR in {od}: {repr(e)}", flush=True)
                traceback.print_exc()
                continue

        if not complexes:
            raise HTTPException(status_code=400, detail="No valid AlphaFold output found in ZIP")

        response.headers["X-Skipped"] = str(skipped)
        print(f"[DONE] Created={len(complexes)} Skipped={skipped}", flush=True)
        return complexes

    except HTTPException:
        raise
    except Exception as e:
        print(f"[FATAL] Unexpected error: {repr(e)}", flush=True)
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Internal error while processing upload")
    finally:
        # Cleanup
        try:
            if tmp_zip and os.path.exists(tmp_zip.name):
                os.remove(tmp_zip.name)
        except Exception as e:
            print(f"[CLEANUP] Zip remove failed: {e}", flush=True)
        try:
            if tmpdir and tmpdir.exists():
                shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception as e:
            print(f"[CLEANUP] Tmpdir remove failed: {e}", flush=True)


@router.post("/bulk_collection_update")
def bulk_collection_update(
        data: BulkCollectionUpdate,
        db: Session = Depends(get_db),
        admin: str = Depends(get_current_admin)
):
    """
    Moves complexes to an existing or new collection.
    Requires Admin Authentication.
    """
    # Target Collection
    target_coll_id = data.existing_collection_id

    if data.new_collection_name:
        existing = db.query(models.Collection).filter(models.Collection.name == data.new_collection_name).first()
        if existing:
            target_coll_id = existing.id
        else:
            new_coll = models.Collection(name=data.new_collection_name)
            db.add(new_coll)
            db.commit()
            db.refresh(new_coll)
            target_coll_id = new_coll.id

    if not target_coll_id:
        raise HTTPException(status_code=400, detail="No target collection specified.")

    # Update Complexes
    (
        db.query(models.Complex)
        .filter(models.Complex.accession.in_(data.accessions))
        .update({models.Complex.collection_id: target_coll_id}, synchronize_session=False)
    )

    db.commit()
    return {"status": "ok", "moved_count": len(data.accessions), "target_collection_id": target_coll_id}


@router.post("/bulk_delete")
def bulk_delete(
        accessions: List[str] = Body(...),
        db: Session = Depends(get_db),
        admin: str = Depends(get_current_admin)
):
    """
    Deletes multiple complexes from the database and disk.
    Requires Admin Authentication.
    Automatically removes collections if they become empty.
    """
    if not accessions:
        return {"status": "no items"}

    # Identify affected collections before deletion to check for cleanup later
    affected_coll_ids = (
        db.query(models.Complex.collection_id)
        .filter(models.Complex.accession.in_(accessions))
        .filter(models.Complex.collection_id.isnot(None))
        .distinct()
        .all()
    )
    coll_ids_to_check = [row[0] for row in affected_coll_ids]

    # Delete Complexes and Files
    comps = db.query(models.Complex).filter(models.Complex.accession.in_(accessions)).all()
    count = 0
    for c in comps:
        full = settings.storage_root / c.file_path
        if full.exists():
            shutil.rmtree(full, ignore_errors=True)
        db.delete(c)
        count += 1

    db.commit()

    # Delete empty collections
    deleted_collections = 0
    for cid in coll_ids_to_check:
        remaining = db.query(models.Complex).filter(models.Complex.collection_id == cid).count()
        if remaining == 0:
            db.query(models.Collection).filter(models.Collection.id == cid).delete()
            deleted_collections += 1

    if deleted_collections > 0:
        db.commit()

    return {"deleted": count, "deleted_collections": deleted_collections}


@router.delete("/collection/{name}", status_code=204)
def delete_collection(
        name: str,
        delete_content: bool = Query(False),  # False = Remove name only, True = Remove everything
        db: Session = Depends(get_db),
        admin: str = Depends(get_current_admin)
):
    """
    Deletes a collection.
    - If delete_content=False: Sets collection_id to NULL for contained complexes.
    - If delete_content=True: Deletes the collection AND all complexes (DB + Files) inside it.
    """
    coll = crud.get_collection_by_name(db, name)
    if not coll:
        raise HTTPException(404, "Collection not found")

    if delete_content:
        # Delete files and database entries for all complexes in the collection
        for comp in coll.complexes:
            full_path = settings.storage_root / comp.file_path
            if full_path.exists() and full_path.is_dir():
                try:
                    shutil.rmtree(full_path)
                except Exception as e:
                    print(f"[DELETE] Error removing folder {full_path}: {e}")

            db.delete(comp)

    # Delete the collection entry
    db.delete(coll)
    db.commit()

    return Response(status_code=204)


@router.post("/bulk_download")
def bulk_download(
        accessions: str = Form(...),
        part: str = Form("all"),
        db: Session = Depends(get_db)
):
    """
    Generates a ZIP file containing files for the requested accessions.
    """
    try:
        acc_list = json.loads(accessions)
    except:
        raise HTTPException(400, "Invalid accession list")

    comps = db.query(models.Complex).filter(models.Complex.accession.in_(acc_list)).all()
    if not comps:
        raise HTTPException(404, "No complexes found")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for comp in comps:
            folder = settings.storage_root / comp.file_path

            # Add CIF
            if part in ["all", "cif"]:
                cif = folder / "model.cif"
                if cif.exists():
                    zf.write(cif, arcname=f"{comp.accession}_model.cif")

            # Add JSON
            if part in ["all", "confidences"]:
                conf = folder / "confidences.json"
                if conf.exists():
                    zf.write(conf, arcname=f"{comp.accession}_confidences.json")

    zip_buffer.seek(0)

    filename = f"selection_{part}.zip"
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/upload", summary="Upload AlphaFold3 ZIP bundle",
             description="""
Uploads a ZIP archive with one or more AlphaFold3 output folders.
Both AlphaFold server and local AlphaFold outputs are recognized.

Requirement: Files must be named according to the standard AlphaFold output naming convention.
""")
async def submit_form(
        request: Request,
        background_tasks: BackgroundTasks,
        submitted_from: str = Form(...),
        description: str = Form(...),
        collection_name: str = Form(...),
        filename_pattern: str | None = Form(None),
        mapping_fallback_only: bool = Form(False),
        bundle: UploadFile = File(...),
        admin_user: str = Depends(get_current_admin),
        db: Session = Depends(get_db),
):
    """
    Public form endpoint to upload a ZIP bundle.
    Delegates logic to submit_complex.
    """
    try:
        dummy_resp = Response()

        # Pass background_tasks down to submit_complex
        comps = await submit_complex(
            request=request,
            response=dummy_resp,
            background_tasks=background_tasks,
            submitted_from=submitted_from,
            bundle=bundle,
            description=description,
            collection_name=collection_name,
            filename_pattern=filename_pattern,
            mapping_fallback_only=mapping_fallback_only,
            db=db,
        )

        if isinstance(comps, list) and comps:
            accession_msg = ", ".join([c.accession for c in comps])
        else:
            accession_msg = getattr(comps, "accession", "-")

        return JSONResponse({"success": True, "accessions": accession_msg, "message": "Processing in background."})

    except HTTPException as e:
        print(f"[FORM][HTTP {e.status_code}] {e.detail}", flush=True)
        return JSONResponse({"success": False, "detail": e.detail}, status_code=e.status_code)

    except Exception as e:
        print(f"[FORM][FATAL] Unexpected error: {repr(e)}", flush=True)
        traceback.print_exc(file=sys.stdout)
        return JSONResponse(
            {"success": False, "detail": "Error processing upload. Please check form data."},
            status_code=500,
        )


@router.get("/", response_model=list[schemas.ComplexOut], summary="List last 50 complexes",
            description="Lists the 50 last submitted complexes.")
def list_complexes(db: Session = Depends(get_db)):
    """
    Returns the last 50 complexes added to the database.
    """
    return crud.list_complexes(db)


@router.get("/{accession}", response_model=schemas.ComplexOut, summary="Get metadata for complex",
            description="Returns metadata and chain information for the specified complex accession.")
def get_complex(accession: str, db: Session = Depends(get_db)):
    """
    Returns full metadata for a single complex.
    """
    comp = crud.get_complex_by_accession(db, accession)
    if not comp:
        raise HTTPException(404)
    return comp


@router.get("/{accession}/structure", summary="Get .cif Structure",
            description="Returns the 3D molecular structure (mmCIF format).")
def get_structure(accession: str, db: Session = Depends(get_db)):
    """
    Streams the model.cif file for a complex.
    """
    c = crud.get_complex_by_accession(db, accession)
    if not c:
        raise HTTPException(404)
    cif_path = Path(settings.storage_root) / c.file_path / "model.cif"
    if not cif_path.exists():
        raise HTTPException(404, "mmCIF not found")
    return FileResponse(cif_path, media_type="chemical/x-mmcif")


@router.get("/{accession}/confidences", summary="Get confidence JSON",
            description="Returns the per-residue and pairwise confidence information (JSON).")
def get_confidences(accession: str, db: Session = Depends(get_db)):
    """
    Streams the confidences.json file for a complex.
    """
    c = crud.get_complex_by_accession(db, accession)
    if not c:
        raise HTTPException(404)

    json_path = Path(settings.storage_root) / c.file_path / "confidences.json"
    if not json_path.exists():
        raise HTTPException(404, "confidences.json not found")

    return FileResponse(json_path, media_type="application/json")
