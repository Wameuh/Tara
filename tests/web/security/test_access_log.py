from __future__ import annotations

from tara_web.api.middleware.access_log import normalized_route


def test_access_route_hides_opaque_identifier_and_query() -> None:
    class Request:
        scope = {}

        class url:
            path = "/api/v1/jobs/job_abcdefghijklmnop?secret=nope"

    assert normalized_route(Request()) == "/api/{unmatched}"
