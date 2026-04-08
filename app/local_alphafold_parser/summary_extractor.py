from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, Optional
import json
import numpy as np


class SummaryExtractor:
    """
    Lazy wrapper for *_summary_confidences.json*.
    """

    def __init__(self, json_path: Path | str):
        """
        Initialize the wrapper and load the JSON data.
        """
        self._data: Dict[str, Any] = json.loads(Path(json_path).read_text())
        self._cache: Dict[str, np.ndarray] = {}

    # Simple scalar getters

    def get_iptm(self) -> Optional[float]:
        """
        Get the iptm value.
        """
        return self._data.get("iptm")

    def get_ptm(self) -> Optional[float]:
        """
        Get the ptm value.
        """
        return self._data.get("ptm")

    def get_ranking_score(self) -> Optional[float]:
        """
        Get the ranking score.
        """
        return self._data.get("ranking_score")

    # Array helpers

    def _arr(self, key: str, nd: int) -> np.ndarray:
        """
        Retrieve and cache an array of a specific dimension.
        """
        if key not in self._cache:
            arr = np.asarray(self._data[key], np.float32)
            if arr.ndim != nd:
                raise ValueError(f"{key} expected {nd}-D")
            self._cache[key] = arr
        return self._cache[key]

    # 1-D arrays

    def get_chain_iptm(self) -> np.ndarray:
        """
        Get the 1-D array for chain_iptm.
        """
        return self._arr("chain_iptm", 1)

    def get_chain_ptm(self) -> np.ndarray:
        """
        Get the 1-D array for chain_ptm.
        """
        return self._arr("chain_ptm", 1)

    # 2-D arrays

    def get_chain_pair_iptm(self) -> np.ndarray:
        """
        Get the 2-D array for chain_pair_iptm.
        """
        return self._arr("chain_pair_iptm", 2)

    def get_chain_pair_pae_min(self) -> np.ndarray:
        """
        Get the 2-D array for chain_pair_pae_min.
        """
        return self._arr("chain_pair_pae_min", 2)

    def fraction_disordered(self) -> Optional[float]:
        """
        Get the fraction disordered value.
        """
        return self._data.get("fraction_disordered")

    def has_clash(self) -> Optional[float]:
        """
        Check if there is a clash.
        """
        return self._data.get("has_clash")