from __future__ import annotations
from pathlib import Path
from typing import Optional
import pandas as pd


class RankingExtractor:
    """
    Reads ranking_scores.csv.
    Provides access to ranking scores per seed and sample.
    """

    def __init__(self, output_folder: Path | str):
        """
        Initialize the extractor with the output folder path.
        """
        self.path = Path(output_folder).expanduser().resolve()
        self.csv_path = self.path / "ranking_scores.csv"
        self._df: Optional[pd.DataFrame] = None

        if self.csv_path.exists():
            try:
                self._df = pd.read_csv(self.csv_path)
            except Exception as e:
                print(f"⚠️ Warning: Failed to read {self.csv_path}: {e}")

    def get_dataframe(self) -> Optional[pd.DataFrame]:
        """
        Return the loaded dataframe.
        """
        return self._df

    def get_score(self, seed: int, sample: int) -> Optional[float]:
        """
        Retrieve the ranking score for a specific seed and sample.
        """
        if self._df is None:
            return None
        row = self._df[(self._df["seed"] == seed) & (self._df["sample"] == sample)]
        if row.empty:
            return None
        return float(row["ranking_score"].iloc[0])

    def get_seed_sample_map(self) -> dict[int, set[int]]:
        """
        Return a dictionary mapping seeds to sets of samples from the dataframe.
        """
        if self._df is None:
            return {}

        seed_sample = self._df[["seed", "sample"]].dropna()
        result = {}
        for row in seed_sample.itertuples(index=False):
            result.setdefault(int(row.seed), set()).add(int(row.sample))
        return result
