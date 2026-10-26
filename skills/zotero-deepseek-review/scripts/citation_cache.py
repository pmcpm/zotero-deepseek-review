"""Invalidate only Zotero's rendered-citation cache in task-owned field snapshots.

This module does not open Word, access Zotero, or update any document. Callers must
compare each before_sha256 with the live field code before applying returned code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path


_PREFIX = re.compile(r"\A\s*ADDIN\s+ZOTERO_ITEM\s+CSL_CITATION\s*", re.IGNORECASE)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object keys are not supported")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Non-finite JSON numbers are not supported")


_DECODER = json.JSONDecoder(object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def _skip_whitespace(text: str, position: int) -> int:
    while position < len(text) and text[position] in " \t\r\n":
        position += 1
    return position


def _object_members(text: str, start: int) -> list[dict]:
    """Locate members in an already validated object without reserializing values."""
    members = []
    position = _skip_whitespace(text, start + 1)
    while text[position] != "}":
        key_start = position
        key, position = _DECODER.raw_decode(text, position)
        position = _skip_whitespace(text, position)
        value_start = _skip_whitespace(text, position + 1)  # Skip the validated colon.
        _, value_end = _DECODER.raw_decode(text, value_start)
        after = _skip_whitespace(text, value_end)
        comma = after if text[after] == "," else None
        members.append({"key": key, "start": key_start, "value_start": value_start,
                        "end": value_end, "comma": comma})
        if comma is None:
            break
        position = _skip_whitespace(text, comma + 1)
    return members


def invalidate_render_cache(code: str) -> str:
    """Remove properties.formattedCitation, preserving all other JSON semantics.

    The original prefix and trailing whitespace survive byte-for-byte as text.
    An already uncached field is returned unchanged, including JSON whitespace.
    dontUpdate marks a user-controlled citation and is never overridden.
    """
    if not isinstance(code, str):
        raise ValueError("Citation field code must be a string")
    match = _PREFIX.match(code)
    if not match:
        raise ValueError("Expected an ADDIN ZOTERO_ITEM CSL_CITATION field")
    try:
        citation, end = _DECODER.raw_decode(code, match.end())
    except (json.JSONDecodeError, ValueError, RecursionError) as error:
        raise ValueError("Citation field contains invalid JSON") from error
    if not isinstance(citation, dict):
        raise ValueError("Citation JSON must be an object")
    if code[end:].strip():
        raise ValueError("Citation JSON must be followed only by whitespace")
    properties = citation.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("Citation properties must be an object")
    if properties.get("dontUpdate") is True:
        raise ValueError("Citation dontUpdate=true forbids render-cache invalidation")
    if "formattedCitation" not in properties:
        return code
    # Remove one member lexically. Re-encoding the whole JSON could round an
    # unrelated high-precision number or normalize custom escaping/whitespace.
    top = _object_members(code, match.end())
    property_start = next(member["value_start"] for member in top if member["key"] == "properties")
    members = _object_members(code, property_start)
    index = next(i for i, member in enumerate(members) if member["key"] == "formattedCitation")
    member = members[index]
    if member["comma"] is not None:
        first, last = member["start"], member["comma"] + 1
    elif index:
        first, last = members[index - 1]["comma"], member["end"]
    else:
        first, last = member["start"], member["end"]
    return code[:first] + code[last:]


def invalidate_batch(entries: list) -> list[dict]:
    """Validate every snapshot before returning any transformed batch result."""
    if not isinstance(entries, list):
        raise ValueError("Input must be an array of field snapshots")
    seen = set()
    results = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each field snapshot must be an object")
        index = entry.get("index")
        if type(index) is not int or index <= 0:
            raise ValueError("Field index must be a positive integer, not a boolean")
        if index in seen:
            raise ValueError(f"Duplicate field index: {index}")
        seen.add(index)
        code = entry.get("code")
        try:
            updated = invalidate_render_cache(code)
        except ValueError as error:
            raise ValueError(f"Field {index}: {error}") from error
        results.append({"index": index,
                        "before_sha256": hashlib.sha256(code.encode("utf8")).hexdigest(),
                        "code": updated, "changed": updated != code})
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    temporary = None
    try:
        # Parse and validate the complete input before creating or replacing output.
        entries = json.loads(args.input.read_text(encoding="utf-8-sig"),
                             object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        results = invalidate_batch(entries)
        with tempfile.NamedTemporaryFile("w", encoding="utf8", newline="\n", delete=False,
                                         dir=args.output.parent, prefix=".citation-cache-", suffix=".tmp") as handle:
            temporary = Path(handle.name)
            json.dump(results, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, args.output)
        temporary = None
    except (OSError, ValueError, RecursionError) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 2
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(json.dumps({"fields": len(results), "changed": sum(row["changed"] for row in results)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
