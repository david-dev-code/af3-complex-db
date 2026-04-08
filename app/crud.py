from __future__ import annotations

import traceback
import re

from pathlib import Path
from typing import List, Optional
from fastapi.params import Form as _FormParam
from collections import Counter, defaultdict
from sqlalchemy import and_, cast, desc, exists, func, or_, select, String, Float
from sqlalchemy.orm import aliased, joinedload, Session
from sqlalchemy.sql import true as sql_true
from app import models, schemas
from app.core.config import settings
from app.models import Chain, Collection, Complex, UniParcEntry, UniprotAccession
from app.server_alphafold_parser.cif_extractor import CifExtractor
from app.services.biophysics import compute_biophysical_stats
from app.services.ipsae import compute_ipsae_scores_multi
from app.services.uniprot_mapping import query_uniparc, query_uniprot_details

# Constants
REVIEWED_FLAG = "UniProtKB reviewed (Swiss-Prot)"
PRIO_ORG = {
    "Homo sapiens": 0,
    "Mus musculus": 1,
    "Drosophila melanogaster": 2,
}

# Helpers

def _next_accession(db: Session) -> str:
    """Generates the next sequential AF-CP accession ID."""
    last = db.query(models.Complex).order_by(models.Complex.id.desc()).first()
    idx = (last.id + 1) if last else 1
    return f"AF-CP-{idx:05d}"


