from __future__ import annotations
from datetime import datetime, date
from typing import Optional, List

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, JSON, String, Date, UniqueConstraint, Column, Float, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """
    Common base class for all SQLAlchemy ORM models.
    """


class UniParcEntry(Base):
    """
    Represents a UniParc entry holding a unique sequence identifier (UPI).
    Acts as the central hub connecting sequences (Chains) to UniProt metadata.
    """
    __tablename__ = "uniparc_entry"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    upi: Mapped[str] = mapped_column(String, unique=True, index=True)

    accessions: Mapped[list["UniprotAccession"]] = relationship(
        back_populates="uniparc",
        cascade="all, delete-orphan"
    )

    chains: Mapped[list["Chain"]] = relationship(
        back_populates="uniparc",
        cascade="all, delete-orphan"
    )


class UniprotAccession(Base):
    """
    Represents a UniProt accession and its associated metadata,
    mapped to a specific UniParc UPI.
    """
    __tablename__ = "uniprot_accession"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    upi_id: Mapped[int] = mapped_column(
        ForeignKey("uniparc_entry.id", ondelete="CASCADE"), nullable=False, index=True
    )
    accession: Mapped[str] = mapped_column(String, index=True)

    # UniProt Metadata
    status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    protein_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    alternative_names: Mapped[Optional[list[str]]] = mapped_column(JSON)
    gene_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    function: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    organism: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    taxonomy: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    retrieval: Mapped[date] = mapped_column(Date, default=date.today)

    uniparc: Mapped["UniParcEntry"] = relationship(
        back_populates="accessions",
        foreign_keys=[upi_id],
    )

    __table_args__ = (
        UniqueConstraint("upi_id", "accession", name="uq_uniprot_per_upi"),
    )


class Collection(Base):
    """
    Represents a named collection grouping multiple complexes together.
    """
    __tablename__ = "collection"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)

    complexes: Mapped[List["Complex"]] = relationship(
        back_populates="collection",
        cascade="all, delete",
        passive_deletes=True
    )


