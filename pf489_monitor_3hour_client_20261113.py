#!/usr/bin/env python3
"""Read-only PF489 availability scanner.

No LINE, no booking/cancellation, no writes. Never claim zero vacancies
when any date/court/time result is incomplete or ambiguous.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

RELAY = "https://pf489-vercel-connectivity-test.vercel.app/api/pf489-monitor-relay.mjs"
AUDIENCE = "pf489-tennis-monitor-oidc-v2"
FACILITY = "戸倉第二テニスコート"
TIMES = (("09:00", "11:00"), ("11:00", "13:00"),
         ("13:00", "15:00"), ("15:00", "17:00"))
COURTS = frozenset("ABCD")
MAX_CANDIDATES = 12
MAX_RESPONSE_SIZE = 250_000

class IncompleteScan(RuntimeError):
    pass

def request_json(req, timeout=60):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            if res.status != 200:
                raise IncompleteScan("upstream HTTP status not 200")
            raw = res.read(MAX_RESPONSE_SIZE + 1)
            if len(raw) > MAX_RESPONSE_SIZE:
                raise IncompleteScan("response exceeds safe size")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise IncompleteScan("response is not an object")
            return result
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        raise IncompleteScan("unable to retrieve verified response") from exc

def token():
    link = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    bearer = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not link or not bearer:
        raise IncompleteScan("GitHub OIDC authorization not available")
    separator = "&" if "?" in link else "?"
    url = link + separator + urllib.parse.urlencode({"audience": AUDIENCE})
    result = request_json(urllib.request.Request(
        url, headers={"Authorization": "bearer " + bearer}), timeout=10)
    value = result.get("value")
    if not isinstance(value, str) or not value:
        raise IncompleteScan("OIDC token missing")
    return value

def relay_call(jwt, city, mode, **params):
    query = urllib.parse.urlencode({"city": city, "mode": mode, **params})
    req = urllib.request.Request(RELAY + "?" + query, headers={
        "Authorization": "Bearer " + jwt, "Accept": "application/json"})
    value = request_json(req)
    if value.get("authenticated") is not True or value.get("city") != city:
        raise IncompleteScan("relay identity mismatch")
    result = value.get("output")
    if not isinstance(result, dict):
        raise IncompleteScan("relay result absent")
    return result

def valid_day(value):
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False

def read_kokubunji(jwt):
    info = relay_call(jwt, "kokubunji", "calendar")
    days, cells = info.get("days"), info.get("totalCells")
    candidates = info.get("candidates")
    if (info.get("stage") != "CALENDAR_VERIFIED" or
            not isinstance(days, int) or not 1 <= days <= 14 or
            cells != days * 4 or not isinstance(candidates, list)):
        raise IncompleteScan("invalid Kokubunji calendar")
    if len(candidates) > MAX_CANDIDATES:
        raise IncompleteScan("too many candidates for one complete scan")
    seen = set()
    all_slots = []
    for item in candidates:
        if not isinstance(item, dict):
            raise IncompleteScan("invalid candidate")
        court, day = item.get("court"), item.get("date")
        identity = (day, court)
        if (item.get("facility") != FACILITY or court not in COURTS or
                not valid_day(day) or
                item.get("status") not in ("available", "limited") or
                identity in seen):
            raise IncompleteScan("wrong or duplicate calendar cell")
        seen.add(identity)
        detail = relay_call(jwt, "kokubunji", "detail", date=day, court=court)
        slots = detail.get("slots")
        if (detail.get("stage") != "TIME_DETAIL_VERIFIED" or
                not isinstance(slots, list) or len(slots) != 4):
            raise IncompleteScan("missing hourly detail")
        for actual, (start, end) in zip(slots, TIMES):
            if (not isinstance(actual, dict) or
                    actual.get("facility") != FACILITY or
                    actual.get("court") != court or
                    actual.get("date") != day or
                    actual.get("start") != start or
                    actual.get("end") != end or
                    actual.get("status") not in (
                        "available", "unavailable", "limited",
                        "lottery", "not_applicable")):
                raise IncompleteScan("time or court inconsistency")
            all_slots.append(actual)
    if len(seen) != len(candidates) or len(all_slots) != len(candidates) * 4:
        raise IncompleteScan("not all candidate slots verified")
    return {"calendarDays": days, "cells": cells, "candidates": len(candidates),
            "hourlySlots": len(all_slots),
            "confirmedAvailable": sum(x["status"] == "available" for x in all_slots)}

def read_kunitachi(jwt):
    result = relay_call(jwt, "kunitachi", "summary")
    if (result.get("stage") != "SUMMARY_VERIFIED_ONLY" or
            not isinstance(result.get("courts"), int) or result["courts"] < 1 or
            result.get("complete") is not False):
        raise IncompleteScan("Kunitachi summary invalid")
    cells = result.get("cells")
    if not isinstance(cells, list) or len(cells) != result["courts"]:
        raise IncompleteScan("Kunitachi cell count mismatch")
    return {"courts": len(cells), "date": result.get("date"),
            "hourlyComplete": False}

def main():
    try:
        jwt = token()
        koku = read_kokubunji(jwt)
        kuni = read_kunitachi(jwt)
        print("KOKUBUNJI_READONLY " + json.dumps(koku, ensure_ascii=False), flush=True)
        print("KUNITACHI_READONLY " + json.dumps(kuni, ensure_ascii=False), flush=True)
        print("SCAN_INCOMPLETE: notification and time detail for Kunitachi disabled", flush=True)
        return 2
    except IncompleteScan as error:
        print("SCAN_INCOMPLETE: " + str(error), file=sys.stderr, flush=True)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
