from __future__ import annotations
from pathlib import Path
from typing import List, Tuple, Dict, Union, Optional
import numpy as np

from app.local_alphafold_parser.data_extractor import DataExtractor
from app.local_alphafold_parser.confidence_extractor import ConfidenceExtractor
from app.local_alphafold_parser.summary_extractor import SummaryExtractor
from app.local_alphafold_parser.ranking_extractor import RankingExtractor
from app.local_alphafold_parser.seed_sample_model import SeedSampleModel

Idx = Union[int, str]


def _find_jsons(folder: Path) -> Tuple[Path, Path, Path]:
    """Finds and returns the paths to data, confidence, and summary JSON files."""
    folder = folder.expanduser().resolve()
    data = next(folder.glob("*_data.json"), None)
    conf = next((f for f in folder.glob("*_confidences.json")
                 if not f.name.endswith("_summary_confidences.json")), None)
    summ = next(folder.glob("*_summary_confidences.json"), None)

    if not (data and conf and summ):
        print(folder)
        folder = Path(folder).expanduser().resolve()

        for p in folder.rglob("*"):
            if p.is_dir():
                print("  [DIR]  %s", p.relative_to(folder))
            else:
                print("  [FILE] %s", p.relative_to(folder))
        raise FileNotFoundError("AF3 JSON files missing")
    return data, conf, summ


