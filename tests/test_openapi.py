import json

from app.export_openapi import OUT, build_document


def test_committed_openapi_matches_the_code() -> None:
    """Run `python -m app.export_openapi` after changing routes or models."""
    assert json.loads(OUT.read_text()) == json.loads(json.dumps(build_document()))
