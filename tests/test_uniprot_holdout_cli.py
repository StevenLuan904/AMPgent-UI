from __future__ import annotations

import pytest

from pepagent.uniprot_holdout_cli import NEXT_LINK, fetch_paginated_fasta


def test_next_link_parser_accepts_uniprot_relation_header() -> None:
    link = '<https://rest.uniprot.org/uniprotkb/search?cursor=abc>; rel="next"'
    assert NEXT_LINK.search(link).group(1).endswith("cursor=abc")


def test_uniprot_page_size_fails_closed() -> None:
    with pytest.raises(ValueError, match="page size"):
        fetch_paginated_fasta(query="reviewed:true", page_size=501)
