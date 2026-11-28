"""Finding a club's badge and colours online (library/club_lookup.py).

    POST /club-lookup                 {name, hint} -> candidates, colours, notes
    GET  /club-lookup/{id}/image      a candidate as RGBA PNG, cleaned up:
         ?raw=1                       as found (a thumbnail for the list)
         &remove_bg=1&tolerance=40&holes=0&trim=1&square=0
         The badge's main colours come back in the X-Colors header.
    POST /club-lookup/upload          multipart `file` -> {id}: an uploaded or
                                      pasted image, then cleaned up as above

Choosing one uploads the PNG through PUT /clubs/{id}/logo (or a team's
logo endpoint) as usual - after the browser has cropped it.
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from ..library import club_lookup, logo_tools

router = APIRouter(prefix="/club-lookup", tags=["clubs"])


class LookupIn(BaseModel):
    name: str
    hint: str = ""


@router.post("")
def lookup(body: LookupIn) -> dict:
    if not body.name.strip():
        raise HTTPException(400, "A name to search for")
    return club_lookup.lookup(body.name, body.hint)


@router.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    data = await file.read(club_lookup.MAX_IMAGE_BYTES + 1)
    if len(data) > club_lookup.MAX_IMAGE_BYTES:
        raise HTTPException(413, "That image is too large (8 MB at most)")
    try:
        return {"id": club_lookup.remember_upload(data)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{cid}/image")
def image(
    cid: str,
    raw: bool = False,
    remove_bg: bool = False,
    tolerance: int = Query(40, ge=0, le=200),
    holes: bool = False,
    trim: bool = False,
    square: bool = False,
) -> Response:
    try:
        data = club_lookup.image_bytes(cid)
        img = logo_tools.load(data)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    if raw:
        img.thumbnail((256, 256))
    else:
        img = logo_tools.process(img, remove_bg=remove_bg, tolerance=tolerance, holes=holes,
                                 trim=trim, square=square)
    return Response(
        logo_tools.png(img),
        media_type="image/png",
        headers={
            "X-Colors": ",".join(logo_tools.palette(img)),
            "X-Size": f"{img.width}x{img.height}",
            "Cache-Control": "private, max-age=600",
        },
    )
