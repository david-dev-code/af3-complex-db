from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, Optional
import json
import numpy as np


class ConfidenceExtractor:
    """
    Lazy wrapper for *_confidences.json*.

    Getters (all lazy):
    -------------------
    get_contact_matrix()   -> (N, N) float32
    get_pae_matrix()       -> (N, N) float32
    get_contact_sides()    -> (N, N) float32 | None
    get_plddt_vector()     -> (N,)  float32 | None
    get_atom_chain_ids()   -> (N,)  str/int | None
    get_chain_plddt(id)    -> (k,)  float32 | None
    """

    def __init__(self, json_path: Path | str):
        """Initializes the extractor and parses the JSON file."""
        self._conf: Dict[str, Any] = json.loads(Path(json_path).read_text())

        # Lazy cache fields
        self._contact:   Optional[np.ndarray] = None
        self._pae:       Optional[np.ndarray] = None
        self._plddt:     Optional[np.ndarray] = None
        self._chains:    Optional[np.ndarray] = None
        self._c_sides:   Optional[np.ndarray] = None

    # Main matrices
    def get_contact_matrix(self) -> np.ndarray:
        """Returns the contact probability matrix."""
        if self._contact is None:
            self._contact = np.asarray(self._conf["contact_probs"], np.float32)
        return self._contact

    def get_pae_matrix(self) -> np.ndarray:
        """Returns the predicted aligned error (PAE) matrix."""
        if self._pae is None:
            key = "pae" if "pae" in self._conf else "predicted_aligned_error"
            self._pae = np.asarray(self._conf[key], np.float32)
        return self._pae

    def get_contact_sides(self) -> Optional[np.ndarray]:
        """Returns the contact sides matrix, if available."""
        if self._c_sides is None and "contact_sides" in self._conf:
            self._c_sides = np.asarray(self._conf["contact_sides"], np.float32)
        return self._c_sides

    # Per-atom vectors
    def get_plddt_vector(self) -> Optional[np.ndarray]:
        """Returns the per-atom pLDDT vector, if available."""
        if self._plddt is None and "atom_plddts" in self._conf:
            self._plddt = np.asarray(self._conf["atom_plddts"], np.float32)
        return self._plddt

    def get_atom_chain_ids(self) -> Optional[np.ndarray]:
        """Returns the per-atom chain IDs, if available."""
        if self._chains is None and "atom_chain_ids" in self._conf:
            self._chains = np.asarray(self._conf["atom_chain_ids"])
        return self._chains

    def get_chain_plddt(self, chain_id: str | int):
        """
        Returns the pLDDT values for a specific chain.
        Accepts either a chain letter (e.g., 'A') or an integer index.
        """
        plddt  = self.get_plddt_vector()
        chains = self.get_atom_chain_ids()
        if plddt is None or chains is None:
            return None

        # Convert integer index to chain letter
        if isinstance(chain_id, int):
            # Get unique chain order of appearance
            seen, order = set(), []
            for ch in chains:
                if ch not in seen:
                    order.append(ch)
                    seen.add(ch)
            if chain_id >= len(order):
                raise IndexError(f"Chain index {chain_id} out of range.")
            chain_id = order[chain_id]

        return plddt[chains == chain_id]