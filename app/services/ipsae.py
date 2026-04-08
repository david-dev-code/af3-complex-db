"""
AF3-only ipSAE (plus pDockQ, pDockQ2, LIS) calculation module.
Based on Dunbrack et al. ipsae.py (MIT License).
View: https://github.com/DunbrackLab/IPSAE
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Dict, List, Tuple, Any, Iterable

import numpy as np

np.set_printoptions(threshold=np.inf)

REVIEWED_FLAG = "UniProtKB reviewed (Swiss-Prot)"

def ptm_func(x: float, d0: float) -> float:
    return 1.0 / (1 + (x / d0) ** 2.0)

ptm_func_vec = np.vectorize(ptm_func)

def calc_d0(L: float, pair_type: str) -> float:
    L = float(L)
    min_value = 2.0 if pair_type == "nucleic_acid" else 1.0
    if L > 27:
        d0 = 1.24 * (L - 15) ** (1.0 / 3.0) - 1.8
    else:
        d0 = 1.0
    return max(min_value, d0)

def calc_d0_array(L: np.ndarray, pair_type: str) -> np.ndarray:
    L = np.array(L, dtype=float)
    L = np.maximum(26, L)
    min_value = 2.0 if pair_type == "nucleic_acid" else 1.0
    return np.maximum(min_value, 1.24 * (L - 15) ** (1.0 / 3.0) - 1.8)

def init_chainpairdict_zeros(chainlist: np.ndarray) -> Dict[str, Dict[str, float]]:
    return {c1: {c2: 0 for c2 in chainlist if c1 != c2} for c1 in chainlist}

def init_chainpairdict_npzeros(chainlist: np.ndarray, arraysize: int) -> Dict[str, Dict[str, np.ndarray]]:
    return {c1: {c2: np.zeros(arraysize) for c2 in chainlist if c1 != c2} for c1 in chainlist}

def init_chainpairdict_set(chainlist: np.ndarray) -> Dict[str, Dict[str, set]]:
    return {c1: {c2: set() for c2 in chainlist if c1 != c2} for c1 in chainlist}

def classify_chains(chains: np.ndarray, residue_types: np.ndarray) -> Dict[str, str]:
    nuc_residue_set = {"DA", "DC", "DT", "DG", "A", "C", "U", "G"}
    types = {}
    for ch in np.unique(chains):
        idx = np.where(chains == ch)[0]
        res = residue_types[idx]
        types[ch] = "nucleic_acid" if any(r in nuc_residue_set for r in res) else "protein"
    return types

def parse_cif_atom_line(line: str, fielddict: dict) -> Dict[str, Any] | None:
    parts = line.split()
    def g(k): return parts[fielddict[k]]

    atom_num = int(g("id"))
    atom_name = g("label_atom_id")
    residue_name = g("label_comp_id")
    chain_id = g("label_asym_id")
    residue_seq_num = g("label_seq_id")

    if residue_seq_num == ".": return None

    return dict(
        atom_num=atom_num, atom_name=atom_name, residue_name=residue_name,
        chain_id=chain_id, residue_seq_num=int(residue_seq_num),
        x=float(g("Cartn_x")), y=float(g("Cartn_y")), z=float(g("Cartn_z"))
    )

def _read_af3_structure_tokens(cif_path: Path) -> Tuple[list, list, list, list, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    residues, cb_residues, chains, token_mask = [], [], [], []
    residue_set = {"ALA", "ARG", "ASN", "ASP", "CYS", "GLN", "GLU", "GLY", "HIS", "ILE", "LEU", "LYS", "MET", "PHE", "PRO", "SER", "THR", "TRP", "TYR", "VAL", "DA", "DC", "DT", "DG", "A", "C", "U", "G"}

    atomsitefield_num = 0
    atomsitefield_dict = {}

    with open(cif_path, "r") as fh:
        for line in fh:
            if line.startswith("_atom_site."):
                (_, fieldname) = line.strip().split(".")
                atomsitefield_dict[fieldname] = atomsitefield_num
                atomsitefield_num += 1
                continue

            if not (line.startswith("ATOM") or line.startswith("HETATM")):
                continue

            atom = parse_cif_atom_line(line, atomsitefield_dict)
            if atom is None:
                token_mask.append(0)
                continue

            if atom["atom_name"] == "CA" or "C1" in atom["atom_name"]:
                token_mask.append(1)
                residues.append({
                    "atom_num": atom["atom_num"], "coor": np.array([atom["x"], atom["y"], atom["z"]]),
                    "res": atom["residue_name"], "chainid": atom["chain_id"], "resnum": atom["residue_seq_num"],
                    "residue": f"{atom['residue_name']:3}   {atom['chain_id']:3} {atom['residue_seq_num']:4}"
                })
                chains.append(atom["chain_id"])

            if (atom["atom_name"] == "CB" or "C3" in atom["atom_name"] or (atom["residue_name"] == "GLY" and atom["atom_name"] == "CA")):
                cb_residues.append({
                    "atom_num": atom["atom_num"], "coor": np.array([atom["x"], atom["y"], atom["z"]]),
                    "res": atom["residue_name"], "chainid": atom["chain_id"], "resnum": atom["residue_seq_num"]
                })

            if (atom["atom_name"] != "CA" and "C1" not in atom["atom_name"] and atom["residue_name"] not in residue_set):
                token_mask.append(0)

    numres = len(residues)
    if numres == 0:
        return residues, cb_residues, chains, [], np.array([]), np.array([]), np.array([[]]), np.array([], dtype=bool)

    CA_atom_num = np.array([r["atom_num"] - 1 for r in residues], dtype=int)
    CB_atom_num = np.array([r["atom_num"] - 1 for r in cb_residues], dtype=int) if cb_residues else CA_atom_num.copy()
    coordinates = np.array([r["coor"] for r in (cb_residues if cb_residues else residues)])
    chains = np.array(chains)
    residue_types = np.array([r["res"] for r in residues])


    token_array = np.array(token_mask, dtype=bool)
    distances = np.sqrt(((coordinates[:, None, :] - coordinates[None, :, :]) ** 2).sum(axis=2))

    return residues, cb_residues, chains, residue_types, CA_atom_num, CB_atom_num, distances, token_array

def compute_ipsae_scores(
        conf_json_path: Path | str,
        cif_path: Path | str,
        pae_cutoff: float = 10.0
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:

    internal_dist_cutoff = 10.0
    conf_json_path = Path(conf_json_path)
    cif_path = Path(cif_path)

    residues, cb_residues, chains, residue_types, CA_atom_num, CB_atom_num, distances, token_array = \
        _read_af3_structure_tokens(cif_path)

    numres = len(residues)
    if numres == 0:
        return [], dict()

    unique_chains = np.unique(chains)
    chain_dict = classify_chains(chains, residue_types)
    chain_pair_type = init_chainpairdict_zeros(unique_chains)

    for c1 in unique_chains:
        for c2 in unique_chains:
            if c1 == c2: continue
            chain_pair_type[c1][c2] = "nucleic_acid" if "nucleic_acid" in (chain_dict[c1], chain_dict[c2]) else "protein"

    with open(conf_json_path, "r") as fh:
        data = json.load(fh)

    atom_plddts = np.array(data.get("atom_plddts", []), dtype=float)
    if atom_plddts.size > 0 and np.nanmax(atom_plddts) <= 1.0:
        atom_plddts = atom_plddts * 100.0

    def safe_take(arr, idx):
        out = np.zeros(len(idx), dtype=float)
        m = (idx >= 0) & (idx < arr.size)
        out[m] = arr[idx[m]]
        return out

    cb_plddt = safe_take(atom_plddts, CB_atom_num)

    pae_matrix_af3 = np.array(data.get("pae", data.get("predicted_aligned_error", [])), dtype=float)
    if pae_matrix_af3.size == 0:
        return [], dict()


    pae_matrix = pae_matrix_af3[np.ix_(token_array, token_array)]

    iptm_d0chn_byres = init_chainpairdict_npzeros(unique_chains, numres)
    ipsae_d0chn_byres = init_chainpairdict_npzeros(unique_chains, numres)
    ipsae_d0dom_byres = init_chainpairdict_npzeros(unique_chains, numres)
    ipsae_d0res_byres = init_chainpairdict_npzeros(unique_chains, numres)

    iptm_d0chn_asym = init_chainpairdict_zeros(unique_chains)
    ipsae_d0chn_asym = init_chainpairdict_zeros(unique_chains)
    ipsae_d0dom_asym = init_chainpairdict_zeros(unique_chains)
    ipsae_d0res_asym = init_chainpairdict_zeros(unique_chains)

    iptm_d0chn_max = init_chainpairdict_zeros(unique_chains)
    ipsae_d0chn_max = init_chainpairdict_zeros(unique_chains)
    ipsae_d0dom_max = init_chainpairdict_zeros(unique_chains)
    ipsae_d0res_max = init_chainpairdict_zeros(unique_chains)

    iptm_d0chn_asymres = init_chainpairdict_zeros(unique_chains)
    ipsae_d0chn_asymres = init_chainpairdict_zeros(unique_chains)
    ipsae_d0dom_asymres = init_chainpairdict_zeros(unique_chains)
    ipsae_d0res_asymres = init_chainpairdict_zeros(unique_chains)

    iptm_d0chn_maxres = init_chainpairdict_zeros(unique_chains)
    ipsae_d0chn_maxres = init_chainpairdict_zeros(unique_chains)
    ipsae_d0dom_maxres = init_chainpairdict_zeros(unique_chains)
    ipsae_d0res_maxres = init_chainpairdict_zeros(unique_chains)

    n0chn = init_chainpairdict_zeros(unique_chains)
    n0dom = init_chainpairdict_zeros(unique_chains)
    n0dom_max = init_chainpairdict_zeros(unique_chains)
    n0res = init_chainpairdict_zeros(unique_chains)
    n0res_max = init_chainpairdict_zeros(unique_chains)
    n0res_byres = init_chainpairdict_npzeros(unique_chains, numres)

    d0chn = init_chainpairdict_zeros(unique_chains)
    d0dom = init_chainpairdict_zeros(unique_chains)
    d0dom_max = init_chainpairdict_zeros(unique_chains)
    d0res = init_chainpairdict_zeros(unique_chains)
    d0res_max = init_chainpairdict_zeros(unique_chains)
    d0res_byres = init_chainpairdict_npzeros(unique_chains, numres)

    valid_pair_counts = init_chainpairdict_zeros(unique_chains)
    dist_valid_pair_counts = init_chainpairdict_zeros(unique_chains)
    unique_residues_chain1 = init_chainpairdict_set(unique_chains)
    unique_residues_chain2 = init_chainpairdict_set(unique_chains)
    dist_unique_residues_chain1 = init_chainpairdict_set(unique_chains)
    dist_unique_residues_chain2 = init_chainpairdict_set(unique_chains)
    pDockQ_unique_residues = init_chainpairdict_set(unique_chains)

    pDockQ = init_chainpairdict_zeros(unique_chains)
    pDockQ2 = init_chainpairdict_zeros(unique_chains)
    LIS = init_chainpairdict_zeros(unique_chains)

    pDockQ_cutoff = 8.0
    for c1 in unique_chains:
        for c2 in unique_chains:
            if c1 == c2: continue
            npairs = 0
            for i in range(numres):
                if chains[i] != c1: continue
                valid_pairs = (chains == c2) & (distances[i] <= pDockQ_cutoff)
                npairs += int(np.sum(valid_pairs))
                if valid_pairs.any():
                    pDockQ_unique_residues[c1][c2].add(i)
                    for j in np.where(valid_pairs)[0]: pDockQ_unique_residues[c1][c2].add(int(j))

            if npairs > 0:
                mean_pl = float(cb_plddt[list(pDockQ_unique_residues[c1][c2])].mean())
                x = mean_pl * math.log10(npairs)
                pDockQ[c1][c2] = 0.724 / (1 + math.exp(-0.052 * (x - 152.611))) + 0.018
            else:
                pDockQ[c1][c2] = 0.0

    for c1 in unique_chains:
        for c2 in unique_chains:
            if c1 == c2: continue
            npairs = 0
            s = 0.0
            for i in range(numres):
                if chains[i] != c1: continue
                valid_pairs = (chains == c2) & (distances[i] <= pDockQ_cutoff)
                if valid_pairs.any():
                    npairs += int(np.sum(valid_pairs))
                    pae_list = pae_matrix[i][valid_pairs]
                    s += float(ptm_func_vec(pae_list, 10.0).sum())

            if npairs > 0:
                mean_pl = float(cb_plddt[list(pDockQ_unique_residues[c1][c2])].mean())
                mean_ptm = s / npairs
                x = mean_pl * mean_ptm
                pDockQ2[c1][c2] = 1.31 / (1 + math.exp(-0.075 * (x - 84.733))) + 0.005
            else:
                pDockQ2[c1][c2] = 0.0

    for c1 in unique_chains:
        for c2 in unique_chains:
            if c1 == c2: continue
            mask = (chains[:, None] == c1) & (chains[None, :] == c2)
            selected = pae_matrix[mask]
            if selected.size > 0:
                valid = selected[selected < 12]
                LIS[c1][c2] = float(((12 - valid) / 12).mean()) if valid.size > 0 else 0.0
            else:
                LIS[c1][c2] = 0.0

    for c1 in unique_chains:
        for c2 in unique_chains:
            if c1 == c2: continue
            n0chn[c1][c2] = int(np.sum(chains == c1) + np.sum(chains == c2))
            d0chn[c1][c2] = float(calc_d0(n0chn[c1][c2], chain_pair_type[c1][c2]))
            ptm_matrix_d0chn = ptm_func_vec(pae_matrix, d0chn[c1][c2])

            valid_pairs_iptm = (chains == c2)
            valid_pairs_matrix = np.outer(chains == c1, chains == c2) & (pae_matrix < pae_cutoff) # BUG FIX: outer product

            for i in range(numres):
                if chains[i] != c1: continue
                ipsae_mask = valid_pairs_matrix[i]
                iptm_d0chn_byres[c1][c2][i] = ptm_matrix_d0chn[i, valid_pairs_iptm].mean() if valid_pairs_iptm.any() else 0.0
                ipsae_d0chn_byres[c1][c2][i] = ptm_matrix_d0chn[i, ipsae_mask].mean() if ipsae_mask.any() else 0.0

                valid_pair_counts[c1][c2] += int(np.sum(ipsae_mask))
                if ipsae_mask.any():
                    iresnum = residues[i]["resnum"]
                    unique_residues_chain1[c1][c2].add(iresnum)
                    for j in np.where(ipsae_mask)[0]:
                        unique_residues_chain2[c1][c2].add(residues[int(j)]["resnum"])

                valid_pairs_dist = (chains == c2) & (pae_matrix[i] < pae_cutoff) & (distances[i] < internal_dist_cutoff)
                dist_valid_pair_counts[c1][c2] += int(np.sum(valid_pairs_dist))
                if valid_pairs_dist.any():
                    iresnum = residues[i]["resnum"]
                    dist_unique_residues_chain1[c1][c2].add(iresnum)
                    for j in np.where(valid_pairs_dist)[0]:
                        dist_unique_residues_chain2[c1][c2].add(residues[int(j)]["resnum"])

    for c1 in unique_chains:
        for c2 in unique_chains:
            if c1 == c2: continue
            residues_1 = len(unique_residues_chain1[c1][c2])
            residues_2 = len(unique_residues_chain2[c1][c2])
            n0dom[c1][c2] = residues_1 + residues_2
            d0dom[c1][c2] = float(calc_d0(n0dom[c1][c2], chain_pair_type[c1][c2]))
            ptm_matrix_d0dom = ptm_func_vec(pae_matrix, d0dom[c1][c2])

            valid_pairs_matrix = np.outer(chains == c1, chains == c2) & (pae_matrix < pae_cutoff)
            n0res_byres_all = np.sum(valid_pairs_matrix, axis=1)
            d0res_byres_all = calc_d0_array(n0res_byres_all, chain_pair_type[c1][c2])
            n0res_byres[c1][c2] = n0res_byres_all
            d0res_byres[c1][c2] = d0res_byres_all

            for i in range(numres):
                if chains[i] != c1: continue
                mask = valid_pairs_matrix[i]
                ipsae_d0dom_byres[c1][c2][i] = ptm_matrix_d0dom[i, mask].mean() if mask.any() else 0.0
                ptm_row_d0res = ptm_func_vec(pae_matrix[i], d0res_byres_all[i])
                ipsae_d0res_byres[c1][c2][i] = ptm_row_d0res[mask].mean() if mask.any() else 0.0

    for c1 in unique_chains:
        for c2 in unique_chains:
            if c1 == c2: continue
            idx = int(np.argmax(iptm_d0chn_byres[c1][c2]))
            iptm_d0chn_asym[c1][c2] = float(iptm_d0chn_byres[c1][c2][idx])
            iptm_d0chn_asymres[c1][c2] = residues[idx]["residue"]

            idx = int(np.argmax(ipsae_d0chn_byres[c1][c2]))
            ipsae_d0chn_asym[c1][c2] = float(ipsae_d0chn_byres[c1][c2][idx])
            ipsae_d0chn_asymres[c1][c2] = residues[idx]["residue"]

            idx = int(np.argmax(ipsae_d0dom_byres[c1][c2]))
            ipsae_d0dom_asym[c1][c2] = float(ipsae_d0dom_byres[c1][c2][idx])
            ipsae_d0dom_asymres[c1][c2] = residues[idx]["residue"]

            idx = int(np.argmax(ipsae_d0res_byres[c1][c2]))
            ipsae_d0res_asym[c1][c2] = float(ipsae_d0res_byres[c1][c2][idx])
            ipsae_d0res_asymres[c1][c2] = residues[idx]["residue"]
            n0res[c1][c2] = int(n0res_byres[c1][c2][idx])
            d0res[c1][c2] = float(d0res_byres[c1][c2][idx])

    for c1 in unique_chains:
        for c2 in unique_chains:
            if c1 <= c2: continue

            if iptm_d0chn_asym[c1][c2] >= iptm_d0chn_asym[c2][c1]:
                iptm_d0chn_max[c1][c2] = iptm_d0chn_max[c2][c1] = iptm_d0chn_asym[c1][c2]
                iptm_d0chn_maxres[c1][c2] = iptm_d0chn_maxres[c2][c1] = iptm_d0chn_asymres[c1][c2]
            else:
                iptm_d0chn_max[c1][c2] = iptm_d0chn_max[c2][c1] = iptm_d0chn_asym[c2][c1]
                iptm_d0chn_maxres[c1][c2] = iptm_d0chn_maxres[c2][c1] = iptm_d0chn_asymres[c2][c1]

            if ipsae_d0chn_asym[c1][c2] >= ipsae_d0chn_asym[c2][c1]:
                ipsae_d0chn_max[c1][c2] = ipsae_d0chn_max[c2][c1] = ipsae_d0chn_asym[c1][c2]
                ipsae_d0chn_maxres[c1][c2] = ipsae_d0chn_maxres[c2][c1] = ipsae_d0chn_asymres[c1][c2]
            else:
                ipsae_d0chn_max[c1][c2] = ipsae_d0chn_max[c2][c1] = ipsae_d0chn_asym[c2][c1]
                ipsae_d0chn_maxres[c1][c2] = ipsae_d0chn_maxres[c2][c1] = ipsae_d0chn_asymres[c2][c1]

            if ipsae_d0dom_asym[c1][c2] >= ipsae_d0dom_asym[c2][c1]:
                ipsae_d0dom_max[c1][c2] = ipsae_d0dom_max[c2][c1] = ipsae_d0dom_asym[c1][c2]
                n0dom_max[c1][c2] = n0dom_max[c2][c1] = int(n0dom[c1][c2])
                d0dom_max[c1][c2] = d0dom_max[c2][c1] = float(d0dom[c1][c2])
            else:
                ipsae_d0dom_max[c1][c2] = ipsae_d0dom_max[c2][c1] = ipsae_d0dom_asym[c2][c1]
                n0dom_max[c1][c2] = n0dom_max[c2][c1] = int(n0dom[c2][c1])
                d0dom_max[c1][c2] = d0dom_max[c2][c1] = float(d0dom[c2][c1])

            if ipsae_d0res_asym[c1][c2] >= ipsae_d0res_asym[c2][c1]:
                ipsae_d0res_max[c1][c2] = ipsae_d0res_max[c2][c1] = ipsae_d0res_asym[c1][c2]
                n0res_max[c1][c2] = n0res_max[c2][c1] = int(n0res[c1][c2])
                d0res_max[c1][c2] = d0res_max[c2][c1] = float(d0res[c1][c2])
            else:
                ipsae_d0res_max[c1][c2] = ipsae_d0res_max[c2][c1] = ipsae_d0res_asym[c2][c1]
                n0res_max[c1][c2] = n0res_max[c2][c1] = int(n0res[c2][c1])
                d0res_max[c1][c2] = d0res_max[c2][c1] = float(d0res[c2][c1])

    pair_rows = []
    for c1 in unique_chains:
        for c2 in unique_chains:
            if c1 <= c2: continue

            residues_1 = max(len(unique_residues_chain2[c1][c2]), len(unique_residues_chain1[c2][c1]))
            residues_2 = max(len(unique_residues_chain1[c1][c2]), len(unique_residues_chain2[c2][c1]))
            dist_residues_1 = max(len(dist_unique_residues_chain2[c1][c2]), len(dist_unique_residues_chain1[c2][c1]))
            dist_residues_2 = max(len(dist_unique_residues_chain1[c1][c2]), len(dist_unique_residues_chain2[c2][c1]))

            LIS_score = (float(LIS[c1][c2]) + float(LIS[c2][c1])) / 2.0
            pDockQ2_value = max(float(pDockQ2[c1][c2]), float(pDockQ2[c2][c1]))
            pDockQ_value = max(float(pDockQ[c1][c2]), float(pDockQ[c2][c1]))

            pair_rows.append({
                "chain1": str(c2),
                "chain2": str(c1),
                "pae_cutoff": float(pae_cutoff),
                "ipsae": float(ipsae_d0res_max[c1][c2]),
                "ipsae_d0chn": float(ipsae_d0chn_max[c1][c2]),
                "ipsae_d0dom": float(ipsae_d0dom_max[c1][c2]),
                "iptm_d0chn": float(iptm_d0chn_max[c1][c2]),
                "pdockq": float(pDockQ_value),
                "pdockq2": float(pDockQ2_value),
                "lis": float(LIS_score),
                "n0res": int(n0res_max[c1][c2]),
                "n0chn": int(n0chn[c1][c2]),
                "n0dom": int(n0dom_max[c1][c2]),
                "d0res": float(d0res_max[c1][c2]),
                "d0chn": float(d0chn[c1][c2]),
                "d0dom": float(d0dom_max[c1][c2]),
                "nres1": int(residues_1),
                "nres2": int(residues_2),
                "dist1": int(dist_residues_1),
                "dist2": int(dist_residues_2),
            })

    best_pair = None
    best_val = None
    best_row = None
    for r in pair_rows:
        v = r["ipsae"]
        if best_val is None or v > best_val:
            best_val = v
            best_pair = f"{r['chain1']}-{r['chain2']}"
            best_row = r

    summary = {}
    if best_row:
        summary = dict(
            ipsae=round(float(best_row["ipsae"]), 6),
            ipsae_d0chn=round(float(best_row["ipsae_d0chn"]), 6),
            ipsae_d0dom=round(float(best_row["ipsae_d0dom"]), 6),
            iptm_d0chn=round(float(best_row["iptm_d0chn"]), 6),
            pdockq=round(float(best_row["pdockq"]), 4),
            pdockq2=round(float(best_row["pdockq2"]), 4),
            lis=round(float(best_row["lis"]), 4),
            best_pair=best_pair,
            pae_cutoff=float(pae_cutoff),
        )

    return pair_rows, summary

def compute_ipsae_scores_multi(
        pae_json_path: Path | str,
        cif_path: Path | str,
        pae_cutoffs: Iterable[float] = (3.0, 5.0, 10.0, 15.0, 20.0),
) -> tuple[list[dict], list[dict]]:

    pair_rows_all: list[dict] = []
    summary_rows: list[dict] = []

    for pae_cutoff in pae_cutoffs:
        pair_rows, summary = compute_ipsae_scores(
            pae_json_path,
            cif_path,
            pae_cutoff=float(pae_cutoff),
        )

        if summary is not None:
            s = dict(summary)
            s["pae_cutoff"] = float(pae_cutoff)
            summary_rows.append(s)

        pair_rows_all.extend(pair_rows)

    return pair_rows_all, summary_rows