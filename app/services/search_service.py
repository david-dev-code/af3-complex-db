import ast
import operator
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import desc, func
from sqlalchemy.orm import Session, joinedload

from app import crud, models, schemas


SCORE_VAR_MAP = {
    "iptm": models.Complex.iptm,
    "ptm": models.Complex.ptm,
    "ranking": models.Complex.ranking_score,
    "ranking_score": models.Complex.ranking_score,
    "plddt": models.Complex.mean_plddt,
    "mean_plddt": models.Complex.mean_plddt,
    "fraction_disordered": models.Complex.fraction_disordered,
    "mean_iptm": models.Complex.mean_iptm,
    "mean_ptm": models.Complex.mean_ptm,
    "bsa": models.Complex.bsa,
    "num_h_bonds": models.Complex.num_h_bonds,
    "num_salt_bridges": models.Complex.num_salt_bridges,
}

_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

def _safe_eval_ast(node: ast.AST, var_map: dict) -> Any:
    """Evaluates the parsed AST node safely using the provided variable map."""
    if isinstance(node, ast.Expression):
        return _safe_eval_ast(node.body, var_map)
    elif isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise ValueError("Only numeric constants are allowed.")
        return node.value
    elif isinstance(node, ast.Name):
        if node.id in var_map:
            return var_map[node.id]
        raise ValueError(f"Variable '{node.id}' is not allowed.")
    elif isinstance(node, ast.BinOp):
        left = _safe_eval_ast(node.left, var_map)
        right = _safe_eval_ast(node.right, var_map)
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPERATORS:
            raise ValueError(f"Operator '{op_type.__name__}' is not supported.")
        if op_type == ast.Pow and isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if right > 100:
                raise ValueError("Exponent is too large.")
        return _ALLOWED_OPERATORS[op_type](left, right)
    elif isinstance(node, ast.UnaryOp):
        operand = _safe_eval_ast(node.operand, var_map)
        op_type = type(node.op)
        if op_type not in _ALLOWED_OPERATORS:
            raise ValueError(f"Unary operator '{op_type.__name__}' is not supported.")
        return _ALLOWED_OPERATORS[op_type](operand)
    else:
        raise ValueError(f"Syntax node '{type(node).__name__}' is not allowed.")

def parse_custom_formula(formula: str) -> Tuple[Optional[Any], Optional[Callable]]:
    """Parses a custom mathematical formula string into SQL and Python evaluators."""
    formula = formula.strip()
    if not formula:
        return None, None
    try:
        tree = ast.parse(formula, mode='eval')
    except SyntaxError:
        return None, None

    try:
        sql_expr = _safe_eval_ast(tree, SCORE_VAR_MAP)
    except Exception as e:
        print(f"[CUSTOM SCORE] SQL Expression Error: {e}", flush=True)
        return None, None

    def calc_py(c: models.Complex) -> Optional[float]:
        """Calculates the custom score for a single complex in Python."""
        val_map = {
            "iptm": float(c.iptm or 0.0),
            "ptm": float(c.ptm or 0.0),
            "ranking": float(c.ranking_score or 0.0),
            "ranking_score": float(c.ranking_score or 0.0),
            "plddt": float(c.mean_plddt or 0.0),
            "mean_plddt": float(c.mean_plddt or 0.0),
            "fraction_disordered": float(c.fraction_disordered or 0.0),
            "mean_iptm": float(c.mean_iptm or 0.0),
            "mean_ptm": float(c.mean_ptm or 0.0),
            "bsa": float(c.bsa or 0.0),
            "num_h_bonds": float(c.num_h_bonds or 0.0),
            "num_salt_bridges": float(c.num_salt_bridges or 0.0),
        }
        try:
            result = _safe_eval_ast(tree, val_map)
            return float(result)
        except Exception:
            return None

    return sql_expr, calc_py


# Cart Data Aggregation

def get_cart_complexes(db: Session, accessions: List[str]) -> List[models.Complex]:
    """Fetches a list of complexes by their accessions for the cart."""
    return (
        db.query(models.Complex)
        .options(joinedload(models.Complex.chains))
        .filter(models.Complex.accession.in_(accessions))
        .all()
    )

