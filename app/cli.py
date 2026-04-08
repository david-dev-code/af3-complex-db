#!/usr/bin/env python3
"""
AF3-DB Maintenance CLI
"""

from __future__ import annotations
import os
import shutil
import sys
import re
import tempfile
import zipfile
import tarfile
import warnings
from pathlib import Path
from typing import Generator


warnings.filterwarnings("ignore", category=UserWarning, module="biotite")
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

import numpy as np
import typer
from sqlalchemy import text
from sqlalchemy.orm import Session
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.core.database import _SessionLocal as SessionLocal
from app import models, crud
from app.local_alphafold_parser.alphafold_parser import AlphaFoldParser as LocalAlphaFoldParser
from app.server_alphafold_parser.alphafold_parser import AlphaFoldParser as ServerAlphaFoldParser

os.umask(0o002)
settings = get_settings()
STORAGE_ROOT = Path(settings.storage_root).resolve()
STATIC_DIR = STORAGE_ROOT / "static"

app = typer.Typer(help="AF3-DB Container CLI", rich_markup_mode="rich", add_completion=False, no_args_is_help=True)
console = Console()


def _db() -> Generator[Session, None, None]:
    """Yields a database session and ensures it is closed afterwards."""
    if SessionLocal is None:
        console.print("[bold red]❌  DB not initialized (DATABASE_URL not set).[/bold red]")
        sys.exit(2)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _safe_rmdir(rel_path: str | Path):
    """Safely removes a directory within the storage root, protecting the static folder."""
    if not rel_path:
        return
    abs_path = (STORAGE_ROOT / rel_path).resolve()
    if abs_path == STATIC_DIR or STATIC_DIR in abs_path.parents:
        console.print("[bold magenta]⚠  static/ will not be removed.[/bold magenta]")
        return
    if abs_path.exists() and abs_path.is_dir():
        shutil.rmtree(abs_path)
        console.print(f"🗑  removed {abs_path.relative_to(STORAGE_ROOT)}")


def _wipe_storage_except_static():
    """Deletes all folders in the storage root except the static directory."""
    for p in STORAGE_ROOT.iterdir():
        if p == STATIC_DIR or not p.is_dir():
            continue
        shutil.rmtree(p)
    console.print("[bold green]🗑  Storage wiped.[/bold green]")


def _parse_filename_pattern(pattern: str, filename: str) -> dict[str, str]:
    """Parses a filename against a given regex pattern to extract chain mappings."""
    if not pattern or not pattern.strip():
        return {}
    try:
        regex_str = re.sub(
            r'\{([a-zA-Z0-9]+)\}',
            r'(?P<\1>[a-zA-Z0-9\-\+.,_]+)',
            pattern.strip()
        )
        match = re.search(regex_str, filename, re.IGNORECASE)
        if match:
            return {k: v.upper() for k, v in match.groupdict().items()}
    except Exception as e:
        console.print(f"[bold red]Error parsing regex pattern:[/bold red] {e}")
    return {}


@app.command()
def delete_complex(accession: str = typer.Argument(..., help="AF-CP-NNNNN")):
    """Deletes a single complex from the database and storage."""
    db = next(_db())
    comp = db.query(models.Complex).filter(models.Complex.accession == accession).first()
    if not comp:
        console.print(f"[bold red]❌ Complex {accession} not found.[/bold red]")
        raise typer.Exit(code=1)
    _safe_rmdir(comp.file_path)
    db.query(models.Chain).filter(models.Chain.complex_id == comp.id).delete()
    db.delete(comp)
    db.commit()
    console.print(f"[bold green]✅ Complex {accession} deleted[/bold green]")


@app.command("delete-collection")
def delete_collection(name: str = typer.Argument(...), yes: bool = typer.Option(False, "-y")):
    """Deletes an entire collection and all its associated complexes."""
    db = next(_db())
    coll = db.query(models.Collection).filter(models.Collection.name == name).first()
    if not coll:
        console.print(f"[bold red]❌ Collection '{name}' not found.[/bold red]")
        raise typer.Exit(code=1)
    complexes = db.query(models.Complex).filter(models.Complex.collection_id == coll.id).all()

    if not yes:
        console.print(f"Found {len(complexes)} complexes.")
        if not typer.confirm(f"⚠  DELETE collection '{name}'?"): raise typer.Abort()

    for comp in complexes:
        _safe_rmdir(comp.file_path)
        db.delete(comp)
    db.delete(coll)
    db.commit()
    console.print(f"[bold green]✅ Collection '{name}' removed.[/bold green]")


@app.command()
def purge_db(yes: bool = typer.Option(False, "-y")):
    """Completely resets the database and wipes all stored files."""
    if not yes and not typer.confirm("⚠  DELETE ALL DATA?"): raise typer.Abort()
    _wipe_storage_except_static()
    db = next(_db())
    for t in ["uniprot_accession", "chain", "interface_score", "complex", "uniparc_entry", "collection"]:
        try:
            db.execute(text(f"TRUNCATE TABLE {t} CASCADE"))
        except:
            pass
    db.commit()
    console.print("[bold green]DB Cleaned.[/bold green]")


