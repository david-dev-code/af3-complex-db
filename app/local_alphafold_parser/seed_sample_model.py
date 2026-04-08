from __future__ import annotations
from pathlib import Path
from app.local_alphafold_parser.confidence_extractor import ConfidenceExtractor
from app.local_alphafold_parser.summary_extractor import SummaryExtractor


class SeedSampleModel:
    """
    Access to subfolder *seed-{i}_sample-{j}*.
    Contains individual Confidence and Summary Extractors.
    """

    def __init__(self, parent: Path, seed: int, sample: int):
        """Initializes the model and verifies the existence of required directories and files."""
        self.seed = seed
        self.sample = sample
        sub = parent / f"seed-{seed}_sample-{sample}"

        if not sub.is_dir():
            raise FileNotFoundError(f"{sub} not found")

        conf_json = sub / "confidences.json"
        summ_json = sub / "summary_confidences.json"

        if not conf_json.exists() or not summ_json.exists():
            raise FileNotFoundError("confidences.json or summary_confidences.json missing")

        self._conf = ConfidenceExtractor(conf_json)
        self._summary = SummaryExtractor(summ_json)

    # Confidence

    def get_contact_matrix(self):
        """Retrieves the contact matrix from the confidence extractor."""
        return self._conf.get_contact_matrix()

    def get_pae_matrix(self):
        """Retrieves the PAE matrix from the confidence extractor."""
        return self._conf.get_pae_matrix()

    def get_contact_sides(self):
        """Retrieves the contact sides from the confidence extractor."""
        return self._conf.get_contact_sides()

    def get_plddt_vector(self):
        """Retrieves the pLDDT vector from the confidence extractor."""
        return self._conf.get_plddt_vector()

    def get_atom_chain_ids(self):
        """Retrieves the atom chain IDs from the confidence extractor."""
        return self._conf.get_atom_chain_ids()

    def get_chain_plddt(self, cid):
        """Retrieves the pLDDT scores for a specific chain ID."""
        return self._conf.get_chain_plddt(cid)

    # Summary

    def get_iptm(self):
        """Retrieves the iPTM score from the summary extractor."""
        return self._summary.get_iptm()

    def get_ptm(self):
        """Retrieves the PTM score from the summary extractor."""
        return self._summary.get_ptm()

    def get_ranking_score(self):
        """Retrieves the ranking score from the summary extractor."""
        return self._summary.get_ranking_score()