def calculate_cart_stats(complexes: List[models.Complex]) -> Dict[str, Any]:
    """Calculates summary statistics for the complexes in the cart."""
    def avg(lst):
        clean = [x for x in lst if x is not None]
        return sum(clean) / len(clean) if clean else None

    return {
        "count": len(complexes),
        "avg_iptm": avg([c.iptm for c in complexes]),
        "avg_ptm": avg([c.ptm for c in complexes]),
        "avg_mean_iptm": avg([c.mean_iptm for c in complexes]),
        "avg_mean_ptm": avg([c.mean_ptm for c in complexes]),
        "avg_plddt": avg([c.mean_plddt for c in complexes]),
    }

def generate_scatter_data(complexes: List[models.Complex]) -> List[Dict[str, Any]]:
    """Generates a list of dictionaries for scatter plot visualization."""
    return [
        {"x": c.iptm, "y": c.ptm, "acc": c.accession}
        for c in complexes
        if c.iptm is not None and c.ptm is not None
    ]

def get_species_distribution(db: Session, accessions: List[str], limit: int = 8) -> List[Dict[str, Any]]:
    """Fetches the top organism distribution for the given accessions."""
    results = (
        db.query(
            models.UniprotAccession.organism,
            models.Chain.mapping_method,
            func.count(models.UniprotAccession.organism).label("cnt")
        )
        .select_from(models.UniprotAccession)
        .join(models.Chain, models.Chain.upi_id == models.UniprotAccession.upi_id)
        .join(models.Complex, models.Complex.id == models.Chain.complex_id)
        .filter(models.Complex.accession.in_(accessions))
        .filter(models.UniprotAccession.organism.isnot(None))
        .filter(models.UniprotAccession.accession == models.Chain.primary_accession)
        .group_by(models.UniprotAccession.organism, models.Chain.mapping_method)
        .order_by(desc("cnt"))
        .all()
    )
    return [{"name": r[0], "method": r[1] or "auto", "count": r[2]} for r in results]


# View Processing

