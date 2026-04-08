from __future__ import annotations
import re
import requests
from typing import List, Dict, Any, Optional, Tuple

from Bio.SeqUtils.CheckSum import crc64

# Constants
_UNIPARC_CHECKSUM_URL = (
    "https://rest.uniprot.org/uniparc/search"
    "?query=checksum:{crc}&format=json&size=1"
)

UNIPROT_URL = "https://rest.uniprot.org/uniprotkb/{acc}"

_UNIPROT_CACHE: Dict[str, Optional[Dict[str, Any]]] = {}

_HTTP_HEADERS = {
    "accept": "application/json",
    "User-Agent": "AF3-DB-Importer/1.0 (Structural Database)"
}


def query_uniparc(seq: str) -> Tuple[Optional[str], List[str]]:
    """
    Queries the UniParc API using a CRC64 checksum of the provided amino acid sequence.

    Returns:
        A tuple containing the UniParc ID (str or None) and a list of associated
        UniProtKB accessions (excluding versioned accessions containing a dot).
    """
    try:
        raw = crc64(seq)
        crc_hex = re.sub(r"[^0-9A-F]", "", raw)[-16:]

        url = _UNIPARC_CHECKSUM_URL.format(crc=crc_hex)
        r = requests.get(url, headers=_HTTP_HEADERS, timeout=15)
        r.raise_for_status()

        hits = r.json().get("results", [])
        if not hits:
            return None, []

        first_hit = hits[0]
        uni_parc_id = first_hit.get("uniParcId")

        raw_accessions = first_hit.get("uniProtKBAccessions", [])
        clean_accessions = [ac for ac in raw_accessions if "." not in ac]

        return uni_parc_id, clean_accessions

    except requests.Timeout as e:
        print(f"[UNIPARC] Timeout (seq_len={len(seq)}): {e}", flush=True)
    except requests.RequestException as e:
        print(f"[UNIPARC] HTTP Error: {e}", flush=True)
    except ValueError as e:
        print(f"[UNIPARC] JSON Decode Error: {e}", flush=True)
    except Exception as e:
        print(f"[UNIPARC] Unexpected Error: {e}", flush=True)

    return None, []


def query_uniprot_details(
        accession: str,
        fields: Optional[List[str]] = None,
        timeout: int = 10,
) -> Optional[Dict[str, Any]]:
    """
    Fetches selected metadata fields for a specific UniProtKB accession.
    Uses an internal cache to prevent redundant API calls for the same accession.

    Returns:
        A dictionary containing parsed metadata (status, protein name, gene name,
        function, organism, taxonomy) or None if the accession does not exist.
    """
    if accession in _UNIPROT_CACHE:
        return _UNIPROT_CACHE[accession]

    if fields is None:
        fields = [
            "accession",
            "protein_name",
            "gene_primary",
            "gene_names",
            "organism_name",
            "cc_function",
        ]

    url = UNIPROT_URL.format(acc=accession)
    params = {"fields": ",".join(fields)}

    print(f"[UNIPROT] Fetching details for accession: {accession}", flush=True)

    try:
        r = requests.get(url, params=params, headers=_HTTP_HEADERS, timeout=timeout)

        if r.status_code == 404:
            print(f"[UNIPROT] Accession '{accession}' not found.", flush=True)
            _UNIPROT_CACHE[accession] = None
            return None

        r.raise_for_status()
        data = r.json() or {}

    except requests.RequestException:
        print(f"[UNIPROT] Accession '{accession}' is invalid or API request failed.", flush=True)
        _UNIPROT_CACHE[accession] = None
        return None

    # --- Organism & Taxonomy ---
    org: Dict[str, Any] = data.get("organism") or {}
    scientific = org.get("scientificName")
    common = org.get("commonName")
    lineage_data = org.get("lineage")
    lineage = str(lineage_data) if lineage_data else None

    organism = scientific or common
    taxonomy_parts = filter(None, [scientific, common, lineage])
    taxonomy = "; ".join(taxonomy_parts) or None

    # Status (reviewed/unreviewed)
    status: Optional[str] = data.get("entryType")

    # Protein Names
    pd: Dict[str, Any] = data.get("proteinDescription") or {}
    protein_name = (
        (pd.get("recommendedName") or {})
        .get("fullName", {})
        .get("value")
    )

    alternative_names: List[str] = []
    for alt in pd.get("alternativeNames") or []:
        full = (alt.get("fullName") or {}).get("value")
        if full:
            alternative_names.append(full)

    if not alternative_names:
        alternative_names = None

    # Gene Name
    gene_name = None
    for g in data.get("genes") or []:
        gn = (g.get("geneName") or {}).get("value")
        if gn:
            gene_name = gn
            break

    # Function Comment
    function = None
    for c in data.get("comments") or []:
        if c.get("commentType") == "FUNCTION":
            texts = [
                t.get("value")
                for t in c.get("texts") or []
                if t.get("value")
            ]
            if texts:
                function = " ".join(texts)
                break

    result = {
        "status": status,
        "protein_name": protein_name,
        "alternative_names": alternative_names,
        "gene_name": gene_name,
        "function": function,
        "organism": organism,
        "taxonomy": taxonomy,
    }

    _UNIPROT_CACHE[accession] = result
    return result