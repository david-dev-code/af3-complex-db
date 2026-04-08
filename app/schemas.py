from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ChainOut(BaseModel):
    """
    Output schema representing a single chain within a complex.
    """
    id: int
    sequence_length: int

    class Config:
        orm_mode = True


class CollectionOut(BaseModel):
    """
    Output schema for a collection grouping multiple complexes.
    """
    id: int
    name: str

    class Config:
        orm_mode = True


class ComplexOut(BaseModel):
    """
    Output schema for an AlphaFold complex, including metadata, summary scores, and associated chains.
    """
    accession: str
    version: str
    description: Optional[str] = None
    collection: Optional[CollectionOut] = None
    iptm: Optional[float] = None
    ptm: Optional[float] = None
    ranking_score: Optional[float] = None
    created_at: datetime
    chains: List[ChainOut] = Field(default_factory=list)

    class Config:
        orm_mode = True


class QuickSearch(BaseModel):
    """
    Schema for basic text-based search queries.
    """
    q: str = Field(min_length=1)


class ChainFilter(BaseModel):
    """
    Schema for applying specific filters to individual chains.
    """
    seq: str
    fuzzy: bool = False  # Include similar sequences?
    iptm_min: Optional[float] = None
    iptm_max: Optional[float] = None
    ptm_min: Optional[float] = None
    ptm_max: Optional[float] = None
    chain_len_min: Optional[int] = None
    chain_len_max: Optional[int] = None


class AdvancedSearch(BaseModel):
    """
    Schema for advanced, multi-parameter search queries covering global scores,
    chain counts, and date ranges.
    """
    accession: Optional[str] = None
    desc: Optional[str] = None

    iptm_min: Optional[float] = None
    iptm_max: Optional[float] = None
    ptm_min: Optional[float] = None
    ptm_max: Optional[float] = None
    ranking_min: Optional[float] = None
    ranking_max: Optional[float] = None
    plddt_min: Optional[float] = None
    plddt_max: Optional[float] = None

    has_clash_exclude: bool = False

    chain_count_min: Optional[int] = None
    chain_count_max: Optional[int] = None

    chain_filters: Optional[List[ChainFilter]] = None

    created_from: Optional[date] = None
    created_to: Optional[date] = None