class AlphaFoldParser:
    """High-level API for AlphaFold3 outputs including seeds, samples, and ranking."""

    def __init__(self, folder: Path | str):
        """Initializes the parser by loading JSON data and building the chain index."""
        self._folder = Path(folder).expanduser().resolve()
        d_json, c_json, s_json = _find_jsons(self._folder)

        self._data = DataExtractor(d_json)
        self._conf = ConfidenceExtractor(c_json)
        self._summary = SummaryExtractor(s_json)
        self._ranking = RankingExtractor(self._folder)
        self._build_chain_index()
        self._cuts = np.cumsum([0, *self._expanded_lengths])
        self._submodels: Dict[tuple[int, int], SeedSampleModel] = {}

    # Meta
    def get_name(self):
        """Returns the name of the model."""
        return self._data.get_name()

    def get_dialect(self):
        """Returns the dialect of the data."""
        return self._data.get_dialect()

    def get_version(self):
        """Returns the version of the data."""
        return self._data.get_version()

    def get_model_seeds(self):
        """Returns the model seeds."""
        return self._data.get_model_seeds()

    # Summary
    def get_ptm(self) -> float | None:
        """Returns the global pTM score."""
        return self._summary.get_ptm()

    def get_fraction_disordered(self) -> float | None:
        """Returns the fraction of disordered residues."""
        return self._summary.fraction_disordered()

    def get_has_clash(self) -> float | None:
        """Returns the clash indicator."""
        return self._summary.has_clash()

    # Ranking / Seeds
    def get_seed_sample(self, seed: int, sample: int) -> SeedSampleModel:
        """Returns a SeedSampleModel for the given seed and sample."""
        key = (seed, sample)
        if key not in self._submodels:
            self._submodels[key] = SeedSampleModel(self._folder, seed, sample)
        return self._submodels[key]

    # Chains & MSA
    def get_chain_ids(self) -> List[str]:
        """Returns expanded chain IDs matching the actual chain count."""
        return self._chain_ids

    def get_sequence(self, idx: int = 0) -> str:
        """Returns the sequence of the expanded chain index."""
        base_idx = self._chain_index[idx]
        return self._data.get_sequence(base_idx)

    def get_msa(self, idx: int = 0, msa_type: str = "auto"):
        """Returns the MSA for the expanded chain index."""
        base_idx = self._chain_index[idx]
        return self._data.get_msa(base_idx, msa_type)

    # Helpers for Matrices/Blocks
    def _to_index(self, idx: Idx) -> int:
        """Maps a letter or integer to the expanded chain index."""
        if isinstance(idx, int):
            return idx
        return self._chain_ids.index(idx)

    def _blk(self, mat: np.ndarray, i: int, j: int):
        """Extracts a sub-block from a matrix for given chain indices."""
        a0, a1 = self._cuts[i], self._cuts[i + 1]
        b0, b1 = self._cuts[j], self._cuts[j + 1]
        return mat[a0:a1, b0:b1]

    # Matrices & Blocks
    def get_contact_matrix(self):
        """Returns the contact matrix."""
        return self._conf.get_contact_matrix()

    def get_pae_matrix(self):
        """Returns the PAE matrix."""
        return self._conf.get_pae_matrix()

    def get_plddt_vector(self):
        """Returns the pLDDT vector."""
        return self._conf.get_plddt_vector()

    def get_atom_chain_ids(self):
        """Returns the atom chain IDs."""
        return self._conf.get_atom_chain_ids()

    def get_contacts(self, i, j):
        """Returns the contact matrix block for chains i and j."""
        return self._blk(self.get_contact_matrix(), i, j)

    def get_pae(self, i, j):
        """Returns the PAE matrix block for chains i and j."""
        return self._blk(self.get_pae_matrix(), i, j)

    def get_chain_plddt(self, cid):
        """Returns the pLDDT vector for a specific chain."""
        return self._conf.get_chain_plddt(cid)

    # Summary – Chain & Chain-Pair
    def get_iptm(self):
        """Returns the global ipTM score."""
        return self._summary.get_iptm()

    def get_ranking_score(self):
        """Returns the ranking score."""
        return self._summary.get_ranking_score()

    def get_num_seeds_and_samples(self) -> tuple[int, int]:
        """Returns the total number of seeds and maximum samples per seed."""
        if not self._ranking:
            print("No ranking data available.")
            return 0, 0

        seed_sample_map = self._ranking.get_seed_sample_map()
        if not seed_sample_map:
            print("No seed sample map found.")
            return 0, 0

        num_seeds = len(seed_sample_map)
        samples_per_seed = max(len(s) for s in seed_sample_map.values())
        return num_seeds, samples_per_seed

    def get_mean_scores(self) -> dict[str, float | None]:
        """Calculates the mean of ipTM and pTM across all seeds and samples."""
        if not self._ranking:
            return {"mean_iptm": None, "mean_ptm": None}

        # Retrieve the seed to sample mapping.
        seed_map = self._ranking.get_seed_sample_map()
        if not seed_map:
            return {"mean_iptm": None, "mean_ptm": None}

        all_iptm = []
        all_ptm = []

        for seed, samples in seed_map.items():
            for sample in samples:
                try:
                    model = self.get_seed_sample(seed, sample)

                    val_iptm = model.get_iptm()
                    val_ptm = model.get_ptm()

                    if val_iptm is not None: all_iptm.append(val_iptm)
                    if val_ptm is not None:  all_ptm.append(val_ptm)
                except Exception as e:
                    print(f"Warning: Could not load metrics for seed {seed} sample {sample}: {e}")
                    continue

        # Calculate mean scores.
        res = {}
        if all_iptm:
            res["mean_iptm"] = float(np.mean(all_iptm))
        else:
            res["mean_iptm"] = None

        if all_ptm:
            res["mean_ptm"] = float(np.mean(all_ptm))
        else:
            res["mean_ptm"] = None

        return res

    def get_chain_iptm(self, idx: Idx) -> float:
        """Returns the ipTM score for a specific chain."""
        return float(self._summary.get_chain_iptm()[self._to_index(idx)])

    def get_chain_ptm(self, idx: Idx) -> float:
        """Returns the pTM score for a specific chain."""
        return float(self._summary.get_chain_ptm()[self._to_index(idx)])

    def get_chain_pair_iptm(self, chain_a: Idx, chain_b: Idx | None = None):
        """Returns the chain pair ipTM score or array."""
        arr = self._summary.get_chain_pair_iptm()
        ia = self._to_index(chain_a)
        if chain_b is None:
            return arr[ia, :].astype(float).tolist()
        ib = self._to_index(chain_b)
        return float(arr[ia, ib])

    def get_chain_pair_pae_min(self, chain_a: Idx, chain_b: Idx | None = None):
        """Returns the chain pair minimum PAE score or array."""
        arr = self._summary.get_chain_pair_pae_min()
        ia = self._to_index(chain_a)
        if chain_b is None:
            return arr[ia, :].astype(float).tolist()
        ib = self._to_index(chain_b)
        return float(arr[ia, ib])

    # Chain Expansion Logic
    def _build_chain_index(self) -> None:
        """Builds expanded chain index mapping base sequences to actual copies."""
        base_ids = self._data.get_chain_ids()
        base_lens = self._data.get_chain_lengths()
        n_base = len(base_ids)

        try:
            n_summary = len(self._summary.get_chain_ptm())
        except Exception:
            n_summary = n_base

        try:
            copies = self._data.get_copy_hints()
        except Exception:
            copies = [1] * n_base

        if len(copies) != n_base:
            copies = [1] * n_base

        if sum(copies) != n_summary:
            cif_counts = self._infer_copies_from_cif(n_base)
            if cif_counts and sum(cif_counts) == n_summary:
                copies = cif_counts

        if sum(copies) != n_summary:
            if n_base and n_summary % n_base == 0:
                factor = n_summary // n_base
                copies = [factor] * n_base
            else:
                delta = max(0, n_summary - sum(copies))
                copies[-1] += delta

        chain_index: list[int] = []
        for i, cnt in enumerate(copies):
            chain_index.extend([i] * max(1, int(cnt)))

        self._chain_index = chain_index
        self._chain_ids = [self._letter(i) for i in range(len(chain_index))]
        self._expanded_lengths = [base_lens[i] for i in chain_index]

    @staticmethod
    def _letter(i: int) -> str:
        """Converts an integer to a letter representation (A, B, ..., Z, AA, ...)."""
        letters = []
        n = i
        while True:
            n, r = divmod(n, 26)
            letters.append(chr(65 + r))
            if n == 0:
                break
            n -= 1
        return "".join(reversed(letters))

    def _infer_copies_from_cif(self, n_base: int) -> Optional[list[int]]:
        """Infers the number of chain copies from mmCIF entity_id counts."""
        cif = next(self._folder.glob("*_model_0.cif"), None) \
              or next(self._folder.glob("*_model.cif"), None)
        if not cif:
            return None

        try:
            lines = cif.read_text().splitlines()
        except Exception:
            return None

        i = 0
        while i < len(lines):
            if lines[i].strip().lower() != "loop_":
                i += 1
                continue

            # Collect tags
            j = i + 1
            tags = []
            while j < len(lines) and lines[j].strip().startswith("_"):
                tags.append(lines[j].strip())
                j += 1

            if "_struct_asym.id" in tags and "_struct_asym.entity_id" in tags:
                col_ent = tags.index("_struct_asym.entity_id")
                counts: Dict[str, int] = {}
                k = j
                while k < len(lines):
                    s = lines[k].strip()
                    if not s or s.startswith("_") or s.lower() == "loop_":
                        break

                    # Simple token split
                    row = s.split()
                    if len(row) > col_ent:
                        ent = row[col_ent]
                        counts[ent] = counts.get(ent, 0) + 1
                    k += 1

                # Map to 1..n_base
                out = [counts.get(str(n), 0) for n in range(1, n_base + 1)]
                # Ensure minimum 1
                out = [c if c > 0 else 1 for c in out]
                return out
            i = j

        return None