def _store_file(folder: Path, filename: str, raw: bytes) -> str:
    """Saves raw bytes to a file and returns the relative path from storage root."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    path.write_bytes(raw)
    return str(path.relative_to(settings.storage_root))


def get_cif_path_by_accession(db: Session, accession: str) -> Path | None:
    """
    Returns the absolute path to the `model.cif` file of the specified Complex,
    or None if the complex does not exist.
    """
    rel_path = (
        db.query(models.Complex.file_path)
        .filter(models.Complex.accession == accession)
        .scalar()
    )
    if rel_path is None:
        return None
    return Path(settings.storage_root) / rel_path / "model.cif"


def add_chain_mappings(db: Session, chain: Chain, upi: str, accessions: list[str]) -> None:
    """
    Links a chain to a UniParc entry and adds associated UniProt accessions
    if they do not already exist in the database.
    """
    if not upi:
        return

    entry = db.query(UniParcEntry).filter_by(upi=upi).first()
    if entry is None:
        entry = UniParcEntry(upi=upi)
        db.add(entry)
        db.flush()

    # Link chain to UPI
    chain.upi_id = entry.id
    db.flush()

    # Deduplicate via UPI, not via Chain
    existing = {ua.accession for ua in entry.accessions}

    for ac in accessions:
        if ac in existing:
            continue

        meta = query_uniprot_details(ac) or {}

        ua = UniprotAccession(
            upi_id=entry.id,
            accession=ac,
            status=meta.get("status"),
            protein_name=meta.get("protein_name"),
            alternative_names=meta.get("alternative_names"),
            gene_name=meta.get("gene_name"),
            function=meta.get("function"),
            organism=meta.get("organism"),
            taxonomy=meta.get("taxonomy"),
        )
        entry.accessions.append(ua)

    db.flush()


def _get_or_create_collection(db: Session, name: str | None):
    """Retrieves an existing collection by name or creates a new one."""
    if not name or isinstance(name, _FormParam):
        return None

    name = name.strip()
    if not name:
        return None

    entry = db.query(Collection).filter_by(name=name).first()
    if not entry:
        entry = Collection(name=name)
        db.add(entry)
        db.flush()
    return entry.id


def _organism_rank(org: str | None) -> int:
    """Returns an integer rank prioritizing specific model organisms."""
    return PRIO_ORG.get(org or "", 3)


def _determine_best_entry(chain: models.Chain) -> tuple[str | None, str | None, str | None]:
    """
    Determines the "best" UniProt accession for a chain in auto-mode.
    Logic priority:
    1. Most frequent protein name among associated accessions.
    2. 'Reviewed' status preferred.
    3. Organism ranking.
    4. Alphabetical sort.

    Returns: (primary_accession, protein_name, gene_name)
    """
    if not chain.uniparc or not chain.uniparc.accessions:
        return None, None, None

    entries = chain.uniparc.accessions

    # Count frequency of protein names
    name_counts = Counter()
    for ua in entries:
        if ua.protein_name:
            name_counts[ua.protein_name] += 1

    if not name_counts:
        # Fallback to the first available (preferring reviewed)
        best = sorted(entries, key=lambda x: (0 if x.status and REVIEWED_FLAG in x.status else 1, x.accession))[0]
        return best.accession, best.protein_name, best.gene_name

    max_freq = max(name_counts.values())
    candidate_names = {n for n, c in name_counts.items() if c == max_freq}
    candidates = [ua for ua in entries if ua.protein_name in candidate_names]

    def rank_key(ua):
        is_reviewed = 0 if (ua.status and REVIEWED_FLAG in ua.status) else 1
        org_r = _organism_rank(ua.organism)
        return (is_reviewed, org_r, ua.accession)

    best_ua = sorted(candidates, key=rank_key)[0]
    return best_ua.accession, best_ua.protein_name, best_ua.gene_name


def _best_protein_name(chain: models.Chain) -> str:
    """
    Deterministically returns the 'best' protein name for a chain based on its mappings.
    Falls back to UPI or '–'.
    """
    counts = Counter()
    reviewed = defaultdict(bool)

    accessions = chain.uniparc.accessions if chain.uniparc else []
    for ua in accessions:
        if ua.protein_name:
            counts[ua.protein_name] += 1
            if ua.status and REVIEWED_FLAG in ua.status:
                reviewed[ua.protein_name] = True

    if counts:
        max_freq = max(counts.values())
        candidates = [n for n, c in counts.items() if c == max_freq]

        reviewed_cands = [n for n in candidates if reviewed[n]]
        if reviewed_cands:
            candidates = reviewed_cands

        return sorted(candidates)[0]

    return chain.uniparc.upi if chain.uniparc else "–"


# Main Logic

def update_complex_full(
        db: Session,
        accession: str,
        description: str,
        collection_id: str | int | None,
        new_collection_name: str | None,
        chain_updates: dict,
) -> models.Complex | None:
    """
    Updates a complex's metadata, collection assignment, and specific chain attributes.
    """
    comp = get_complex_by_accession(db, accession)
    if not comp:
        return None

    comp.description = description.strip() if description else None

    # Handle Collection logic
    final_coll_id = None
    if new_collection_name and new_collection_name.strip():
        clean_name = new_collection_name.strip()
        existing_coll = db.query(models.Collection).filter_by(name=clean_name).first()
        if existing_coll:
            final_coll_id = existing_coll.id
        else:
            new_coll = models.Collection(name=clean_name)
            db.add(new_coll)
            db.flush()
            final_coll_id = new_coll.id
    elif collection_id and str(collection_id) != "__none__":
        final_coll_id = int(collection_id)

    comp.collection_id = final_coll_id

    # Update chain-level metadata
    for chain in comp.chains:
        c_upd = chain_updates.get(chain.id)
        if c_upd:
            if 'gene_name' in c_upd:
                chain.gene_name = c_upd['gene_name'].strip() or None
            if 'protein_name' in c_upd:
                chain.protein_name = c_upd['protein_name'].strip() or None

            new_primary = c_upd.get('primary_ac')
            if new_primary:
                if new_primary == "__none__":
                    if chain.primary_accession is not None:
                        chain.primary_accession = None
                        chain.mapping_method = "manual"
                elif new_primary != chain.primary_accession:
                    chain.primary_accession = new_primary
                    chain.mapping_method = "manual"

    db.commit()
    db.refresh(comp)

    # Reattach virtual summary attributes
    from app.crud import _attach_summary_names
    _attach_summary_names([comp])

    return comp


def create_complex_initial(
        db: Session,
        *,
        meta: dict,
        summary: dict,
        cif_bytes: bytes,
        conf_bytes: bytes,
        collection_name: str | None = None,
) -> models.Complex:
    """
    Fast, synchronous phase of complex creation.
    Saves files to disk and creates the initial DB record.
    """
    accession = _next_accession(db)
    comp_dir = settings.storage_root / accession
    comp_dir.mkdir(parents=True, exist_ok=True)

    # Write files to disk
    cif_path = comp_dir / "model.cif"
    cif_path.write_bytes(cif_bytes)
    if conf_bytes:
        (comp_dir / "confidences.json").write_bytes(conf_bytes)

    coll_id = _get_or_create_collection(db, collection_name)

    comp = models.Complex(
        accession=accession,
        file_path=str(comp_dir.relative_to(settings.storage_root)),
        collection_id=coll_id,
        processing_status="PROCESSING",
        **meta,
        **summary,
    )
    db.add(comp)
    db.commit()
    db.refresh(comp)

    return comp


def process_complex_background(
        db: Session,
        complex_id: int,
        chains: list[dict],
        custom_map: dict[str, str] | None = None,
        mapping_fallback_only: bool = False,
):
    """
    Heavy, asynchronous phase of complex creation designed to run in a BackgroundTask.
    Handles CIF parsing, UniProt API mapping, ipSAE computation, and biophysical stats.
    """
    comp = db.query(models.Complex).filter(models.Complex.id == complex_id).first()
    if not comp:
        print(f"[BACKGROUND] Error: Complex {complex_id} not found.", flush=True)
        return

    comp_dir = settings.storage_root / comp.file_path
    cif_path = comp_dir / "model.cif"
    conf_path = comp_dir / "confidences.json"

    print(f"[BACKGROUND] Starting processing for {comp.accession}", flush=True)

    try:
        # Extract CIF metrics
        extractor = CifExtractor(cif_path)
        cif_chain_ids = extractor.get_chain_ids()

        STANDARD_RADII = [5.0, 10.0, 15.0]
        radius_map = extractor.compute_radius_plddt(STANDARD_RADII)

        for idx, ch in enumerate(chains):
            sequence = ch.get("sequence")

            # Deduplication
            existing_chain = db.query(models.Chain).filter(models.Chain.sequence == sequence).first()

            if existing_chain and existing_chain.upi_id:
                new_chain = models.Chain(complex_id=comp.id, upi_id=existing_chain.upi_id, **ch)
            else:
                new_chain = models.Chain(complex_id=comp.id, **ch)

            db.add(new_chain)
            db.flush()

            cid = None
            if idx < len(cif_chain_ids):
                cid = cif_chain_ids[idx]
                new_chain.residue_plddt = extractor.get_residue_plddt(cid)
                if cid in radius_map:
                    new_chain.radius_plddt = radius_map[cid]

            # Mapping Logic
            forced_raw = custom_map.get(cid) if (custom_map and cid) else None
            forced_ac = None
            modifier = None

            if forced_raw:
                if "+" in forced_raw:
                    forced_ac, modifier = forced_raw.split("+", 1)
                else:
                    forced_ac = forced_raw

            run_auto_search = False
            meta_ac_manual = None

            if forced_ac:
                meta_ac_manual = query_uniprot_details(forced_ac)

                if meta_ac_manual:
                    new_chain.mapping_method = "manual"
                    new_chain.primary_accession = forced_ac
                    new_chain.protein_name = (meta_ac_manual.get("protein_name") or forced_ac)
                    new_chain.gene_name = (meta_ac_manual.get("gene_name") or "-")
                    run_auto_search = not mapping_fallback_only
                else:
                    print(f"[MAPPING] Accession '{forced_ac}' not found. Falling back to automatic search.", flush=True)
                    run_auto_search = True
            else:
                run_auto_search = True

            upi_str, auto_accessions = query_uniparc(new_chain.sequence)
            accessions_to_save = []

            if run_auto_search:
                accessions_to_save.extend(auto_accessions)

            if forced_ac and meta_ac_manual and forced_ac not in accessions_to_save:
                accessions_to_save.append(forced_ac)

            add_chain_mappings(db, new_chain, upi_str, accessions_to_save)

            if forced_ac and meta_ac_manual and new_chain.upi_id:
                exists = db.query(models.UniprotAccession).filter_by(upi_id=new_chain.upi_id,
                                                                     accession=forced_ac).first()
                if not exists:
                    ua = models.UniprotAccession(
                        upi_id=new_chain.upi_id, accession=forced_ac,
                        status=meta_ac_manual.get("status"), protein_name=meta_ac_manual.get("protein_name"),
                        alternative_names=meta_ac_manual.get("alternative_names"),
                        gene_name=meta_ac_manual.get("gene_name"),
                        function=meta_ac_manual.get("function"), organism=meta_ac_manual.get("organism"),
                        taxonomy=meta_ac_manual.get("taxonomy"),
                    )
                    db.add(ua)
                    db.flush()

            if not new_chain.mapping_method:
                new_chain.mapping_method = "auto"
                best_ac, best_prot, best_gene = _determine_best_entry(new_chain)
                if best_ac:
                    new_chain.primary_accession = best_ac
                    new_chain.protein_name = best_prot
                    new_chain.gene_name = best_gene
                else:
                    new_chain.primary_accession = None

                    new_chain.protein_name = forced_ac if forced_ac else (
                        new_chain.uniparc.upi if new_chain.uniparc else "–")
                    new_chain.gene_name = forced_ac if forced_ac else "–"

            if modifier:
                if new_chain.protein_name and new_chain.protein_name != "–":
                    new_chain.protein_name = f"{new_chain.protein_name} [{modifier}]"
                elif forced_ac:
                    new_chain.protein_name = f"{forced_ac} [{modifier}]"

                if new_chain.gene_name and new_chain.gene_name != "–":
                    new_chain.gene_name = f"{new_chain.gene_name} [{modifier}]"
                elif forced_ac:
                    new_chain.gene_name = f"{forced_ac} [{modifier}]"

        #  Precompute ipSAE Interface Scores
        if conf_path.exists():
            try:
                cutoffs = [3.0, 5.0, 10.0, 15.0, 20.0]
                pair_rows, summary_rows = compute_ipsae_scores_multi(conf_path, cif_path, pae_cutoffs=cutoffs)

                for r in pair_rows:
                    db.add(models.InterfaceScore(
                        complex_id=comp.id, chain1=r["chain1"], chain2=r["chain2"],
                        pae_cutoff=float(r["pae_cutoff"]),
                        ipsae=r.get("ipsae"), ipsae_d0chn=r.get("ipsae_d0chn"), ipsae_d0dom=r.get("ipsae_d0dom"),
                        iptm_d0chn=r.get("iptm_d0chn"), pdockq=r.get("pdockq"), pdockq2=r.get("pdockq2"), lis=r.get("lis"),
                        n0res=r.get("n0res"), n0chn=r.get("n0chn"), n0dom=r.get("n0dom"),
                        d0res=r.get("d0res"), d0chn=r.get("d0chn"), d0dom=r.get("d0dom"),
                        nres1=r.get("nres1"), nres2=r.get("nres2"), dist1=r.get("dist1"), dist2=r.get("dist2")
                    ))

                # Constant variables independent of PAE
                if summary_rows:
                    comp.pdockq = summary_rows[0].get("pdockq")
                    comp.pdockq2 = summary_rows[0].get("pdockq2")
                    comp.lis = summary_rows[0].get("lis")

                for s in summary_rows:
                    pae = int(s["pae_cutoff"])
                    suffix = str(pae)

                    def set_f(attr, val):
                        if val is not None: setattr(comp, f"{attr}_{suffix}", float(val))

                    set_f("ipsae", s.get("ipsae"))
                    set_f("ipsae_d0chn", s.get("ipsae_d0chn"))
                    set_f("ipsae_d0dom", s.get("ipsae_d0dom"))
                    set_f("iptm_d0chn", s.get("iptm_d0chn"))

                    bp = s.get("best_pair")
                    if bp:
                        setattr(comp, f"ipsae_best_pair_{suffix}", bp)

            except Exception as e:
                print(f"[BACKGROUND] ipSAE compute failed: {repr(e)}", flush=True)

        # Precompute Biophysical Properties
        try:
            phys_pairs, _ = compute_biophysical_stats(cif_path)
            total_bsa = sum(pp.get("bsa", 0.0) for pp in phys_pairs)
            total_hb = sum(pp.get("num_h_bonds", 0) for pp in phys_pairs)
            total_sb = sum(pp.get("num_salt_bridges", 0) for pp in phys_pairs)

            comp.bsa = float(total_bsa) if total_bsa is not None else None
            comp.num_h_bonds = int(total_hb) if total_hb is not None else None
            comp.num_salt_bridges = int(total_sb) if total_sb is not None else None

        except Exception as e:
            print(f"[BACKGROUND] Biophysics compute failed: {repr(e)}", flush=True)

        comp.processing_status = "SUCCESS"
        db.commit()
        print(f"[BACKGROUND] Completed processing for {comp.accession}", flush=True)

    except Exception as e:
        db.rollback()
        failed_comp = db.query(models.Complex).filter(models.Complex.id == complex_id).first()

        if failed_comp:
            failed_comp.processing_status = "FAILED"
            db.commit()

        print(f"[BACKGROUND] Fatal Error processing {complex_id}: {e}", flush=True)
        traceback.print_exc()


def _organism_rank(org: str | None) -> int:
    """Returns an integer rank. Lower value = higher priority."""
    return PRIO_ORG.get(org or "", 3)


def _best_gene_name(chain: models.Chain) -> str:
    """
    Determines the best gene name for a chain based on mapping metadata.
    Priority logic:
    1. Most frequent gene name.
    2. LOC... genes are deprioritized.
    3. Organism priority (Human < Mouse < Fly < Rest).
    4. Reviewed entries preferred.
    5. Alphabetical fallback.
    Returns the selected gene name, UPI, or '–'.
    """
    counts = Counter()
    reviewed = defaultdict(bool)
    best_org = defaultdict(lambda: 3)

    accessions = chain.uniparc.accessions if chain.uniparc else []
    for ua in accessions:
        if ua.gene_name:
            g = ua.gene_name
            counts[g] += 1
            if ua.status and REVIEWED_FLAG in ua.status:
                reviewed[g] = True
            best_org[g] = min(best_org[g], _organism_rank(ua.organism))

    if not counts:
        return chain.uniparc.upi if chain.uniparc else "–"

    # 1. Most frequent
    max_freq = max(counts.values())
    cand = [g for g, c in counts.items() if c == max_freq]

    # 2. Deprioritize LOC genes
    non_loc = [g for g in cand if not g.startswith("LOC")]
    cand = non_loc or cand

    # 3. Organism ranking
    min_rank = min(best_org[g] for g in cand)
    cand = [g for g in cand if best_org[g] == min_rank]

    # 4. Prefer reviewed
    rev_cand = [g for g in cand if reviewed[g]]
    cand = rev_cand or cand

    # 5. Alphabetical sort
    return sorted(cand)[0]


# Complex Retrievals & Computations

def compute_pair_score_for_complex(
        comp: models.Complex,
        chain1_letter: str,
        chain2_letter: str,
        metric: str  # "iptm" or "pae_min"
) -> float | None:
    """
    Extracts the pairwise score between two chains (e.g., A and B)
    from the stored JSON lists in the Chain table.
    """
    # Sort chains by ID to determine their index (0=A, 1=B, etc.)
    chains_sorted = sorted(comp.chains, key=lambda c: c.id)

    c1_idx = -1
    c2_idx = -1

    for i, chain in enumerate(chains_sorted):
        current_letter = chr(65 + i)  # 65 is 'A'

        if current_letter == chain1_letter:
            c1_idx = i
        if current_letter == chain2_letter:
            c2_idx = i

    if c1_idx == -1 or c2_idx == -1:
        return None

    chain_obj = chains_sorted[c1_idx]

    target_list = []
    if metric == "iptm":
        target_list = chain_obj.chain_pair_iptm
    elif metric == "pae_min":
        target_list = chain_obj.chain_pair_pae_min

    if not target_list or c2_idx >= len(target_list):
        return None

    return target_list[c2_idx]


def compute_summary_names(comp: models.Complex) -> str:
    """Generates a summary string of up to 3 protein names for the complex."""
    chains = sorted(comp.chains, key=lambda ch: ch.id)
    pnames = [(ch.protein_name or "–") for ch in chains]
    return " | ".join(pnames[:3]) + (" …" if len(pnames) > 3 else "")


def compute_summary_genes(comp: models.Complex) -> str:
    """Generates a summary string of up to 3 gene names for the complex."""
    chains = sorted(comp.chains, key=lambda ch: ch.id)
    gnames = [(ch.gene_name or "–") for ch in chains]
    return " | ".join(gnames[:3]) + (" …" if len(gnames) > 3 else "")


def _attach_summary_names(complexes: list[models.Complex]) -> list[models.Complex]:
    """
    Attaches pre-computed summary_names and summary_genes attributes directly
    to the complex objects based on persisted chain columns.
    Ensures stability across sorting and joins without needing recalculation.
    """
    for comp in complexes:
        chains = sorted(comp.chains, key=lambda ch: ch.id)

        # Proteins
        pnames = [(ch.protein_name or "–") for ch in chains]
        comp.summary_names = " | ".join(pnames[:3]) + (" …" if len(pnames) > 3 else "")

        # Genes
        gnames = [(ch.gene_name or "–") for ch in chains]
        comp.summary_genes = " | ".join(gnames[:3]) + (" …" if len(gnames) > 3 else "")

    return complexes


def get_complex_by_accession(db: Session, ac: str) -> models.Complex | None:
    """Fetches a specific complex by its accession ID, eagerly loading its chains and metadata."""
    return (
        db.query(models.Complex)
        .options(
            joinedload(models.Complex.chains)
            .joinedload(models.Chain.uniparc)
            .joinedload(UniParcEntry.accessions),
            joinedload(models.Complex.interface_scores) # Eager load interface scores to prevent DB errors
        )
        .filter(models.Complex.accession == ac)
        .first()
    )


def list_complexes(db: Session, limit: int = 50):
    """Lists the most recent complexes with eager loading for rendering."""
    complexes = (
        db.query(models.Complex)
        .options(
            joinedload(models.Complex.chains).joinedload(models.Chain.uniparc),
            joinedload(models.Complex.collection),
            joinedload(models.Complex.interface_scores)
        )
        .order_by(models.Complex.created_at.desc())
        .limit(limit)
        .all()
    )

    return _attach_summary_names(complexes)


# Search Filtering & SQL Alchemy Logic

def _seq_id_filters(stmt, chain_alias, query: str, fuzzy: bool):
    """
    Parses a search string supporting basic AND/OR logic.
    Returns the SQLAlchemy statement and a list of filter conditions.
    """
    q = query.strip()
    if not q:
        return stmt, []

    logic_re = re.compile(r"\s+(AND|OR)\s+", re.I)
    parts = logic_re.split(q)
    tokens = []
    connectors = []

    for part in parts:
        up = part.upper()
        if up in ("AND", "OR"):
            connectors.append(up)
        elif part.strip():
            tokens.append(part.strip())

    if not tokens:
        return stmt, []

    def clause_for_token(tok: str):
        conds = []

        if fuzzy:
            conds.append(chain_alias.sequence.ilike(f"%{tok}%"))
        else:
            conds.append(chain_alias.sequence.ilike(tok))

        like = f"%{tok}%"

        conds.append(
            chain_alias.uniparc.has(
                models.UniParcEntry.upi.ilike(like)
            )
        )

        upa = models.UniprotAccession
        upa_match = or_(
            upa.accession.ilike(like),
            upa.gene_name.ilike(like),
            upa.protein_name.ilike(like),
            upa.organism.ilike(like),
            cast(upa.alternative_names, String).ilike(like),
        )

        conds.append(
            exists(
                select(1)
                .where(upa.upi_id == chain_alias.upi_id)
                .where(upa_match)
            )
        )

        return or_(*conds) if conds else sql_true()

    clauses = [clause_for_token(t) for t in tokens]

    expr = clauses[0]
    for op, nxt in zip(connectors, clauses[1:]):
        expr = and_(expr, nxt) if op == "AND" else or_(expr, nxt)

    return stmt, [expr]

def _apply_paging_sort(stmt, sort: str | None, desc_flag: bool, page: int, per_page: int):
    """Applies sorting logic and pagination offset/limit to a query statement."""
    sort = sort or "created"

    if sort == "chains":
        stmt = stmt.join(models.Chain).group_by(models.Complex.id)
        col = func.count(models.Chain.id)
    else:
        sortable = {
            "created": models.Complex.created_at,
            "iptm": models.Complex.iptm,
            "ptm": models.Complex.ptm,
            "ranking": models.Complex.ranking_score,
            "plddt": models.Complex.mean_plddt,

            # ipSAE family (10 base)
            "ipsae": models.Complex.ipsae_10,
            "ipsae_d0chn": models.Complex.ipsae_d0chn_10,
            "ipsae_d0dom": models.Complex.ipsae_d0dom_10,
            "iptm_d0chn": models.Complex.iptm_d0chn_10,
            "pdockq": models.Complex.pdockq,
            "pdockq2": models.Complex.pdockq2,
            "lis": models.Complex.lis,
        }
        col = sortable.get(sort, models.Complex.created_at)

    stmt = stmt.order_by(desc(col) if desc_flag else col)
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    return stmt


# Search Implementations

def quick_search(
        db: Session,
        q: str,
        *,
        page: int = 1,
        per_page: int = 20,
        sort: str | None = "created",
        desc_flag: bool = True,
):
    """
    Executes a quick global search against Accessions, Collections, or Chain metadata.
    Returns paginated Complex objects.
    """
    stmt = select(Complex)
    q_clean = q.strip()

    if q_clean:
        # Check Collection Name
        coll = (
            db.query(Collection.id)
            .filter(Collection.name.ilike(q_clean))
            .scalar()
        )
        if coll:
            stmt = stmt.filter(Complex.collection_id == coll)

        # Check Exact Accession
        elif re.fullmatch(r"AF-CP-\d{5}", q_clean, re.IGNORECASE):
            stmt = stmt.filter(Complex.accession == q_clean.upper())

        # Check Sequence / UPI / Metadata via alias and filters
        else:
            chain_alias = aliased(Chain)
            stmt = stmt.join(chain_alias, chain_alias.complex_id == Complex.id)
            stmt, filters = _seq_id_filters(stmt, chain_alias, q_clean, fuzzy=True)
            stmt = stmt.filter(and_(*filters))

    stmt = _apply_paging_sort(stmt, sort, desc_flag, page, per_page)

    stmt = stmt.options(
        joinedload(Complex.chains)
        .joinedload(Chain.uniparc)
        .joinedload(UniParcEntry.accessions),
        joinedload(Complex.collection),
        joinedload(Complex.interface_scores) # UI FIX
    )

    result = db.scalars(stmt).unique().all()
    return _attach_summary_names(result)


# Accession Fetching

def all_accessions(db: Session) -> list[str]:
    """
    Returns all Complex Accessions as a simple list of strings.
    Fetches only a single column to minimize memory overhead.
    """
    stmt = select(models.Complex.accession)
    return [row[0] for row in db.execute(stmt)]


def accessions_in_collection(db: Session, coll_name: str) -> list[str]:
    """Returns accessions belonging to a specific collection."""
    stmt = (
        select(models.Complex.accession)
        .join(models.Collection)
        .filter(models.Collection.name == coll_name)
    )
    return [row[0] for row in db.execute(stmt)]


def accessions_for_quick_search(db: Session, q: str) -> list[str]:
    """
    Returns the Complex Accessions that match a quick search query.
    Bypasses pagination and model mapping to return a simple list of strings.
    """
    q_clean = q.strip()

    if not q_clean:
        stmt = select(models.Complex.accession)
        return [row[0] for row in db.execute(stmt)]

    stmt = select(models.Complex.accession)

    coll_id = db.query(models.Collection.id).filter(models.Collection.name.ilike(q_clean)).scalar()
    if coll_id:
        stmt = stmt.filter(models.Complex.collection_id == coll_id)

    elif re.fullmatch(r"AF-CP-\d{5}", q_clean, re.IGNORECASE):
        stmt = stmt.filter(models.Complex.accession == q_clean.upper())

    else:
        chain_alias = aliased(models.Chain)
        stmt = stmt.join(chain_alias, chain_alias.complex_id == models.Complex.id)
        stmt, filters = _seq_id_filters(stmt, chain_alias, q_clean, fuzzy=True)
        stmt = stmt.filter(and_(*filters))

    return [row[0] for row in db.execute(stmt)]



# Advanced Search Accession Fetcher

def accessions_for_advanced_search(db: Session, qp) -> list[str]:
    def _flt(key):
        return float(qp[key]) if key in qp and qp[key] else None

    def _int(key):
        return int(qp[key]) if key in qp and qp[key] else None

    stmt = select(models.Complex.accession)
    filters = []

    def _range(col, lo, hi):
        if lo is not None: filters.append(col >= lo)
        if hi is not None: filters.append(col <= hi)

    _range(models.Complex.iptm, _flt("iptm_min"), _flt("iptm_max"))
    _range(models.Complex.ptm, _flt("ptm_min"), _flt("ptm_max"))
    _range(models.Complex.ranking_score, _flt("ranking_min"), _flt("ranking_max"))
    _range(models.Complex.mean_plddt, _flt("plddt_min"), _flt("plddt_max"))

    if qp.get("has_clash_exclude") == "true":
        filters.append(or_(models.Complex.has_clash == 0.0, models.Complex.has_clash.is_(None)))

    coll_id = _int("collection_id")
    if coll_id:
        filters.append(models.Complex.collection_id == coll_id)

    cc_min, cc_max = _int("chain_count_min"), _int("chain_count_max")
    oli_state = qp.get("oligomeric_state")

    if cc_min or cc_max or oli_state in ("monomer", "homomer", "heteromer"):
        subc = (
            select(
                models.Chain.complex_id,
                func.count(models.Chain.id).label("cc"),
                func.count(func.distinct(models.Chain.sequence)).label("distinct_seqs")
            )
            .group_by(models.Chain.complex_id)
            .subquery()
        )
        stmt = stmt.join(subc, models.Complex.id == subc.c.complex_id)
        _range(subc.c.cc, cc_min, cc_max)

        if oli_state == "monomer":
            filters.append(subc.c.cc == 1)
        elif oli_state == "homomer":
            filters.append(and_(subc.c.cc > 1, subc.c.distinct_seqs == 1))
        elif oli_state == "heteromer":
            filters.append(subc.c.distinct_seqs > 1)

    chain_seqs = qp.getlist("chain_seq")
    chain_match_type = qp.getlist("chain_match_type")
    if chain_seqs:
        for idx, seq in enumerate(chain_seqs):
            seq = seq.strip()
            if not seq:
                continue

            alias = aliased(models.Chain, name=f"cf{idx}")
            stmt = stmt.join(alias, alias.complex_id == models.Complex.id)

            fuzzy = (chain_match_type[idx] == "substring") if idx < len(chain_match_type) else True

            stmt, seq_flt = _seq_id_filters(stmt, alias, seq, fuzzy)
            filters.extend(seq_flt)

            def _range_chain(param_key_min, param_key_max, col):
                lo, hi = _flt(param_key_min), _flt(param_key_max)
                if lo is not None: filters.append(cast(cast(col, String), Float) >= lo)
                if hi is not None: filters.append(cast(cast(col, String), Float) <= hi)

            _range_chain("chain_iptm_min", "chain_iptm_max", alias.chain_iptm)
            _range_chain("chain_ptm_min", "chain_ptm_max", alias.chain_ptm)

    if filters:
        stmt = stmt.filter(and_(*filters))

    return [row[0] for row in db.execute(stmt)]


# Advanced Search

def search_advanced(
        db: Session,
        adv: schemas.AdvancedSearch,
        *,
        collection_id: str | int | None = None,
        oligomeric_state: str | None = None,
        page: int = 1,
        per_page: int = 20,
        sort: str | None = None,
        desc_flag: bool = True,
):
    stmt = select(models.Complex)
    filters = []

    if adv.accession:
        filters.append(models.Complex.accession.ilike(f"%{adv.accession}%"))
    if adv.desc:
        filters.append(models.Complex.description.ilike(f"%{adv.desc}%"))

    def _range(col, lo, hi):
        if lo is not None: filters.append(col >= lo)
        if hi is not None: filters.append(col <= hi)

    _range(models.Complex.iptm, adv.iptm_min, adv.iptm_max)
    _range(models.Complex.ptm, adv.ptm_min, adv.ptm_max)
    _range(models.Complex.ranking_score, adv.ranking_min, adv.ranking_max)
    _range(models.Complex.mean_plddt, adv.plddt_min, adv.plddt_max)

    if adv.has_clash_exclude:
        filters.append(or_(models.Complex.has_clash == 0.0, models.Complex.has_clash.is_(None)))

    if collection_id:
        try:
            filters.append(models.Complex.collection_id == int(collection_id))
        except ValueError:
            pass

    if adv.chain_count_min or adv.chain_count_max or oligomeric_state in ("monomer", "homomer", "heteromer"):
        subc = (
            select(
                models.Chain.complex_id,
                func.count(models.Chain.id).label("cc"),
                func.count(func.distinct(models.Chain.sequence)).label("uc")
            )
            .group_by(models.Chain.complex_id)
            .subquery()
        )
        stmt = stmt.join(subc, models.Complex.id == subc.c.complex_id)
        _range(subc.c.cc, adv.chain_count_min, adv.chain_count_max)
        if oligomeric_state == "monomer":
            filters.append(subc.c.cc == 1)
        elif oligomeric_state == "homomer":
            filters.append(and_(subc.c.cc > 1, subc.c.uc == 1))
        elif oligomeric_state == "heteromer":
            filters.append(subc.c.uc > 1)

    _range(models.Complex.created_at, getattr(adv, "created_from", None), getattr(adv, "created_to", None))

    if adv.chain_filters:
        aliases = []
        for idx, cf in enumerate(adv.chain_filters):
            alias = aliased(models.Chain, name=f"cf{idx}")
            stmt = stmt.join(alias, alias.complex_id == models.Complex.id)

            for prev in aliases:
                filters.append(alias.id != prev.id)
            aliases.append(alias)

            fuzzy_val = cf.get("fuzzy", True) if isinstance(cf, dict) else cf.fuzzy
            seq_val = cf.get("seq", "") if isinstance(cf, dict) else cf.seq

            stmt, seq_filters = _seq_id_filters(stmt, alias, seq_val, fuzzy_val)
            filters.extend(seq_filters)

            iptm_min = cf.get("iptm_min") if isinstance(cf, dict) else cf.iptm_min
            iptm_max = cf.get("iptm_max") if isinstance(cf, dict) else cf.iptm_max
            ptm_min = cf.get("ptm_min") if isinstance(cf, dict) else cf.ptm_min
            ptm_max = cf.get("ptm_max") if isinstance(cf, dict) else cf.ptm_max

            if iptm_min is not None:
                filters.append(cast(cast(alias.chain_iptm, String), Float) >= iptm_min)
            if iptm_max is not None:
                filters.append(cast(cast(alias.chain_iptm, String), Float) <= iptm_max)
            if ptm_min is not None:
                filters.append(cast(cast(alias.chain_ptm, String), Float) >= ptm_min)
            if ptm_max is not None:
                filters.append(cast(cast(alias.chain_ptm, String), Float) <= ptm_max)

    if filters:
        stmt = stmt.filter(and_(*filters))

    stmt = _apply_paging_sort(stmt, sort, desc_flag, page, per_page)
    stmt = stmt.options(
        joinedload(models.Complex.chains)
        .joinedload(models.Chain.uniparc)
        .joinedload(models.UniParcEntry.accessions),
        joinedload(models.Complex.collection),
        joinedload(models.Complex.interface_scores) # UI FIX
    )
    result = db.scalars(stmt).unique().all()
    return _attach_summary_names(result)

# Radius Score Search & Sorting

def compute_radius_score_for_complex(
        comp: models.Complex,
        *,
        center_res: int,
        radius: float,
        chain_letter: str | None = None,
) -> float | None:
    key = str(int(radius))
    vals = []

    chains_sorted = sorted(comp.chains, key=lambda ch: ch.id)

    for i, ch in enumerate(chains_sorted):
        letter = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[i]

        if chain_letter and letter != chain_letter:
            continue

        if not ch.radius_plddt:
            continue

        arr = ch.radius_plddt.get(key)
        if not arr:
            continue

        if 1 <= center_res <= len(arr):
            vals.append(arr[center_res - 1])

    if not vals:
        return None

    return round(float(sum(vals) / len(vals)), 2)


def radius_sort_over_accessions(
        db: Session,
        accs: list[str],
        *,
        center_res: int,
        radius: float,
        chain_letter: str | None,
        page: int,
        per_page: int,
        desc_flag: bool = True,
):
    if not accs:
        return []

    stmt = (
        select(
            models.Complex.id,
            models.Chain.id.label("chain_id"),
            models.Chain.radius_plddt
        )
        .join(models.Chain, models.Chain.complex_id == models.Complex.id)
        .where(models.Complex.accession.in_(accs))
        .order_by(models.Complex.id, models.Chain.id)
    )
    rows = db.execute(stmt).fetchall()

    complex_data = {}
    for row in rows:
        c_id = row.id
        if c_id not in complex_data:
            complex_data[c_id] = {"chains": []}
        complex_data[c_id]["chains"].append(row.radius_plddt)

    key_str = str(int(radius))
    scored_items = []

    for c_id, data in complex_data.items():
        vals = []
        for i, radius_dict in enumerate(data["chains"]):
            letter = chr(65 + i)
            if chain_letter and letter != chain_letter:
                continue
            if not radius_dict:
                continue

            arr = radius_dict.get(key_str)
            if arr and 1 <= center_res <= len(arr):
                vals.append(arr[center_res - 1])

        score = round(sum(vals) / len(vals), 2) if vals else -1e9
        scored_items.append((score, c_id))

    scored_items.sort(key=lambda x: x[0], reverse=desc_flag)

    start = (page - 1) * per_page
    end = start + per_page
    paged_ids = [item[1] for item in scored_items[start:end]]

    if not paged_ids:
        return []

    complexes = (
        db.query(models.Complex)
        .options(
            joinedload(models.Complex.chains).joinedload(models.Chain.uniparc),
            joinedload(models.Complex.collection),
            joinedload(models.Complex.interface_scores) # UI FIX
        )
        .filter(models.Complex.id.in_(paged_ids))
        .all()
    )

    # Restore the sorted order and attach the computed score
    c_map = {c.id: c for c in complexes}
    sorted_complexes = []

    for score, c_id in scored_items[start:end]:
        if c_id in c_map:
            c = c_map[c_id]
            c.mean_plddt_radius = score if score != -1e9 else None
            sorted_complexes.append(c)

    return sorted_complexes

def search_advanced_radius_sort(
        db: Session,
        qp,
        *,
        center_res: int,
        radius: float,
        chain_letter: str | None,
        page: int,
        per_page: int,
        desc_flag: bool = True,
):
    """Radius sort for Advanced Search (or Quick Search if 'q' is present in params)."""
    q_clean = (qp.get("q") or "").strip()

    if q_clean:
        accs = accessions_for_quick_search(db, q_clean)
    else:
        accs = accessions_for_advanced_search(db, qp)

    return radius_sort_over_accessions(
        db, accs, center_res=center_res, radius=radius, chain_letter=chain_letter,
        page=page, per_page=per_page, desc_flag=desc_flag
    )


def search_quick_radius_sort(
        db: Session,
        q: str,
        *,
        center_res: int,
        radius: float,
        chain_letter: str | None,
        page: int,
        per_page: int,
        desc_flag: bool = True,
):
    """Radius sort specifically for Quick Search."""
    accs = accessions_for_quick_search(db, q)
    return radius_sort_over_accessions(
        db, accs, center_res=center_res, radius=radius, chain_letter=chain_letter,
        page=page, per_page=per_page, desc_flag=desc_flag
    )


def search_collection_radius_sort(
        db: Session,
        coll_name: str,
        *,
        center_res: int,
        radius: float,
        chain_letter: str | None,
        page: int,
        per_page: int,
        desc_flag: bool = True,
):
    """Radius sort for all hits within a specific Collection."""
    accs = accessions_in_collection(db, coll_name)
    return radius_sort_over_accessions(
        db, accs, center_res=center_res, radius=radius, chain_letter=chain_letter,
        page=page, per_page=per_page, desc_flag=desc_flag
    )


# Collections Data Access

def list_all_collections(db: Session):
    """Returns every collection with the total number of complexes it contains."""
    stmt = (
        select(
            models.Collection.id,
            models.Collection.name,
            func.count(models.Complex.id).label("n_complex")
        )
        .outerjoin(models.Complex, models.Complex.collection_id == models.Collection.id)
        .group_by(models.Collection.id)
        .order_by(models.Collection.name.asc())
    )
    return db.execute(stmt).all()


def list_collections(db: Session, limit: int = 10):
    """Returns a limited list of collections ordered by ID descending."""
    return (
        db.query(models.Collection)
        .order_by(models.Collection.id.desc())
        .limit(limit)
        .all()
    )


def list_collection(
        db: Session,
        q: str,
        *,
        page: int = 1,
        per_page: int = 20,
        sort: str | None = "created",
        desc_flag: bool = True,
):
    """Returns paginated complexes for a specific collection name."""
    stmt = select(models.Complex)
    coll_id = (
        db.query(models.Collection.id)
        .filter(models.Collection.name.ilike(q.strip()))
        .scalar()
    )

    if coll_id:
        stmt = stmt.filter(models.Complex.collection_id == coll_id)

    stmt = _apply_paging_sort(stmt, sort, desc_flag, page, per_page)
    stmt = stmt.options(
        joinedload(models.Complex.chains)
        .joinedload(models.Chain.uniparc)
        .joinedload(models.UniParcEntry.accessions),
        joinedload(models.Complex.interface_scores) # UI FIX
    )

    complexes = db.scalars(stmt).unique().all()
    return _attach_summary_names(complexes)


def get_collection_by_name(db: Session, name: str) -> Optional[models.Collection]:
    """Helper to fetch a collection object by exact name."""
    return db.query(models.Collection).filter(models.Collection.name == name).first()


def get_collection_stats(db: Session, collection_name: str) -> dict:
    """
    Calculates aggregate statistics for a collection.
    Fetches description and created date from the first associated complex.
    """
    coll = get_collection_by_name(db, collection_name)
    if not coll:
        return {}

    stats = db.query(
        func.count(models.Complex.id).label("count"),
        func.avg(models.Complex.iptm).label("avg_iptm"),
        func.avg(models.Complex.ptm).label("avg_ptm"),
        func.avg(models.Complex.mean_iptm).label("avg_mean_iptm"),
        func.avg(models.Complex.mean_ptm).label("avg_mean_ptm"),
        func.avg(models.Complex.mean_plddt).label("avg_plddt")
    ).filter(
        models.Complex.collection_id == coll.id
    ).first()

    first_cplx = (
        db.query(models.Complex.description, models.Complex.created_at)
        .filter(models.Complex.collection_id == coll.id)
        .first()
    )

    return {
        "count": stats.count,
        "avg_iptm": float(stats.avg_iptm or 0.0),
        "avg_ptm": float(stats.avg_ptm or 0.0),
        "avg_mean_iptm": float(stats.avg_mean_iptm or 0.0),
        "avg_mean_ptm": float(stats.avg_mean_ptm or 0.0),
        "avg_plddt": float(stats.avg_plddt or 0.0),
        "description": first_cplx.description if first_cplx else "",
        "created": first_cplx.created_at if first_cplx else None
    }


def get_collection_species_distribution(db: Session, collection_name: str, limit: int = 10) -> List[dict]:
    """
    Determines the top species in a collection based on chain assignments.
    Filters against the `primary_accession` to prevent duplicate counting
    from multiple synonyms attached to a single UniParc Entry.
    """
    coll = get_collection_by_name(db, collection_name)
    if not coll:
        return []

    results = (
        db.query(
            models.UniprotAccession.organism,
            models.Chain.mapping_method,
            func.count(models.UniprotAccession.organism).label("cnt")
        )
        .select_from(models.UniprotAccession)
        .join(models.Chain, models.Chain.upi_id == models.UniprotAccession.upi_id)
        .join(models.Complex, models.Complex.id == models.Chain.complex_id)
        .filter(
            models.Complex.collection_id == coll.id,
            models.UniprotAccession.organism.isnot(None),
            models.UniprotAccession.accession == models.Chain.primary_accession
        )
        .group_by(models.UniprotAccession.organism, models.Chain.mapping_method)
        .order_by(desc("cnt"))
        .all()
    )

    return [{"name": r[0], "method": r[1] or "auto", "count": r[2]} for r in results]


def get_collection_scatter_data(db: Session, collection_name: str) -> List[dict]:
    """
    Loads valid (iptm, ptm) pairs for a given collection, including accessions for tooltips.
    Returns:
        List of dictionaries formatted for Chart.js: [{"x": iptm, "y": ptm, "acc": accession}]
    """
    coll = get_collection_by_name(db, collection_name)
    if not coll:
        return []

    results = (
        db.query(models.Complex.iptm, models.Complex.ptm, models.Complex.accession)
        .filter(models.Complex.collection_id == coll.id)
        .filter(models.Complex.iptm.isnot(None))
        .filter(models.Complex.ptm.isnot(None))
        .all()
    )

    return [{"x": r[0], "y": r[1], "acc": r[2]} for r in results]