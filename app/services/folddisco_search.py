import asyncio
from typing import List, Tuple

import httpx
import requests


async def run_folddisco(file_path: str, motif: str, databases: List[str]) -> Tuple[str, str]:
    """
    Submits the Folddisco job, polls the server until processing is COMPLETE,
    and then returns the direct web URL to view the results.
    """
    url = "https://search.foldseek.com/api/ticket/folddisco"

    if not databases:
        databases = ["pdb_folddisco"]

    print(f"[FOLDDISCO] Uploading motif {motif} to {databases}...", flush=True)


    try:
        data_payload = [("motif", motif)]
        for db in databases:
            data_payload.append(("database[]", db))

        def _do_upload():
            with open(file_path, "rb") as fh:
                return requests.post(url, data=data_payload, files={"q": fh})

        resp = await asyncio.to_thread(_do_upload)

        if resp.status_code != 200:
            print(f"[FOLDDISCO] API Upload Error {resp.status_code}: {resp.text}", flush=True)
            return f"ERROR: API Upload {resp.status_code}", ""

        ticket_id = resp.json().get("id")

        if not ticket_id:
            return "ERROR: No Ticket ID received", ""

    except Exception as e:
        print(f"[FOLDDISCO] Upload Exception: {e}", flush=True)
        return "ERROR: Connection failed", ""

    print(f"[FOLDDISCO] Ticket ID: {ticket_id}. Polling until COMPLETE...", flush=True)


    status_url = f"https://search.foldseek.com/api/ticket/{ticket_id}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                status_resp = await client.get(status_url)
                status_data = status_resp.json()
                status = status_data.get("status")

                if status == "ERROR":
                    print("[FOLDDISCO] Server returned ERROR status.", flush=True)
                    return "ERROR: Server processing failed", ""

                if status == "COMPLETE":
                    print("[FOLDDISCO] Processing COMPLETE on server!", flush=True)
                    break


                await asyncio.sleep(2)

            except Exception as e:
                print(f"[FOLDDISCO] Polling Exception: {e}", flush=True)
                return "ERROR: Polling failed", ""


    result_url = f"https://search.foldseek.com/result/folddisco/{ticket_id}"

    print(f"[FOLDDISCO] Returning Redirect URL: {result_url}", flush=True)
    return ticket_id, result_url