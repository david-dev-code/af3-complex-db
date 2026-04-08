# full_data_extractor.py
from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, Optional, Union
import json
import numpy as np


Chain = Union[int, str]        # int-index 0,1,... or label 'A','B',...

class FullDataExtractor:
    """
    Lazy wrapper for a server file *_full_data_0.json

    Contains fields:
      - contact_probs         (NxN)
      - pae                   (NxN)
      - atom_plddts           (N,)
      - atom_chain_ids        (N,)

    Getters (all lazy):
      get_contact_matrix()
      get_pae_matrix()
      get_plddt_vector()
      get_atom_chain_ids()
      get_chain_plddt(id | idx)

    Token fields are intentionally ignored.
    """

    def __init__(self, json_path: Path | str):
        """Initialize the extractor with the path to the JSON file."""
        self._json_path = Path(json_path).expanduser()
        self._data: Dict[str, Any] = json.loads(self._json_path.read_text())

        # Lazy caches
        self._contact: Optional[np.ndarray] = None
        self._pae:     Optional[np.ndarray] = None
        self._plddt:   Optional[np.ndarray] = None
        self._chains:  Optional[np.ndarray] = None

        # List of unique chain labels (in order of appearance)
        self._unique_labels: Optional[list[str]] = None


    # Main matrices
    def get_contact_matrix(self) -> np.ndarray:
        """Get the contact probability matrix."""
        if self._contact is None:
            self._contact = np.asarray(self._data["contact_probs"], np.float32)
        return self._contact

    def get_pae_matrix(self) -> np.ndarray:
        """Get the Predicted Aligned Error (PAE) matrix."""
        if self._pae is None:
            self._pae = np.asarray(self._data["pae"], np.float32)
        return self._pae


    # Per-atom vectors
    def get_plddt_vector(self) -> np.ndarray:
        """Get the per-atom pLDDT vector."""
        if self._plddt is None:
            self._plddt = np.asarray(self._data["atom_plddts"], np.float32)
        return self._plddt

    def get_atom_chain_ids(self) -> np.ndarray:
        """Get the array of atom chain IDs."""
        if self._chains is None:
            self._chains = np.asarray(self._data["atom_chain_ids"])
        return self._chains


    # Chain-specific pLDDT
    def get_chain_plddt(self, chain_id: Chain) -> Optional[np.ndarray]:
        """Get the pLDDT vector for a specific chain by ID or index."""
        plddt  = self.get_plddt_vector()
        chains = self.get_atom_chain_ids()

        # Build unique label list once
        if self._unique_labels is None:
            _, idx = np.unique(chains, return_index=True)
            self._unique_labels = list(chains[np.sort(idx)])

        # Map int index to label
        if isinstance(chain_id, int):
            try:
                chain_id = self._unique_labels[chain_id]
            except IndexError:
                raise IndexError(f"Chain index {chain_id} out of range")

        return plddt[chains == chain_id]