def process_results_for_view(
        complexes: list,
        cols: Optional[List[str]],
        sort: str,
        desc: bool,
        plddt_center: str,
        plddt_chain: str,
        custom_formula: str,
        ipsae_pae: int, ipsae_pair: str,
        ipsae_pae_2: int, ipsae_pair_2: str,
        ipsae_pae_3: int, ipsae_pair_3: str,
) -> Tuple[List[models.Complex], List[str]]:
    """Processes search results, calculates dynamic scores, and sorts the output."""
    default_cols = [
        "accession", "genes", "proteins", "collection",
        "iptm", "ptm", "created", "chains",
    ]
    static_allowed = {
        "accession", "genes", "proteins", "collection",
        "iptm", "ptm", "created", "chains",
        "ranking_score", "mean_plddt", "fraction_disordered",
        "mean_iptm", "mean_ptm", "custom_score",
        "bsa", "num_h_bonds", "num_salt_bridges",
        "mean_plddt_radius_5", "mean_plddt_radius_10", "mean_plddt_radius_15",
        "ipsae", "ipsae_d0chn", "ipsae_d0dom", "iptm_d0chn", "pdockq", "pdockq2", "lis",
        "ipsae_2", "ipsae_d0chn_2", "ipsae_d0dom_2", "iptm_d0chn_2", "pdockq_2", "pdockq2_2", "lis_2",
        "ipsae_3", "ipsae_d0chn_3", "ipsae_d0dom_3", "iptm_d0chn_3", "pdockq_3", "pdockq2_3", "lis_3",
    }

    req_cols = cols or default_cols
    visible_cols = []
    pairwise_cols_info = []

    for c in req_cols:
        if c in static_allowed:
            visible_cols.append(c)
        elif c.startswith("pair_"):
            parts = c.split("__")
            if len(parts) == 3:
                metric, c1, c2 = parts
                if metric in ["pair_iptm", "pair_pae_min"]:
                    visible_cols.append(c)
                    pairwise_cols_info.append((c, metric, c1, c2))

    if not visible_cols:
        visible_cols = default_cols

    calc_custom_py = None
    if custom_formula.strip():
        _, calc_custom_py = parse_custom_formula(custom_formula)

    center_i = int(plddt_center) if plddt_center else None
    chain_letter = plddt_chain.strip().upper() or None

    complexes = crud._attach_summary_names(complexes)

    for c in complexes:
        c.pairwise_values = {}
        for (col_key, metric, c1, c2) in pairwise_cols_info:
            mode = "iptm" if metric == "pair_iptm" else "pae_min"
            val = crud.compute_pair_score_for_complex(c, c1, c2, mode)
            c.pairwise_values[col_key] = val

        c.custom_score_val = None
        if calc_custom_py:
            c.custom_score_val = calc_custom_py(c)

        if center_i:
            c.mean_plddt_radius_5 = crud.compute_radius_score_for_complex(c, center_res=center_i, radius=5.0, chain_letter=chain_letter)
            c.mean_plddt_radius_10 = crud.compute_radius_score_for_complex(c, center_res=center_i, radius=10.0, chain_letter=chain_letter)
            c.mean_plddt_radius_15 = crud.compute_radius_score_for_complex(c, center_res=center_i, radius=15.0, chain_letter=chain_letter)
            c.region_label = None
            if chain_letter:
                chains_sorted = sorted(c.chains, key=lambda ch: ch.id)
                idx0 = ord(chain_letter) - ord("A")
                if 0 <= idx0 < len(chains_sorted):
                    ch = chains_sorted[idx0]
                    if 1 <= center_i <= len(ch.sequence):
                        c.region_label = f"{chain_letter}:{ch.sequence[center_i - 1]}{center_i}"

        def _map_ipsae(comp, suffix, group_idx=""):
            for field in ["ipsae", "ipsae_d0chn", "ipsae_d0dom", "iptm_d0chn", "pdockq", "pdockq2", "lis"]:
                setattr(comp, f"{field}_current{group_idx}", getattr(comp, f"{field}_{suffix}", None))

        _map_ipsae(c, f"{int(ipsae_pae)}", "")
        _map_ipsae(c, f"{int(ipsae_pae_2)}", "_2")
        _map_ipsae(c, f"{int(ipsae_pae_3)}", "_3")

    # In-memory sorting map
    attr_map = {
        "accession": "accession", "iptm": "iptm", "ptm": "ptm", "mean_iptm": "mean_iptm",
        "mean_ptm": "mean_ptm", "ranking": "ranking_score", "ranking_score": "ranking_score",
        "plddt": "mean_plddt", "mean_plddt": "mean_plddt", "fraction_disordered": "fraction_disordered",
        "bsa": "bsa", "num_h_bonds": "num_h_bonds", "num_salt_bridges": "num_salt_bridges",
        "created": "created_at", "chains": "chains",
        "ipsae": "ipsae_current", "ipsae_d0chn": "ipsae_d0chn_current", "ipsae_d0dom": "ipsae_d0dom_current",
        "iptm_d0chn": "iptm_d0chn_current", "pdockq": "pdockq_current", "pdockq2": "pdockq2_current", "lis": "lis_current",
        "ipsae_2": "ipsae_current_2", "ipsae_d0chn_2": "ipsae_d0chn_current_2", "ipsae_d0dom_2": "ipsae_d0dom_current_2",
        "iptm_d0chn_2": "iptm_d0chn_current_2", "pdockq_2": "pdockq_current_2", "pdockq2_2": "pdockq2_current_2", "lis_2": "lis_current_2",
        "ipsae_3": "ipsae_current_3", "ipsae_d0chn_3": "ipsae_d0chn_current_3", "ipsae_d0dom_3": "ipsae_d0dom_current_3",
        "iptm_d0chn_3": "iptm_d0chn_current_3", "pdockq_3": "pdockq_current_3", "pdockq2_3": "pdockq2_current_3", "lis_3": "lis_current_3",
    }

    sort_key = sort or "created"

    if sort_key == "custom_score":
        complexes.sort(key=lambda x: (x.custom_score_val is None, x.custom_score_val), reverse=desc)
    elif sort_key.startswith("pair_"):
        complexes.sort(key=lambda x: (x.pairwise_values.get(sort_key) is None, x.pairwise_values.get(sort_key)), reverse=desc)
    elif sort_key.startswith("mean_plddt_radius_"):
        attr = sort_key
        complexes.sort(key=lambda x: (getattr(x, attr, None) is None, getattr(x, attr, None)), reverse=desc)
    elif sort_key in attr_map:
        attr = attr_map[sort_key]
        if attr == "chains":
            complexes.sort(key=lambda x: len(x.chains), reverse=desc)
        else:
            def get_val(obj): return getattr(obj, attr, None)
            complexes.sort(key=lambda x: (get_val(x) is None, get_val(x)), reverse=desc)

    return complexes, visible_cols



