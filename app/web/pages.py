import base64
import secrets

from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.api.deps import get_current_admin

from app.services import complex_service, search_service, collection_service, structural_search_service
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import crud, models
from app.core.database import get_db, settings


router = APIRouter()
TEMPLATES = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")


def is_admin_optional(request: Request) -> bool:
    """
    Checks if the browser sends a valid Authorization header.
    If valid -> Returns True (shows admin UI elements).
    If invalid -> Returns False (hides admin elements, but allows page access).
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        return False

    try:
        # Expected Header Format: "Basic <base64_encoded_credentials>"
        scheme, param = auth_header.split()
        if scheme.lower() != "basic":
            return False

        decoded = base64.b64decode(param).decode("utf-8")
        username, password = decoded.split(":", 1)

        valid_user = secrets.compare_digest(username, settings.admin_username)
        valid_pass = secrets.compare_digest(password, settings.admin_password)

        return valid_user and valid_pass
    except Exception:
        return False


@router.get("/upload", response_class=HTMLResponse, include_in_schema=False)
def upload_page(request: Request, admin_user: str = Depends(get_current_admin)):
    """Renders the upload page. Requires Admin Authentication."""
    return TEMPLATES.TemplateResponse("upload.html", {"request": request})


@router.get("/advanced-search", response_class=HTMLResponse, include_in_schema=False)
def advanced_page(request: Request, db: Session = Depends(get_db)):
    """Renders the advanced search form."""
    collections = crud.list_all_collections(db)
    return TEMPLATES.TemplateResponse("advanced_search.html", {"request": request, "collections": collections})

@router.post("/advanced-search", include_in_schema=False)
async def advanced_submit(request: Request):
    """Handles the advanced search form submission and redirects to search results."""
    form = await request.form()
    qs = urlencode(list(form.multi_items()), doseq=True)
    qs += "&page=1&sort=created&desc=true"
    return RedirectResponse(url="/search-results?" + qs, status_code=303)


@router.get("/complex/{accession}/edit", response_class=HTMLResponse, include_in_schema=False)
def edit_complex_page(
        accession: str,
        request: Request,
        db: Session = Depends(get_db),
        admin: str = Depends(get_current_admin)
):
    """Renders the complex edit view. Requires Admin Authentication."""
    context = complex_service.get_edit_context(db, accession)
    if not context:
        raise HTTPException(404, "Complex not found")

    return TEMPLATES.TemplateResponse(
        "edit_complex.html",
        {"request": request, **context}
    )


@router.post("/complex/{accession}/edit", include_in_schema=False)
async def edit_complex_submit(
        accession: str,
        request: Request,
        db: Session = Depends(get_db),
        admin: str = Depends(get_current_admin)
):
    """Processes the submitted form to update complex and chain metadata."""
    form_data = await request.form()

    updated_comp = complex_service.process_edit_form(db, accession, form_data)
    if not updated_comp:
        raise HTTPException(404, "Complex not found or update failed")

    return RedirectResponse(url=f"/complex/{accession}", status_code=303)

# Cart & Table Logic

class CartTableRequest(BaseModel):
    """Schema for cart table parameters."""
    accessions: List[str]
    cols: Optional[List[str]] = None
    sort: Optional[str] = "accession"
    desc: bool = False
    plddt_center: Optional[str] = ""
    plddt_chain: Optional[str] = ""
    custom_formula: Optional[str] = ""
    ipsae_pae: Optional[int] = 10
    ipsae_pair: Optional[str] = "MAX"
    ipsae_pae_2: Optional[int] = 10
    ipsae_pair_2: Optional[str] = "MAX"
    ipsae_pae_3: Optional[int] = 10
    ipsae_pair_3: Optional[str] = "MAX"


@router.get("/cart", response_class=HTMLResponse, include_in_schema=False)
def cart_page(
        request: Request,
        db: Session = Depends(get_db),
        admin_check: bool = Depends(is_admin_optional)
):
    """Renders the cart view containing selected complexes."""
    all_collections = crud.list_all_collections(db)
    return TEMPLATES.TemplateResponse(
        "cart.html",
        {
            "request": request,
            "is_admin": admin_check,
            "all_collections": all_collections
        }
    )


@router.post("/cart/render_table", response_class=JSONResponse, include_in_schema=False)
def render_cart_table(
        request: Request,
        data: CartTableRequest,
        db: Session = Depends(get_db)
):
    """Fetches complex data, applies custom calculations, sorts, and renders HTML partials."""
    complexes = search_service.get_cart_complexes(db, data.accessions)

    stats = search_service.calculate_cart_stats(complexes)
    scatter_data = search_service.generate_scatter_data(complexes)
    species_data = search_service.get_species_distribution(db, data.accessions)

    complexes_processed, visible_cols = search_service.process_results_for_view(
        complexes=complexes,
        cols=data.cols,
        sort=data.sort,
        desc=data.desc,
        plddt_center=data.plddt_center,
        plddt_chain=data.plddt_chain,
        custom_formula=data.custom_formula,
        ipsae_pae=data.ipsae_pae, ipsae_pair=data.ipsae_pair,
        ipsae_pae_2=data.ipsae_pae_2, ipsae_pair_2=data.ipsae_pair_2,
        ipsae_pae_3=data.ipsae_pae_3, ipsae_pair_3=data.ipsae_pair_3
    )

    stats_html = TEMPLATES.get_template("_cart_stats_partial.html").render({
        "request": request,
        "stats": stats
    })

    table_html = TEMPLATES.get_template("_results_table.html").render({
        "request": request, "complexes": complexes_processed, "page": 1, "params": request.query_params,
        "sort": data.sort, "desc": data.desc, "visible_cols": visible_cols,
        "plddt_center": data.plddt_center, "plddt_chain": data.plddt_chain, "custom_formula": data.custom_formula,
        "ipsae_pae": data.ipsae_pae, "ipsae_pair": data.ipsae_pair,
        "ipsae_pae_2": data.ipsae_pae_2, "ipsae_pair_2": data.ipsae_pair_2,
        "ipsae_pae_3": data.ipsae_pae_3, "ipsae_pair_3": data.ipsae_pair_3,
    })

    return JSONResponse({
        "stats_html": stats_html,
        "table_html": table_html,
        "chart_data": {
            "species": species_data,
            "scatter": scatter_data
        }
    })

@router.get("/search-results", response_class=HTMLResponse, include_in_schema=False)
def search_results(
        request: Request,
        page: int = 1,
        sort: str | None = "created",
        desc: bool = True,
        q: str = "",
        iptm_min: str = "", iptm_max: str = "",
        ptm_min: str = "", ptm_max: str = "",
        ranking_min: str = "", ranking_max: str = "",
        plddt_min: str = "", plddt_max: str = "",
        has_clash_exclude: bool = False,
        chain_count_min: str = "", chain_count_max: str = "",
        collection_id: str = "", oligomeric_state: str = "",
        chain_seq: Optional[List[str]] = Query(None),
        chain_match_type: Optional[List[str]] = Query(None),
        chain_iptm_min: Optional[List[str]] = Query(None),
        chain_iptm_max: Optional[List[str]] = Query(None),
        chain_ptm_min: Optional[List[str]] = Query(None),
        chain_ptm_max: Optional[List[str]] = Query(None),
        cols: Optional[List[str]] = Query(None),
        plddt_center: str = "",
        plddt_chain: str = "",
        custom_formula: str = "",
        ipsae_pae: int = 10, ipsae_pair: str = "MAX",
        ipsae_pae_2: int = 10, ipsae_pair_2: str = "MAX",
        ipsae_pae_3: int = 10, ipsae_pair_3: str = "MAX",
        db: Session = Depends(get_db),
):
    """Handles global and advanced search requests, returning a populated HTML template."""
    complexes_processed, visible_cols = search_service.execute_advanced_search(
        db=db, query_params=request.query_params,
        page=page, sort=sort, desc=desc, q=q,
        iptm_min=iptm_min, iptm_max=iptm_max, ptm_min=ptm_min, ptm_max=ptm_max,
        ranking_min=ranking_min, ranking_max=ranking_max, plddt_min=plddt_min, plddt_max=plddt_max,
        has_clash_exclude=has_clash_exclude, collection_id=collection_id, oligomeric_state=oligomeric_state,
        chain_count_min=chain_count_min, chain_count_max=chain_count_max,
        chain_seq=chain_seq, chain_match_type=chain_match_type,
        chain_iptm_min=chain_iptm_min, chain_iptm_max=chain_iptm_max,
        chain_ptm_min=chain_ptm_min, chain_ptm_max=chain_ptm_max,
        cols=cols, plddt_center=plddt_center, plddt_chain=plddt_chain, custom_formula=custom_formula,
        ipsae_pae=ipsae_pae, ipsae_pair=ipsae_pair,
        ipsae_pae_2=ipsae_pae_2, ipsae_pair_2=ipsae_pair_2,
        ipsae_pae_3=ipsae_pae_3, ipsae_pair_3=ipsae_pair_3
    )

    return TEMPLATES.TemplateResponse(
        "search_results.html",
        {
            "request": request, "complexes": complexes_processed, "page": page,
            "sort": sort, "desc": desc, "visible_cols": visible_cols,
            "plddt_center": plddt_center, "plddt_chain": plddt_chain, "custom_formula": custom_formula,
            "ipsae_pae": ipsae_pae, "ipsae_pair": ipsae_pair,
            "ipsae_pae_2": ipsae_pae_2, "ipsae_pair_2": ipsae_pair_2,
            "ipsae_pae_3": ipsae_pae_3, "ipsae_pair_3": ipsae_pair_3,
            "params": request.query_params, "querystring": urlencode(request.query_params.multi_items()),
        }
    )

@router.get("/show_collection/{collection_name}", response_class=HTMLResponse, include_in_schema=False)
def show_collection_page(
        collection_name: str,
        page: int = 1, sort: str | None = "created", desc: bool = True,
        cols: Optional[List[str]] = Query(None),
        plddt_center: str = "", plddt_chain: str = "", custom_formula: str = "",
        ipsae_pae: int = 10, ipsae_pair: str = "MAX",
        ipsae_pae_2: int = 10, ipsae_pair_2: str = "MAX",
        ipsae_pae_3: int = 10, ipsae_pair_3: str = "MAX",
        request: Request = None, db: Session = Depends(get_db),
):
    """Renders the detailed view for a specific collection."""
    context_data = collection_service.get_collection_view_data(
        db=db, collection_name=collection_name, page=page, sort=sort, desc=desc, cols=cols,
        plddt_center=plddt_center, plddt_chain=plddt_chain, custom_formula=custom_formula,
        ipsae_pae=ipsae_pae, ipsae_pair=ipsae_pair,
        ipsae_pae_2=ipsae_pae_2, ipsae_pair_2=ipsae_pair_2,
        ipsae_pae_3=ipsae_pae_3, ipsae_pair_3=ipsae_pair_3
    )

    return TEMPLATES.TemplateResponse(
        "show_collection.html",
        {
            "request": request, "page": page, "sort": sort or "created", "desc": desc,
            "plddt_center": plddt_center, "plddt_chain": plddt_chain, "custom_formula": custom_formula,
            "ipsae_pae": ipsae_pae, "ipsae_pair": ipsae_pair,
            "ipsae_pae_2": ipsae_pae_2, "ipsae_pair_2": ipsae_pair_2,
            "ipsae_pae_3": ipsae_pae_3, "ipsae_pair_3": ipsae_pair_3,
            "params": request.query_params, **context_data
        }
    )


@router.get("/complex/{accession}", response_class=HTMLResponse, include_in_schema=False)
def complex_detail(accession: str, request: Request, db: Session = Depends(get_db)):
    """Renders the detailed view for a single predicted complex."""
    context = complex_service.get_complex_detail_context(db, accession)
    if not context:
        raise HTTPException(404, "Complex not found")

    return TEMPLATES.TemplateResponse("complex.html", {"request": request, **context})


@router.get("/foldseek/{accession}", response_class=JSONResponse, include_in_schema=False)
async def foldseek_api(
        accession: str, chain: str | None = Query(None), keep_all: bool = Query(False),
        db: Session = Depends(get_db),
):
    """Triggers an async structural search against the Foldseek API."""
    try:
        result = await structural_search_service.handle_foldseek_request(db, accession, chain, keep_all)
        return result
    except ValueError as e:
        raise HTTPException(502, detail=str(e))


@router.get("/folddisco/{accession}", response_class=JSONResponse, include_in_schema=False)
async def folddisco_api(
        accession: str, database: List[str] = Query(..., description="List of databases"),
        mode_select: str = Query("auto", regex="^(auto|manual)$"), threshold: float = Query(6.0),
        custom_motif: Optional[str] = Query(None), db: Session = Depends(get_db),
):
    """Triggers an async FoldDisco search using auto or custom motifs."""
    try:
        result = await structural_search_service.handle_folddisco_request(
            db, accession, database, mode_select, threshold, custom_motif
        )
        return result
    except ValueError as e:
        raise HTTPException(502, detail=str(e))


# Simple Static Routes

@router.get("/advanced-results", response_class=HTMLResponse, include_in_schema=False)
def advanced_results(request: Request):
    qs = urlencode(list(request.query_params.multi_items()), doseq=True)
    return RedirectResponse(url="/search-results?" + qs, status_code=303)

@router.get("/quick-search", response_class=HTMLResponse, include_in_schema=False)
def quick_search_page(request: Request):
    qs = urlencode(list(request.query_params.multi_items()), doseq=True)
    return RedirectResponse(url="/search-results?" + qs, status_code=303)

@router.get("/download", response_class=HTMLResponse, include_in_schema=False)
def download_page(request: Request):
    return TEMPLATES.TemplateResponse("download.html", {"request": request})

@router.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    """
    Renders the about page with safe public hoster information.
    """
    context = {
        "request": request,
        "today": datetime.now().strftime("%Y"),
        "hoster_name": getattr(settings, "hoster_name", "Local Administrator"),
        "hoster_email": getattr(settings, "hoster_email", ""),
        "hoster_description": getattr(settings, "hoster_description", "")
    }
    return TEMPLATES.TemplateResponse("about.html", context)

@router.get("/faq", response_class=HTMLResponse, include_in_schema=False)
def faq_page(request: Request):
    return TEMPLATES.TemplateResponse("faq.html", {"request": request, "today": date.today().strftime("%Y-%m-%d")})

@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request, db: Session = Depends(get_db)):
    return TEMPLATES.TemplateResponse("index.html", {"request": request, "complexes": crud.list_complexes(db)})

@router.get("/collections", response_class=HTMLResponse, include_in_schema=False)
def collections_page(request: Request, db: Session = Depends(get_db)):
    return TEMPLATES.TemplateResponse("collections.html", {"request": request, "rows": crud.list_all_collections(db)})

@router.get("/status", response_class=JSONResponse, include_in_schema=False)
def check_status(accessions: List[str] = Query(...), db: Session = Depends(get_db)):
    """Lightweight endpoint for frontend polling to check processing status."""
    statuses = (
        db.query(models.Complex.accession, models.Complex.processing_status)
        .filter(models.Complex.accession.in_(accessions))
        .all()
    )
    return {acc: status for acc, status in statuses}