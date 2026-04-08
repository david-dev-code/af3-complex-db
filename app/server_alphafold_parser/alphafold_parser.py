from __future__ import annotations
from pathlib import Path
from typing import List, Tuple, Dict, Union
import numpy as np

from app.server_alphafold_parser.full_data_extractor   import FullDataExtractor
from app.server_alphafold_parser.summary_extractor     import SummaryExtractor
from app.server_alphafold_parser.cif_extractor     import CifExtractor

Idx = Union[int, str]

def _find_jsons(folder: Path) -> Tuple[Path, Path]:
    """Finds and returns the paths to the full data and summary JSON files."""
    folder = folder.expanduser().resolve()
    data  = next(folder.glob("*_full_data_0.json"),  None)
    summ  = next(folder.glob("*_summary_confidences_0.json"), None)
    if not (data and summ):
        print(folder)

        folder = Path(folder).expanduser().resolve()
        for p in folder.rglob("*"):
            if p.is_dir():
                print("  [DIR]  %s", p.relative_to(folder))
            else:
                print("  [FILE] %s", p.relative_to(folder))

        raise FileNotFoundError("AF3 JSON files missing")
    return data, summ


class AlphaFoldParser:
    """High-level API for AlphaFold-3 outputs (including seeds/samples & ranking)."""

    def __init__(self, folder: Path | str):
        """Initializes the parser with the given output folder."""
        self._folder = Path(folder).expanduser().resolve()
        d_json, s_json = _find_jsons(self._folder)
        cif_file = next(self._folder.glob("*_model_0.cif"), None)

        self._cif = CifExtractor(cif_file)
        self._summary = SummaryExtractor(s_json)
        self._full = FullDataExtractor(d_json)

    # ──────────────────────────────────────────────────────────────
    # Helper methods
    # ──────────────────────────────────────────────────────────────
    def _to_index(self, idx: Idx) -> int:
        """Converts a chain ID (str) or index (int) to an integer index."""
        if isinstance(idx, int):
            return idx
        ids = self.get_chain_ids()
        return ids.index(idx)

    # ──────────────────────────────────────────────────────────────
    # Sequence and chain API
    # ──────────────────────────────────────────────────────────────
    def get_chain_ids(self) -> List[str]:
        """Returns a list of all chain IDs."""
        return self._cif.get_chain_ids()

    def get_sequence(self, idx_or_id: Idx = 0) -> str:
        """Returns the sequence for a given chain ID or index."""
        if isinstance(idx_or_id, int):
            cid = self.get_chain_ids()[idx_or_id]
        else:
            cid = idx_or_id
        return self._cif.get_sequence(cid)

    def get_chain_length(self, idx_or_id: Idx = 0) -> int:
        """Returns the length of the sequence for a given chain ID or index."""
        return len(self.get_sequence(idx_or_id))

    # ──────────────────────────────────────────────────────────────
    # Contact & PAE matrices
    # ──────────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────────
    # Mean scores
    # ──────────────────────────────────────────────────────────────
    def get_mean_scores(self) -> dict[str, float | None]:
        """
        Attempts to find all *_summary_confidences_*.json files in the folder
        (not just _0) to calculate an average value.
        """
        # Search for all summary files (e.g., fold_..._summary_confidences_0.json, _1.json etc.)
        all_summaries = list(self._folder.glob("*_summary_confidences_*.json"))

        if not all_summaries:
            # Fallback: Should not happen since __init__ already found _0
            return {"mean_iptm": self.get_iptm(), "mean_ptm": self.get_ptm()}

        iptms = []
        ptms = []

        for p in all_summaries:
            try:
                # Briefly instantiate an extractor for each found file
                ext = SummaryExtractor(p)

                # Note: Pay attention to method names in SummaryExtractor
                # Used in the code above: .get_iptm() and .ptm()
                v_iptm = ext.get_iptm()
                v_ptm = ext.ptm()

                if v_iptm is not None: iptms.append(v_iptm)
                if v_ptm is not None:  ptms.append(v_ptm)
            except Exception:
                # Ignore faulty/empty JSONs
                continue

        res = {}
        if iptms:
            res["mean_iptm"] = float(np.mean(iptms))
        else:
            res["mean_iptm"] = None

        if ptms:
            res["mean_ptm"] = float(np.mean(ptms))
        else:
            res["mean_ptm"] = None

        return res

    def get_contact_matrix(self):
        """Returns the contact matrix."""
        return self._full.get_contact_matrix()

    def get_pae_matrix(self):
        """Returns the PAE matrix."""
        return self._full.get_pae_matrix()

    def get_plddt_vector(self):
        """Returns the pLDDT vector."""
        return self._full.get_plddt_vector()

    def get_chain_plddt(self, cid):
        """Returns the pLDDT vector for a specific chain."""
        return self._full.get_chain_plddt(cid)


    # Summary

    def get_ptm(self) -> float | None:
        """Returns the pTM score."""
        return self._summary.get_ptm()

    def get_fraction_disordered(self) -> float | None:
        """Returns the fraction of disordered regions."""
        return self._summary.fraction_disordered()

    def get_has_clash(self) -> float | None:
        """Returns whether a clash was detected."""
        return self._summary.has_clash()


    # Global summary

    def get_iptm(self):
        """Returns the ipTM score."""
        return self._summary.get_iptm()

    def get_ranking_score(self):
        """Returns the ranking score."""
        return self._summary.get_ranking_score()

    def get_chain_iptm(self, idx: Idx) -> float:
        """Returns the ipTM score for a specific chain."""
        return float(self._summary.get_chain_iptm()[self._to_index(idx)])

    def get_chain_ptm(self, idx: Idx) -> float:
        """Returns the pTM score for a specific chain."""
        return float(self._summary.get_chain_ptm()[self._to_index(idx)])


    # Summary - chain-pair values

    def get_chain_pair_iptm(
            self,
            chain_a: Idx,
            chain_b: Idx | None = None,
    ):
        """
        Returns chain-pair ipTM values.
        - One argument -> List of all ipTM values from chain_a to all chains.
        - Two arguments -> ipTM (float) for exactly this pair.
        """
        arr = self._summary.get_chain_pair_iptm()
        ia = self._to_index(chain_a)

        if chain_b is None:  # Entire row
            return arr[ia, :].astype(float).tolist()
        ib = self._to_index(chain_b)
        return float(arr[ia, ib])

    def get_chain_pair_pae_min(
            self,
            chain_a: Idx,
            chain_b: Idx | None = None,
    ):
        """
        Returns chain-pair minimum PAE values.
        - One argument -> List of minimum PAE values from chain_a to all chains.
        - Two arguments -> Single PAE value.
        """
        arr = self._summary.get_chain_pair_pae_min()
        ia = self._to_index(chain_a)

        if chain_b is None:  # Entire row
            return arr[ia, :].astype(float).tolist()
        ib = self._to_index(chain_b)
        return float(arr[ia, ib])

    def get_num_seeds_and_samples(self) -> tuple[int, int]:
        """Returns the number of seeds and samples (default 0, 0 for server output)."""
        # Only 0, 0 for the server-output
        return 0, 0
