from typing import Any, Dict, List, Tuple
from sqlalchemy.orm import Session

from app import crud, models
from app.services import search_service


def get_collection_view_data(
    db: Session,
    collection_name: str,
    page: int,
    sort: str | None,
    desc: bool,
    cols: List[str] | None,
    plddt_center: str,
    plddt_chain: str,
    custom_formula: str,
    ipsae_pae: int,
    ipsae_pair: str,
    ipsae_pae_2: int,
    ipsae_pair_2: str,
    ipsae_pae_3: int,
    ipsae_pair_3: str,
) -> Dict[str, Any]:
    """
    Aggregates all necessary data for the collection detail view, including
    statistics, species distribution, scatter plots, and the processed complex list.
    """
    stats = crud.get_collection_stats(db, collection_name)
    species_data = crud.get_collection_species_distribution(db, collection_name, limit=8)
    scatter_data = crud.get_collection_scatter_data(db, collection_name)

    derived_radii = {"plddt_r5": 5.0, "plddt_r10": 10.0, "plddt_r15": 15.0}
    db_sort = sort

    if sort == "custom_score" or (sort and sort.startswith("pair_")) or (sort and "ipsae" in sort):
        db_sort = "created"

    if sort in derived_radii and plddt_center:
        complexes = crud.search_collection_radius_sort(
            db,
            collection_name,
            center_res=int(plddt_center),
            radius=derived_radii[sort],
            chain_letter=plddt_chain.strip().upper() or None,
            page=page,
            per_page=20,
            desc_flag=desc,
        )
    else:
        complexes = crud.list_collection(
            db,
            collection_name,
            page=page,
            sort=db_sort,
            desc_flag=desc
        )

    complexes_processed, visible_cols = search_service.process_results_for_view(
        complexes=complexes,
        cols=cols,
        sort=sort or "created",
        desc=desc,
        plddt_center=plddt_center,
        plddt_chain=plddt_chain,
        custom_formula=custom_formula,
        ipsae_pae=ipsae_pae,
        ipsae_pair=ipsae_pair,
        ipsae_pae_2=ipsae_pae_2,
        ipsae_pair_2=ipsae_pair_2,
        ipsae_pae_3=ipsae_pae_3,
        ipsae_pair_3=ipsae_pair_3,
    )

    return {
        "query": collection_name,
        "stats": stats,
        "species_data": species_data,
        "scatter_data": scatter_data,
        "complexes": complexes_processed,
        "visible_cols": visible_cols,
    }