def _detect_kind(dirpath: Path) -> str | None:
    """Detects whether a directory contains local or server AlphaFold outputs."""
    if (dirpath / "ranking_scores.csv").exists(): return "local"
    if list(dirpath.glob("*_full_data_0.json")): return "server"
    return None


def _round_array(arr) -> list[float] | float | None:
    """Rounds a numpy array to two decimal places, converts to list, and sanitizes NaNs."""
    if arr is None:
        return None

    arr_np = np.array(arr, dtype=float)
    sanitized = np.where(np.isnan(arr_np), None, np.round(arr_np, 2))
    return sanitized.tolist()


def _extract_archive(archive_path: Path, extract_to: Path) -> bool:
    """Extracts a zip or tar archive into the specified directory."""
    s = archive_path.suffix.lower()
    try:
        if s == ".zip":
            with zipfile.ZipFile(archive_path, 'r') as z:
                z.extractall(extract_to)
        elif s == ".tar" or archive_path.name.endswith(".tar.gz") or archive_path.name.endswith(".tgz"):
            mode = "r:gz" if (archive_path.name.endswith("gz")) else "r:"
            with tarfile.open(archive_path, mode) as t:
                t.extractall(extract_to)
        else:
            return False
        return True
    except Exception as e:
        console.print(f"[bold red]Failed to extract {archive_path.name}: {e}[/bold red]")
        return False


def _ingest_single_run(
        db: Session,
        path: Path,
        kind: str,
        *,
        submitted_from: str,
        description: str | None,
        collection_name: str | None,
        filename_pattern: str | None,
        mapping_fallback_only: bool,
        original_archive_name: str | None = None
) -> models.Complex:
    """Parses and ingests a single AlphaFold run directory into the database."""
    scan_name = original_archive_name if original_archive_name else path.name

    for ext in [".zip", ".tar.gz", ".tgz", ".tar"]:
        if scan_name.lower().endswith(ext):
            scan_name = scan_name[:-len(ext)]
            break

    custom_map = _parse_filename_pattern(filename_pattern, scan_name)
    if custom_map:
        console.print(f"   Map: {custom_map}")

    if kind == "local":
        parser = LocalAlphaFoldParser(path)
        version = f"{parser.get_dialect()}"
        cif_name = next(path.glob("*_model.cif"), None)
        conf_name = next((p for p in path.glob("*_confidences.json") if "summary" not in p.name), None)
    else:
        parser = ServerAlphaFoldParser(path)
        version = "alphafold-server"
        cif_name = next(path.glob("*_model_0.cif"), None)
        conf_name = next(path.glob("*_data_0.json"), None)

    if not cif_name: raise ValueError("No CIF found")

    cif_bytes = cif_name.read_bytes()
    conf_bytes = conf_name.read_bytes() if conf_name else None

    mean_scores = {"mean_iptm": None, "mean_ptm": None}
    if hasattr(parser, "get_mean_scores"):
        mean_scores = parser.get_mean_scores()

    summary = dict(
        iptm=parser.get_iptm(), ptm=parser.get_ptm(),
        ranking_score=parser.get_ranking_score(), fraction_disordered=parser.get_fraction_disordered(),
        has_clash=parser.get_has_clash(),
        mean_iptm=mean_scores.get("mean_iptm"), mean_ptm=mean_scores.get("mean_ptm"),
    )

    n_seeds, n_models = parser.get_num_seeds_and_samples()
    meta = dict(
        submitted_from=submitted_from, version=version,
        submitted_seeds=n_seeds, submitted_models_per_seed=n_models,
        description=description, mean_plddt=round(float(np.mean(parser.get_plddt_vector())), 2),
    )

    chains_payload = []
    for idx, _ in enumerate(parser.get_chain_ids()):
        seq = parser.get_sequence(idx)
        plddt_arr = parser.get_chain_plddt(idx)
        chain_mean_plddt = round(float(plddt_arr.mean()), 2) if len(plddt_arr) > 0 else 0.0

        chains_payload.append(dict(
            sequence=seq, sequence_length=len(seq),
            chain_iptm=_round_array(parser.get_chain_iptm(idx)),
            chain_ptm=_round_array(parser.get_chain_ptm(idx)),
            chain_pair_iptm=_round_array(parser.get_chain_pair_iptm(idx)),
            chain_pair_pae_min=_round_array(parser.get_chain_pair_pae_min(idx)),
            chain_mean_plddt=chain_mean_plddt,
        ))

    comp = crud.create_complex_initial(
        db, meta=meta, summary=summary,
        cif_bytes=cif_bytes, conf_bytes=conf_bytes,
        collection_name=collection_name
    )

    crud.process_complex_background(
        db, complex_id=comp.id, chains=chains_payload,
        custom_map=custom_map, mapping_fallback_only=mapping_fallback_only
    )

    db.refresh(comp)

    if comp.processing_status == "FAILED":
        _safe_rmdir(comp.file_path)
        db.delete(comp)
        db.commit()
        raise RuntimeError("Database processing failed due to internal parsing error.")

    return comp


