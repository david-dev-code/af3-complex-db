from __future__ import annotations
from pathlib import Path
from typing import Any, Dict, List
import json
import re

class DataExtractor:
    """
    Reads *_data.json* containing meta information, chain data, and MSA.
    Includes get_sequence(idx) to retrieve the raw chain sequence.
    """

    def __init__(self, json_path: Path | str):
        """Initializes the DataExtractor with the given JSON file path."""
        self._json_path = Path(json_path).expanduser()
        self._data: Dict[str, Any] = json.loads(self._json_path.read_text())

        # Meta information
        self._name = self._data.get("name", self._json_path.stem)
        self._dialect = self._data.get("dialect")
        self._version = self._data.get("version")
        self._seeds = self._data.get("modelSeeds") or self._data.get("modelSeed")

        # Collect protein/RNA entries
        self._seq_objs: List[Dict[str, Any]] = [
            v for entry in self._data["sequences"]
            for (k, v) in entry.items() if k in {"protein", "rna"}
        ]
        if not self._seq_objs:
            raise KeyError("No protein/rna entries in data.json")

        self._ids = [o.get("id", f"chain{i}") for i, o in enumerate(self._seq_objs)]
        self._lens = [len(o["sequence"]) for o in self._seq_objs]

        # Cache for cleaned MSAs
        self._msa_cache: Dict[tuple[int, str], List[str]] = {}

    # Meta Getters

    def get_name(self):
        """Returns the name of the dataset."""
        return self._name

    def get_dialect(self):
        """Returns the dialect of the dataset."""
        return self._dialect

    def get_version(self):
        """Returns the version of the dataset."""
        return self._version

    def get_model_seeds(self):
        """Returns the model seeds as a list."""
        return [self._seeds] if isinstance(self._seeds, int) else self._seeds

    # Chain Information

    def get_chain_ids(self) -> List[str]:
        """Returns a list of chain IDs."""
        return self._ids

    def get_chain_lengths(self) -> List[int]:
        """Returns a list of chain lengths."""
        return self._lens

    # Raw Sequence

    def get_sequence(self, idx: int = 0) -> str:
        """Returns the original amino acid or nucleotide sequence for the chain at the given index."""
        if idx >= len(self._seq_objs):
            raise IndexError(f"Chain index {idx} out of range.")
        return self._seq_objs[idx]["sequence"]

    # MSA

    def get_msa(self, idx: int = 0, msa_type: str = "auto") -> List[str]:
        """Retrieves the Multiple Sequence Alignment (MSA) for a specific chain and type."""
        if msa_type not in {"auto", "unpaired", "paired"}:
            raise ValueError("msa_type must be 'auto'|'unpaired'|'paired'")
        key_cache = (idx, msa_type)
        if key_cache in self._msa_cache:
            return self._msa_cache[key_cache]

        seq_obj = self._seq_objs[idx]
        search = ("unpairedMsa",) if msa_type == "unpaired" else \
            ("pairedMsa",) if msa_type == "paired" else \
                ("unpairedMsa", "pairedMsa")

        for key in search:
            txt = seq_obj.get(key) or (
                Path(seq_obj.get(f"{key}Path", "")).expanduser().read_text()
                if seq_obj.get(f"{key}Path") else None
            )
            if txt:
                msa = self._msa_cache[key_cache] = self._clean_a3m(txt)
                return msa

        raise ValueError(f"{msa_type} MSA not found for chain {idx}")

    def get_copy_hints(self) -> list[int]:
        """Returns per-sequence copy counts based on JSON hints, defaulting to 1."""
        hints = []
        for o in self._seq_objs:
            c = o.get("copies") or o.get("numCopies")
            if c is None:
                s = o.get("stoichiometry")
                if isinstance(s, int):
                    c = s
                elif isinstance(s, str):
                    m = re.search(r"\d+", s)
                    c = int(m.group()) if m else 1
            try:
                c = int(c) if c is not None else 1
            except Exception:
                c = 1
            hints.append(max(1, c))
        return hints

    @staticmethod
    def _clean_a3m(text: str) -> List[str]:
        """Parses and cleans A3M format text by removing lowercase insertion characters."""
        seqs, buf = [], []
        for line in text.splitlines():
            if line.startswith(">"):
                if buf:
                    seqs.append("".join(buf));
                    buf.clear()
                continue
            buf.append(line.strip())
        if buf:
            seqs.append("".join(buf))
        return ["".join(c for c in s if not c.islower()) for s in seqs]