from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from Bio.PDB import MMCIFParser, Polypeptide
from Bio.Data.IUPACData import protein_letters_3to1


class CifExtractor:
    """
    BioPython CIF-Extractor.

    Returns:
      - Sequences per chain
      - Per-residue pLDDT from B-factors
      - Per-residue radius-smoothed pLDDT for standard radii
        (neighbors via CA/residue-centroid distance, across all chains)
    """

    def __init__(self, cif_path: Path | str):
        """Initializes the extractor and parses the CIF file."""
        self._cif_path = Path(cif_path).expanduser().resolve()
        if not self._cif_path.exists():
            raise FileNotFoundError(self._cif_path)

        parser = MMCIFParser(QUIET=True)
        structure = parser.get_structure("model", self._cif_path)

        self._seq_strings: Dict[str, str] = {}
        self._res_plddt: Dict[str, List[int]] = {}
        self._res_coords: Dict[str, List[np.ndarray]] = {}

        for model in structure:
            for chain in model:
                seq, coords, plddt = self._chain_to_seq_coords_plddt(chain)
                if not seq:
                    continue

                self._seq_strings[chain.id] = seq
                self._res_coords[chain.id] = coords
                self._res_plddt[chain.id]  = plddt

                if len(coords) != len(seq) or len(plddt) != len(seq):
                    raise ValueError(
                        f"Chain {chain.id}: length mismatch "
                        f"seq={len(seq)} coords={len(coords)} plddt={len(plddt)}"
                    )
            break  # Process only the first model

        self._radius_cache: Optional[Dict[str, Dict[str, List[int]]]] = None

    @staticmethod
    def _chain_to_seq_coords_plddt(chain) -> Tuple[str, List[np.ndarray], List[int]]:
        """
        Extracts sequence, coordinates, and pLDDT for a given chain.

        Returns:
          seq: 1-letter sequence string.
          coords: Coordinates per standard residue (CA if available, else centroid).
          plddt: pLDDT per residue (mean B-Factor).
        """
        # Sequence extraction
        builder = Polypeptide.PPBuilder()
        peptides = builder.build_peptides(chain)
        if peptides:
            seq = "".join(str(pp.get_sequence()) for pp in peptides)
        else:
            seq_chars = []
            for res in chain:
                if res.id[0] != " ":
                    continue
                aa = protein_letters_3to1.get(res.resname.upper(), "X")
                seq_chars.append(aa)
            seq = "".join(seq_chars)

        # Coordinates and pLDDT extraction
        coords: List[np.ndarray] = []
        plddt: List[int] = []

        for res in chain:
            if res.id[0] != " ":
                continue

            vals = []
            atom_coords = []
            for atom in res.get_atoms():
                try:
                    vals.append(float(atom.get_bfactor()))
                    atom_coords.append(atom.get_coord())
                except Exception:
                    continue

            if not vals or not atom_coords:
                raise ValueError(f"No atoms/B-factors found for residue {res.id} in chain {chain.id}")

            plddt.append(int(round(sum(vals) / len(vals))))

            # Prefer CA atoms (BioPython: res["CA"], not res.get)
            ca_atom = None
            if "CA" in res:
                try:
                    ca_atom = res["CA"]
                except KeyError:
                    ca_atom = None

            if ca_atom is not None:
                coords.append(ca_atom.get_coord())
            else:
                coords.append(np.mean(np.array(atom_coords), axis=0))

        return seq, coords, plddt

    def compute_radius_plddt(self, radii: List[float]) -> Dict[str, Dict[str, List[int]]]:
        """
        Precomputes radius-smoothed pLDDT arrays.

        For each residue i, calculates the mean pLDDT of all residues
        (across all chains) whose CA/centroid is within 'radius' Angstroms.
        """
        # Cache based on the list of radii (order/values)
        key = tuple(float(r) for r in radii)
        if self._radius_cache is not None and self._radius_cache.get("_key") == key:
            return {k: v for k, v in self._radius_cache.items() if k != "_key"}

        # Global residue listing
        global_entries = []  # format: (chain_id, local_idx, coord, plddt)
        for cid in self.get_chain_ids():
            for i, (coord, p) in enumerate(zip(self._res_coords[cid], self._res_plddt[cid])):
                global_entries.append((cid, i, coord, p))

        coords = np.array([e[2] for e in global_entries], dtype=float)
        pvals  = np.array([e[3] for e in global_entries], dtype=float)

        # Prepare output container
        out: Dict[str, Dict[str, List[int]]] = {
            cid: {str(int(r)): [0]*len(self._seq_strings[cid]) for r in radii}
            for cid in self.get_chain_ids()
        }

        # Distance matrix calculation on the fly
        for gi, (cid, local_i, coord_i, _) in enumerate(global_entries):
            dists = np.linalg.norm(coords - coord_i, axis=1)

            for r in radii:
                mask = dists <= r
                mean_val = float(pvals[mask].mean()) if mask.any() else 0.0
                out[cid][str(int(r))][local_i] = int(round(mean_val))

        self._radius_cache = {"_key": key, **out}
        return out


    # Public API

    def get_chain_ids(self) -> List[str]:
        """Returns a list of all chain IDs."""
        return list(self._seq_strings.keys())

    def get_sequence(self, chain_id: str) -> str:
        """Returns the amino acid sequence for a specific chain."""
        if chain_id not in self._seq_strings:
            raise KeyError(f"Chain '{chain_id}' not found in CIF.")
        return self._seq_strings[chain_id]

    def get_chain_length(self, chain_id: str) -> int:
        """Returns the sequence length for a specific chain."""
        return len(self.get_sequence(chain_id))

    def get_all_sequences(self) -> Dict[str, str]:
        """Returns a dictionary mapping chain IDs to their sequences."""
        return self._seq_strings.copy()

    def get_residue_plddt(self, chain_id: str) -> List[int]:
        """Returns a list of per-residue pLDDT values for a specific chain."""
        if chain_id not in self._res_plddt:
            raise KeyError(f"Chain '{chain_id}' pLDDT not found in CIF.")
        return self._res_plddt[chain_id]