@app.command("upload-folder")
def upload_folder(
        path: Path = typer.Argument(
            ..., exists=True, resolve_path=True, help="Path to AF3 folders or archives (.zip/.tar)."
        ),
        submitted_from: str = typer.Option(
            "CLI-User", "--submitted-from", help="Identifier for the uploader (e.g., username or 'CLI')."
        ),
        description: str = typer.Option(
            None, "--description", "-d", help="Optional description applied to all uploaded complexes."
        ),
        collection_name: str = typer.Option(
            None, "--collection", "-c", help="Name of the collection to group these complexes into."
        ),
        filename_pattern: str = typer.Option(
            None, "--pattern", "-p",
            help="Regex pattern to extract chain names from the folder or archive name, supporting modification notes with '+' (e.g., '{A}_{B}')."
        ),
        fallback_only: bool = typer.Option(
            False, "--fallback-only",
            help="Skip automatic UniProt search entirely if the custom pattern yields a valid match (UniProt Search is only the fallback)"
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Scan and list files to be processed without actually importing them."
        ),
):
    """
    Recursively scans a path for AF3 folders or archives and ingests them.

    [bold]Examples:[/bold]

    1. Simple upload of a folder or archive :
       [cyan]af3-db upload-folder ./my_protein_run.zip[/cyan]

    2. Advanced upload of a directory with a custom regex pattern:
       (Assigning them to a collection, adding a description, and using a custom regex pattern
       to map chains from folder names like 'P04637-2+phosphorylated_Q00987' and using UniProt mapping only as fallback for faster uploading)
       [cyan]af3-db upload-folder ./af3_results_batch/ -c "p53 Interaction Project" -d "Mutant screen" -p "{A}_{B}" --fallback-only[/cyan]
    """
    console.rule("[bold blue]AF3-DB Upload")

    real_folders_to_process = []
    archives_to_process = []

    if path.is_file():
        if path.suffix in [".zip", ".tar", ".gz", ".tgz"]:
            archives_to_process.append(path)
        else:
            console.print("[bold red]File provided is not a supported archive.[/bold red]")
            raise typer.Exit(1)
    else:
        for root, dirs, files in os.walk(path):
            root_p = Path(root)
            if _detect_kind(root_p):
                real_folders_to_process.append(root_p)
                dirs[:] = []
                continue

            for f in files:
                if f.endswith((".zip", ".tar", ".tar.gz", ".tgz")):
                    archives_to_process.append(root_p / f)

    total_items = len(real_folders_to_process) + len(archives_to_process)
    if total_items == 0:
        console.print("[bold magenta]No AF3 output folders or archives found.[/bold magenta]")
        raise typer.Exit()

    console.print(f"Found [bold]{len(real_folders_to_process)}[/bold] direct folders.")
    console.print(f"Found [bold]{len(archives_to_process)}[/bold] archives to extract.")

    if dry_run:
        raise typer.Exit()

    db = next(_db())
    success = 0

    with Progress(
            SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
            BarColumn(), TextColumn("{task.completed}/{task.total}")
    ) as p:

        if real_folders_to_process:
            t1 = p.add_task("Uploading Folders...", total=len(real_folders_to_process))
            for folder in real_folders_to_process:
                p.update(t1, description=f"Folder: [bold blue]{folder.name}[/bold blue]")
                try:
                    kind = _detect_kind(folder)
                    _ingest_single_run(
                        db, folder, kind,
                        submitted_from=submitted_from, description=description,
                        collection_name=collection_name, filename_pattern=filename_pattern,
                        mapping_fallback_only=fallback_only
                    )
                    success += 1
                except Exception as e:
                    console.print(f"[bold red]Failed {folder.name}: {e}[/bold red]")
                p.advance(t1)

        if archives_to_process:
            t2 = p.add_task("Processing Archives...", total=len(archives_to_process))
            for arch in archives_to_process:
                p.update(t2, description=f"Archive: [bold magenta]{arch.name}[/bold magenta]")
                with tempfile.TemporaryDirectory() as tmp_dir:
                    extracted_root = Path(tmp_dir)
                    if _extract_archive(arch, extracted_root):
                        found_in_zip = []
                        for root, dirs, _ in os.walk(extracted_root):
                            rp = Path(root)
                            k = _detect_kind(rp)
                            if k:
                                found_in_zip.append((rp, k))
                                dirs[:] = []

                        for inner_path, kind in found_in_zip:
                            try:
                                _ingest_single_run(
                                    db, inner_path, kind,
                                    submitted_from=submitted_from, description=description,
                                    collection_name=collection_name, filename_pattern=filename_pattern,
                                    mapping_fallback_only=fallback_only,
                                    original_archive_name=arch.name
                                )
                                success += 1
                            except Exception as e:
                                console.print(f"[bold red]Failed inside zip {inner_path.name}: {e}[/bold red]")
                p.advance(t2)

    db.commit()
    console.print(f"[bold green]Done! Successfully processed {success} complexes.[/bold green]")


if __name__ == "__main__":
    app(prog_name="af3-db")