def execute_advanced_search(
        db: Session,
        query_params: dict,
        page: int,
        sort: str | None,
        desc: bool,
        q: str,
        iptm_min: str, iptm_max: str,
        ptm_min: str, ptm_max: str,
        ranking_min: str, ranking_max: str,
        plddt_min: str, plddt_max: str,
        has_clash_exclude: bool,
        chain_count_min: str, chain_count_max: str,
        collection_id: str, oligomeric_state: str,
        chain_seq: List[str] | None,
        chain_match_type: List[str] | None,
        chain_iptm_min: List[str] | None,
        chain_iptm_max: List[str] | None,
        chain_ptm_min: List[str] | None,
        chain_ptm_max: List[str] | None,
        cols: List[str] | None,
        plddt_center: str,
        plddt_chain: str,
        custom_formula: str,
        ipsae_pae: int, ipsae_pair: str,
        ipsae_pae_2: int, ipsae_pair_2: str,
        ipsae_pae_3: int, ipsae_pair_3: str,
) -> Tuple[List[models.Complex], List[str]]:
    """Executes the advanced search queries including chain-specific constraints."""
    def _flt(x):
        return float(x) if x else None

    def _int(x):
        return int(x) if x else None

    def _opt(lst, idx):
        return lst[idx] if lst and idx < len(lst) else ""

    chain_filters = []
    for idx, seq in enumerate(chain_seq or []):
        seq = seq.strip()
        if not seq:
            continue

        is_fuzzy = True
        if chain_match_type and idx < len(chain_match_type):
            is_fuzzy = (chain_match_type[idx] == "substring")

        chain_filters.append({
            "seq": seq,
            "fuzzy": is_fuzzy,
            "iptm_min": _flt(_opt(chain_iptm_min, idx)),
            "iptm_max": _flt(_opt(chain_iptm_max, idx)),
            "ptm_min": _flt(_opt(chain_ptm_min, idx)),
            "ptm_max": _flt(_opt(chain_ptm_max, idx)),
        })

    adv = schemas.AdvancedSearch(
        iptm_min=_flt(iptm_min), iptm_max=_flt(iptm_max),
        ptm_min=_flt(ptm_min), ptm_max=_flt(ptm_max),
        ranking_min=_flt(ranking_min), ranking_max=_flt(ranking_max),
        plddt_min=_flt(plddt_min), plddt_max=_flt(plddt_max),
        has_clash_exclude=has_clash_exclude,
        chain_count_min=_int(chain_count_min), chain_count_max=_int(chain_count_max),
        chain_filters=chain_filters or None,
    )

    q_clean = q.strip()
    has_any_adv_filter = any([
        iptm_min, iptm_max, ptm_min, ptm_max, ranking_min, ranking_max,
        plddt_min, plddt_max, has_clash_exclude, chain_count_min, chain_count_max, chain_filters,
        collection_id, oligomeric_state
    ])

    derived_radii = {"plddt_r5": 5.0, "plddt_r10": 10.0, "plddt_r15": 15.0}
    db_sort = sort

    if sort == "custom_score" or (sort and sort.startswith("pair_")) or (sort and "ipsae" in sort):
        db_sort = "created"

    if sort in derived_radii and plddt_center:
        complexes = crud.search_advanced_radius_sort(
            db, query_params, center_res=int(plddt_center), radius=derived_radii[sort],
            chain_letter=plddt_chain.strip().upper() or None, page=page, per_page=20, desc_flag=desc,
        )
    elif q_clean and not has_any_adv_filter:
        complexes = crud.quick_search(
            db, q_clean, page=page, per_page=20, sort=db_sort, desc_flag=desc
        )
    else:
        complexes = crud.search_advanced(
            db, adv, collection_id=collection_id, oligomeric_state=oligomeric_state,
            page=page, per_page=20, sort=db_sort, desc_flag=desc
        )

    return process_results_for_view(
        complexes=complexes, cols=cols, sort=sort or "created", desc=desc,
        plddt_center=plddt_center, plddt_chain=plddt_chain, custom_formula=custom_formula,
        ipsae_pae=ipsae_pae, ipsae_pair=ipsae_pair,
        ipsae_pae_2=ipsae_pae_2, ipsae_pair_2=ipsae_pair_2,
        ipsae_pae_3=ipsae_pae_3, ipsae_pair_3=ipsae_pair_3
    )