class Complex(Base):
    """
    Represents an AlphaFold3 prediction run (a single model structure).
    Stores global scores, metadata, and handles relations to specific chains and interface scores.
    """
    __tablename__ = "complex"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    accession: Mapped[str] = mapped_column(unique=True)  # e.g., AF-CP-00001
    description: Mapped[Optional[str]]
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    processing_status: Mapped[str] = mapped_column(String, default="SUCCESS")
    submitted_from: Mapped[str]  # e.g., email or username
    version: Mapped[str]

    collection_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("collection.id", ondelete="SET NULL"), nullable=True
    )
    collection: Mapped[Optional[Collection]] = relationship(back_populates="complexes")

    # Summary Scores
    iptm: Mapped[Optional[float]]
    ptm: Mapped[Optional[float]]
    ranking_score: Mapped[Optional[float]]
    fraction_disordered: Mapped[Optional[float]]
    has_clash: Mapped[Optional[float]]
    mean_plddt: Mapped[Optional[float]]

    # Averages across all seeds/samples
    mean_iptm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mean_ptm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    submitted_seeds: Mapped[Optional[int]]
    submitted_models_per_seed: Mapped[Optional[int]]

    embedding: Mapped[Optional[List[float]]] = mapped_column(Vector(512))
    file_path: Mapped[str]  # Relative folder path in local storage

    chains: Mapped[List["Chain"]] = relationship(
        back_populates="complex",
        cascade="all, delete-orphan",
        passive_deletes=True
    )

    # ipSAE Score PAE Cutoffs
    ipsae_3 = Column(Float, nullable=True)
    ipsae_d0chn_3 = Column(Float, nullable=True)
    ipsae_d0dom_3 = Column(Float, nullable=True)
    iptm_d0chn_3 = Column(Float, nullable=True)
    ipsae_best_pair_3 = Column(String(16), nullable=True)

    ipsae_5 = Column(Float, nullable=True)
    ipsae_d0chn_5 = Column(Float, nullable=True)
    ipsae_d0dom_5 = Column(Float, nullable=True)
    iptm_d0chn_5 = Column(Float, nullable=True)
    ipsae_best_pair_5 = Column(String(16), nullable=True)

    ipsae_10 = Column(Float, nullable=True)
    ipsae_d0chn_10 = Column(Float, nullable=True)
    ipsae_d0dom_10 = Column(Float, nullable=True)
    iptm_d0chn_10 = Column(Float, nullable=True)
    ipsae_best_pair_10 = Column(String(16), nullable=True)

    ipsae_15 = Column(Float, nullable=True)
    ipsae_d0chn_15 = Column(Float, nullable=True)
    ipsae_d0dom_15 = Column(Float, nullable=True)
    iptm_d0chn_15 = Column(Float, nullable=True)
    ipsae_best_pair_15 = Column(String(16), nullable=True)

    ipsae_20 = Column(Float, nullable=True)
    ipsae_d0chn_20 = Column(Float, nullable=True)
    ipsae_d0dom_20 = Column(Float, nullable=True)
    iptm_d0chn_20 = Column(Float, nullable=True)
    ipsae_best_pair_20 = Column(String(16), nullable=True)

    pdockq = Column(Float, nullable=True)
    pdockq2 = Column(Float, nullable=True)
    lis = Column(Float, nullable=True)

    # Biophysical Properties
    bsa = Column(Float, nullable=True, comment="Buried Surface Area (A^2)")
    num_h_bonds = Column(Integer, nullable=True, comment="Number of Hydrogen Bonds")
    num_salt_bridges = Column(Integer, nullable=True, comment="Number of Salt Bridges")

    interface_scores = relationship(
        "InterfaceScore",
        back_populates="complex",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class InterfaceScore(Base):
    """
       Stores pairwise interface scores and metrics for specific chain pairs
       within a complex, evaluated at specific PAE and distance cutoffs.
       """
    __tablename__ = "interface_score"

    id = Column(Integer, primary_key=True)
    complex_id = Column(ForeignKey("complex.id", ondelete="CASCADE"), nullable=False, index=True)

    chain1 = Column(String, nullable=False)
    chain2 = Column(String, nullable=False)
    pae_cutoff = Column(Float, nullable=False)

    ipsae = Column(Float, nullable=True)
    ipsae_d0chn = Column(Float, nullable=True)
    ipsae_d0dom = Column(Float, nullable=True)
    iptm_d0chn = Column(Float, nullable=True)

    pdockq = Column(Float, nullable=True)
    pdockq2 = Column(Float, nullable=True)
    lis = Column(Float, nullable=True)

    n0res = Column(Integer, nullable=True)
    n0chn = Column(Integer, nullable=True)
    n0dom = Column(Integer, nullable=True)
    d0res = Column(Float, nullable=True)
    d0chn = Column(Float, nullable=True)
    d0dom = Column(Float, nullable=True)

    nres1 = Column(Integer, nullable=True)
    nres2 = Column(Integer, nullable=True)
    dist1 = Column(Integer, nullable=True)
    dist2 = Column(Integer, nullable=True)

    complex = relationship("Complex", back_populates="interface_scores")

    __table_args__ = (
        UniqueConstraint(
            "complex_id", "chain1", "chain2", "pae_cutoff",
            name="uq_interface_score"
        ),
    )


class Chain(Base):
    """
    Represents a single molecular chain within a predicted complex.
    Stores sequence data, chain-level metrics, and mappings to UniParc/UniProt.
    """
    __tablename__ = "chain"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    complex_id: Mapped[int] = mapped_column(ForeignKey("complex.id", ondelete="CASCADE"))
    sequence: Mapped[str]
    sequence_length: Mapped[int]

    protein_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    gene_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Tracks which accession was primarily assigned and how ('auto' or 'manual')
    primary_accession: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    mapping_method: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    upi_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("uniparc_entry.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    uniparc: Mapped["UniParcEntry"] = relationship(
        back_populates="chains",
        foreign_keys=[upi_id]
    )

    uniprot_mappings: Mapped[list["UniprotAccession"]] = relationship(
        "UniprotAccession",
        primaryjoin="Chain.upi_id==foreign(UniprotAccession.upi_id)",
        viewonly=True,
        lazy="selectin",
    )

    # Granular Metrics
    chain_iptm: Mapped[Optional[list]] = mapped_column(JSON)
    chain_ptm: Mapped[Optional[list]] = mapped_column(JSON)
    chain_pair_iptm: Mapped[Optional[list]] = mapped_column(JSON)
    chain_pair_pae_min: Mapped[Optional[list]] = mapped_column(JSON)
    chain_mean_plddt: Mapped[Optional[float]]

    residue_plddt: Mapped[Optional[list[int]]] = mapped_column(JSON)
    radius_plddt: Mapped[Optional[dict]] = mapped_column(JSON)

    complex: Mapped["Complex"] = relationship(back_populates="chains")
