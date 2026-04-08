import asyncio
from typing import Tuple

import httpx
import requests
import pandas as pd


async def run_foldseek(
        file_path: str,
        keep_all: bool,
        prob_threshold: float,
        database: str = "pdb100",
        mode: str = "complex-3diaa"
) -> Tuple[str, pd.DataFrame]:
    url = "https://search.foldseek.com/api/ticket"

    # Request Ticket
    try:
        data_payload = [
            ("mode", mode),
            ("database[]", database)
        ]

        def _do_upload():
            filename = "structure.cif" if file_path.endswith(".cif") else "structure.pdb"
            with open(file_path, "rb") as fh:
                return requests.post(url, data=data_payload, files={"q": (filename, fh)})

        resp = await asyncio.to_thread(_do_upload)

        if resp.status_code != 200:
            err_text = resp.text[:200]
            print(f"[FOLDSEEK] API Error {resp.status_code}: {err_text}", flush=True)
            return f"ERROR: API {resp.status_code} - {err_text}", pd.DataFrame()

        try:
            ticket_resp = resp.json()
        except Exception:
            return "ERROR: Invalid JSON from API", pd.DataFrame()

    except Exception as e:
        print(f"[FOLDSEEK] Request exception: {e}", flush=True)
        return f"ERROR: Connection failed ({str(e)})", pd.DataFrame()

    if "id" not in ticket_resp:
        return "ERROR: No Ticket ID in response", pd.DataFrame()

    ticket_id = ticket_resp["id"]

    # Polling for Completion
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                status_resp = await client.get(f"https://search.foldseek.com/api/ticket/{ticket_id}")
                status_data = status_resp.json()
                status = status_data.get("status")

                if status == "ERROR":
                    return "ERROR: Processing failed on Server", pd.DataFrame()
                if status == "COMPLETE":
                    break

                await asyncio.sleep(1)
            except Exception:
                return "ERROR: Polling failed", pd.DataFrame()

        # Fetch Result
        try:
            result_resp = await client.get(f"https://search.foldseek.com/api/result/{ticket_id}/0")
            result_data = result_resp.json()
        except Exception:
            return "ERROR: Result download failed", pd.DataFrame()

    if not result_data.get("results") or not result_data["results"][0].get("alignments"):
        return ticket_id, pd.DataFrame(columns=["Hit", "prob", "seqId", "E-value", "score", "RCSB PDB"])

    # Process Data
    try:
        aligns = result_data["results"][0]["alignments"][0]
        df = pd.DataFrame(aligns)

        df["prob"] = pd.to_numeric(df["prob"], errors="coerce")
        df["eval_num"] = pd.to_numeric(df["eval"], errors="coerce")

        df = df[df["prob"] > float(prob_threshold)].copy()

        if df.empty:
            return ticket_id, pd.DataFrame(columns=["Hit", "prob", "seqId", "E-value", "score", "RCSB PDB"])

        parts = df["target"].str.split(" ", n=1, expand=True)
        if len(parts.columns) > 1:
            df["name"] = parts[1]
            df["id"] = parts[0].str.split("-", n=1, expand=True)[0]
        else:
            df["name"] = df["target"]
            df["id"] = ""

        df["RCSB PDB"] = "https://www.rcsb.org/structure/" + df["id"]
        df = df.drop_duplicates(subset="target", keep="first" if keep_all else "first")

        lower_map = {c.lower(): c for c in df.columns}
        renames = {"name": "Hit", "prob": "prob", "seqId": "seqId", "score": "score"}

        if "complexqtm" in lower_map:
            renames[lower_map["complexqtm"]] = "complexQTM"
        if "complexttm" in lower_map:
            renames[lower_map["complexttm"]] = "complexTTM"

        df = df.rename(columns=renames)

        out_cols = ["Hit", "prob", "seqId", "E-value", "score", "RCSB PDB"]
        if "complexQTM" in df.columns:
            out_cols.insert(5, "complexQTM")
        if "complexTTM" in df.columns:
            out_cols.insert(6, "complexTTM")

        for c in out_cols:
            if c not in df.columns:
                df[c] = None

        df["E-value"] = df["eval_num"].map(lambda x: f"{x:.1e}" if pd.notna(x) else "")
        df["prob"] = df["prob"].map(lambda x: round(x, 2) if pd.notna(x) else x)

        return ticket_id, df[out_cols]

    except Exception as e:
        print(f"[FOLDSEEK] Parsing error: {e}", flush=True)
        return "ERROR: Parsing result failed", pd.DataFrame()
