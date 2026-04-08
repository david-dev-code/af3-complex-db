import re
import warnings
from pathlib import Path
from typing import Dict, List, Tuple, Set

import numpy as np
import biotite.structure as struc
import biotite.structure.io as strucio
from biotite.structure import sasa
from scipy.spatial.distance import cdist

from app.core.config import get_settings

warnings.filterwarnings("ignore", category=UserWarning, module="biotite")


def compute_biophysical_stats(cif_path: Path) -> Tuple[List[dict], Dict[str, List[int]]]:
    """
    Computes biophysical properties for a given CIF structure.
    Calculates BSA, heavy-atom hydrogen bonds, salt bridges, and interface residues.
    """
    settings = get_settings()
    print(f"[BIOPHYS] Loading structure from {cif_path}", flush=True)

    try:
        atom_array = strucio.load_structure(str(cif_path))
    except Exception as e:
        print(f"[BIOPHYS] Error loading CIF: {e}", flush=True)
        return [], {}

    if isinstance(atom_array, struc.AtomArrayStack):
        structure = atom_array[0]
    else:
        structure = atom_array

    if not hasattr(structure, "chain_id"):
        return [], {}

    mask_clean = (structure.element != "H") & (structure.hetero == False)
    structure_clean = structure[mask_clean]

    chain_ids = np.unique(structure_clean.chain_id)
    chains = sorted(chain_ids)
    print(f"[BIOPHYS] Chains to analyze: {chains} (Total atoms: {len(structure_clean)})", flush=True)

    pair_stats = []
    interface_residues_map: Dict[str, Set[int]] = {cid: set() for cid in chains}

    for i in range(len(chains)):
        for j in range(i + 1, len(chains)):
            c1 = chains[i]
            c2 = chains[j]

            atoms1 = structure_clean[structure_clean.chain_id == c1]
            atoms2 = structure_clean[structure_clean.chain_id == c2]

            if len(atoms1) == 0 or len(atoms2) == 0:
                continue

            complex_ab = atoms1 + atoms2
            sasa_1 = _calc_sasa(atoms1)
            sasa_2 = _calc_sasa(atoms2)
            sasa_complex = _calc_sasa(complex_ab)
            bsa_val = max(0.0, (sasa_1 + sasa_2 - sasa_complex) / 2.0)

            c1_polar = atoms1[np.isin(atoms1.element, ["N", "O"])]
            c2_polar = atoms2[np.isin(atoms2.element, ["N", "O"])]

            num_h_bonds = 0
            if len(c1_polar) > 0 and len(c2_polar) > 0:
                d_polar = cdist(c1_polar.coord, c2_polar.coord)
                num_h_bonds = np.count_nonzero(d_polar <= settings.threshold_h_bond)

            def get_sb_coords(atoms):
                mask_a = ((atoms.res_name == "ASP") & np.isin(atoms.atom_name, ["OD1", "OD2"])) | \
                         ((atoms.res_name == "GLU") & np.isin(atoms.atom_name, ["OE1", "OE2"]))
                mask_c = ((atoms.res_name == "LYS") & (atoms.atom_name == "NZ")) | \
                         ((atoms.res_name == "ARG") & np.isin(atoms.atom_name, ["NH1", "NH2"])) | \
                         ((atoms.res_name == "HIS") & np.isin(atoms.atom_name, ["ND1", "NE2"]))
                return atoms.coord[mask_a], atoms.coord[mask_c]

            c1_an, c1_cat = get_sb_coords(atoms1)
            c2_an, c2_cat = get_sb_coords(atoms2)

            num_salt = 0
            if len(c1_an) > 0 and len(c2_cat) > 0:
                d = cdist(c1_an, c2_cat)
                num_salt += np.count_nonzero(d < settings.threshold_salt_bridge)
            if len(c1_cat) > 0 and len(c2_an) > 0:
                d = cdist(c1_cat, c2_an)
                num_salt += np.count_nonzero(d < settings.threshold_salt_bridge)

            dists = cdist(atoms1.coord, atoms2.coord)
            min_dist_global = dists.min() if dists.size > 0 else 999.0

            min_dists_1 = dists.min(axis=1)
            contact_mask_1 = min_dists_1 < settings.threshold_interface
            min_dists_2 = dists.min(axis=0)
            contact_mask_2 = min_dists_2 < settings.threshold_interface

            res_ids_1 = np.unique(atoms1.res_id[contact_mask_1]).tolist()
            res_ids_2 = np.unique(atoms2.res_id[contact_mask_2]).tolist()

            interface_residues_map[c1].update(res_ids_1)
            interface_residues_map[c2].update(res_ids_2)

            print(
                f"[BIOPHYS] Pair {c1}-{c2}: MinDist={min_dist_global:.2f}A, BSA={bsa_val:.1f}, IF_Res={len(res_ids_1)}/{len(res_ids_2)}",
                flush=True)

            pair_stats.append({
                "chain1": c1,
                "chain2": c2,
                "bsa": round(bsa_val, 2),
                "num_h_bonds": int(num_h_bonds),
                "num_salt_bridges": int(num_salt)
            })

    final_res_map = {k: sorted(list(v)) for k, v in interface_residues_map.items()}
    return pair_stats, final_res_map


def _calc_sasa(atoms) -> float:
    """
    Calculates Solvent Accessible Surface Area (SASA) using Biotite.
    """
    return float(np.sum(sasa(atoms, probe_radius=1.4)))


def get_interface_motif(cif_path: Path, threshold: float = None) -> str:
    """
    Identifies interface residues across all chains based on a distance threshold.
    Returns a formatted motif string.
    """
    if threshold is None:
        threshold = get_settings().threshold_interface

    try:
        atom_array = strucio.load_structure(str(cif_path))
        if isinstance(atom_array, struc.AtomArrayStack):
            structure = atom_array[0]
        else:
            structure = atom_array
    except Exception as e:
        print(f"[BIOPHYS] Error loading CIF for motif: {e}", flush=True)
        return ""

    if not hasattr(structure, "chain_id"):
        return ""

    mask = (structure.element != "H") & (structure.hetero == False)
    structure = structure[mask]

    chain_ids = np.unique(structure.chain_id)
    chains = sorted(chain_ids)

    interface_residues = set()

    for i in range(len(chains)):
        for j in range(i + 1, len(chains)):
            c1 = chains[i]
            c2 = chains[j]

            atoms1 = structure[structure.chain_id == c1]
            atoms2 = structure[structure.chain_id == c2]

            if len(atoms1) == 0 or len(atoms2) == 0:
                continue

            dists = cdist(atoms1.coord, atoms2.coord)

            mask_1 = dists.min(axis=1) < threshold
            mask_2 = dists.min(axis=0) < threshold

            res_ids_1 = np.unique(atoms1.res_id[mask_1])
            for rid in res_ids_1:
                interface_residues.add(f"{c1}{rid}")

            res_ids_2 = np.unique(atoms2.res_id[mask_2])
            for rid in res_ids_2:
                interface_residues.add(f"{c2}{rid}")

    def sort_key(s: str):
        match = re.match(r"([a-zA-Z]+)(\d+)", s)
        if match:
            return match.group(1), int(match.group(2))
        return s, 0

    sorted_res = sorted(list(interface_residues), key=sort_key)
    motif_string = ",".join(sorted_res)

    print(f"[BIOPHYS] Interface Motif (th={threshold}A): Found {len(sorted_res)} residues.", flush=True)
    return motif_string