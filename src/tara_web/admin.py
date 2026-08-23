# ruff: noqa: E501
"""Loopback-only operator dashboard with aggregate, privacy-safe statistics."""

from __future__ import annotations

import argparse
import hmac
import html
import os
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from tara_web.db.connection import ConnectionFactory
from tara_web.db.migrations import MIGRATIONS, schema_version
from tara_web.db.repositories.analytics import AnalyticsRepository
from tara_web.db.repositories.funding import FundingRepository

_MAX_FORM_BYTES = 4_096
_PAGE_LABELS = {
    "new_job": "Nouvelle analyse",
    "help": "Aide",
    "upload_session": "Chargement",
    "job": "Suivi / résultat",
}


def create_admin_app(
    database: ConnectionFactory, *, timezone: str = "Europe/Paris"
) -> FastAPI:
    zone = ZoneInfo(timezone)
    csrf_token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        connection = database.connect()
        try:
            if schema_version(connection) != MIGRATIONS[-1].version:
                raise RuntimeError("admin dashboard requires the latest database schema")
        finally:
            connection.close()
        yield

    app = FastAPI(
        title="Tara local administration",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )

    @app.middleware("http")
    async def local_security(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/health", response_class=PlainTextResponse)
    def health() -> str:
        connection = database.connect()
        try:
            connection.execute("SELECT 1").fetchone()
        finally:
            connection.close()
        return "ok"

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        return HTMLResponse(
            _dashboard_html(database, zone, csrf_token, request.query_params.get("saved"))
        )

    @app.post("/consumption-adjustments")
    async def adjust_consumption(request: Request) -> RedirectResponse:
        try:
            form = await _form(request)
            supplied = form.get("csrf", [""])
            if len(supplied) != 1 or not hmac.compare_digest(
                supplied[0], csrf_token
            ):
                raise ValueError("invalid csrf token")
            values = form.get("amount", [])
            directions = form.get("direction", [])
            notes = form.get("note", [])
            if len(values) != 1 or len(directions) != 1 or len(notes) != 1:
                raise ValueError("missing adjustment fields")
            micros = _euros_to_micros(values[0])
            if directions[0] == "subtract":
                micros = -micros
            elif directions[0] != "add":
                raise ValueError("invalid direction")
            AnalyticsRepository(database).add_adjustment(
                amount_micro_eur=micros,
                note=notes[0],
                created_at=datetime.now(UTC).isoformat(),
            )
        except (InvalidOperation, ValueError):
            return RedirectResponse("/?saved=invalid", status_code=303)
        return RedirectResponse("/?saved=1", status_code=303)

    return app


async def _form(request: Request) -> dict[str, list[str]]:
    length = request.headers.get("content-length")
    if length is None or not length.isdecimal() or int(length) > _MAX_FORM_BYTES:
        raise ValueError("invalid form length")
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != (
        "application/x-www-form-urlencoded"
    ):
        raise ValueError("invalid form content type")
    body = await request.body()
    if not body or len(body) > _MAX_FORM_BYTES:
        raise ValueError("invalid form")
    return parse_qs(body.decode("utf-8"), strict_parsing=True, max_num_fields=4)


def _euros_to_micros(value: str) -> int:
    if not 1 <= len(value) <= 24:
        raise ValueError("invalid amount")
    amount = Decimal(value)
    micros = amount * Decimal(1_000_000)
    if (
        not amount.is_finite()
        or amount <= 0
        or micros != micros.to_integral_value()
        or micros > 10**15
    ):
        raise ValueError("invalid amount")
    return int(micros)


def _dashboard_html(
    database: ConnectionFactory, zone: ZoneInfo, csrf_token: str, saved: str | None
) -> str:
    now = datetime.now(UTC)
    local_now = now.astimezone(zone)
    local_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    local_end = (
        local_start.replace(year=local_start.year + 1, month=1)
        if local_start.month == 12
        else local_start.replace(month=local_start.month + 1)
    )
    analytics = AnalyticsRepository(database)
    funding = FundingRepository(database).monthly_totals(
        start_utc=local_start.astimezone(UTC).isoformat(),
        end_utc=local_end.astimezone(UTC).isoformat(),
    )
    views = analytics.page_views(
        today=now.date().isoformat(),
        seven_days_ago=(now.date() - timedelta(days=6)).isoformat(),
    )
    statuses = analytics.job_status_counts()
    adjustments = analytics.recent_adjustments()
    kofi = analytics.recent_kofi_events()
    kofi_total, kofi_tests = analytics.kofi_event_counts()
    notice = (
        '<p class="notice ok">Ajustement enregistré.</p>'
        if saved == "1"
        else '<p class="notice error">Ajustement refusé. Vérifiez le montant et le motif.</p>'
        if saved == "invalid"
        else ""
    )
    view_rows = "".join(
        f"<tr><th>{html.escape(_PAGE_LABELS[row.page])}</th><td>{row.today}</td>"
        f"<td>{row.last_7_days}</td><td>{row.total}</td></tr>"
        for row in views
    )
    status_rows = "".join(
        f"<tr><th>{html.escape(status)}</th><td>{count}</td></tr>"
        for status, count in statuses.items()
    ) or '<tr><td colspan="2">Aucune analyse enregistrée.</td></tr>'
    adjustment_rows = "".join(
        f"<tr><td>{_date(row.created_at)}</td><td class=\"money\">{_money(row.amount_micro_eur, signed=True)}</td>"
        f"<td>{html.escape(row.note)}</td></tr>"
        for row in adjustments
    ) or '<tr><td colspan="3">Aucun ajustement manuel.</td></tr>'
    kofi_rows = "".join(
        f"<tr><td>{_date(row.received_at)}</td><td>{html.escape(row.event_type)}</td>"
        f"<td>{'Oui' if row.is_test_transaction else 'Non'}</td>"
        f"<td class=\"money\">{_money(row.amount_micros)} {html.escape(row.currency)}</td></tr>"
        for row in kofi
    ) or '<tr><td colspan="4">Aucun webhook Ko-fi reçu.</td></tr>'
    total_views = sum(row.total for row in views)
    total_jobs = sum(statuses.values())
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Administration Tara</title>
<style>{_STYLE}</style></head><body><main><header><p class="eyebrow">Accès local uniquement</p>
<h1>Administration Tara</h1><p>Statistiques agrégées, réceptions Ko-fi et correction du coût affiché.</p></header>{notice}
<section class="cards"><article><span>Vues enregistrées</span><strong>{total_views}</strong></article>
<article><span>Analyses créées</span><strong>{total_jobs}</strong></article>
<article><span>Webhooks Ko-fi</span><strong>{kofi_total}</strong><small>dont {kofi_tests} test(s)</small></article></section>
<section><h2>Pages affichées</h2><table><thead><tr><th>Vue</th><th>Aujourd'hui</th><th>7 jours</th><th>Total</th></tr></thead><tbody>{view_rows}</tbody></table>
<p class="muted">Comptage agrégé sans adresse IP, cookie ni identifiant visiteur.</p></section>
<section><h2>Analyses par état</h2><table><tbody>{status_rows}</tbody></table></section>
<section><h2>Consommation affichée — {local_start:%B %Y}</h2><div class="cards funding">
<article><span>Estimation réelle</span><strong>{_money(funding.raw_consumption_micro_eur)}</strong></article>
<article><span>Ajustements</span><strong>{_money(funding.adjustment_micro_eur, signed=True)}</strong></article>
<article><span>Cumul public</span><strong>{_money(funding.estimated_consumption_micro_eur)}</strong></article></div>
<form method="post" action="/consumption-adjustments"><input type="hidden" name="csrf" value="{html.escape(csrf_token)}">
<label>Montant en euros<input name="amount" type="number" min="0.01" max="1000000" step="0.01" required placeholder="5,00"></label>
<label>Motif<input name="note" maxlength="200" required placeholder="Correction de test"></label>
<div class="actions"><button name="direction" value="subtract">− Retirer du cumul</button><button class="primary" name="direction" value="add">+ Ajouter au cumul</button></div></form>
<h3>Historique des ajustements</h3><table><thead><tr><th>Date</th><th>Montant</th><th>Motif</th></tr></thead><tbody>{adjustment_rows}</tbody></table></section>
<section><h2>Derniers webhooks Ko-fi</h2><table><thead><tr><th>Reçu</th><th>Type</th><th>Test</th><th>Montant</th></tr></thead><tbody>{kofi_rows}</tbody></table>
<p class="muted">Les noms, e-mails, messages et détails de commande ne sont jamais conservés.</p></section>
</main></body></html>"""


def _money(micros: int, *, signed: bool = False) -> str:
    value = Decimal(micros) / Decimal(1_000_000)
    prefix = "+" if signed and value > 0 else ""
    return f"{prefix}{value:.2f} €".replace(".", ",")


def _date(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value).astimezone(UTC)
        return parsed.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return "—"


_STYLE = """
:root{font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:#172019;background:#f3f1e9}*{box-sizing:border-box}
body{margin:0}main{width:min(1100px,calc(100% - 32px));margin:48px auto 80px}header{margin-bottom:32px}h1{font-size:clamp(2rem,5vw,4rem);margin:.15em 0}h2{margin-top:0}h3{margin-top:28px}.eyebrow{text-transform:uppercase;letter-spacing:.14em;font-size:.75rem;font-weight:800;color:#56715c}
section{background:#fff;border:1px solid #d9d7cc;border-radius:18px;padding:24px;margin:20px 0;box-shadow:0 8px 30px #2036240d}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;background:none;border:0;box-shadow:none;padding:0}.cards article{background:#183d29;color:#fff;border-radius:14px;padding:20px}.cards span{display:block;color:#c6d9ca}.cards strong{display:block;font-size:1.8rem;margin-top:8px}.cards small{display:block;margin-top:5px;color:#c6d9ca}.funding article:nth-child(2){background:#73582d}.funding article:nth-child(3){background:#265f3e}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:11px;border-bottom:1px solid #e7e4da}thead th{font-size:.78rem;text-transform:uppercase;color:#647068}.money{font-variant-numeric:tabular-nums}.muted{color:#647068;font-size:.9rem}
form{display:grid;grid-template-columns:1fr 2fr;gap:16px;margin:24px 0}label{font-weight:700}input{display:block;width:100%;margin-top:7px;padding:12px;border:1px solid #a8ada7;border-radius:9px;font:inherit}.actions{grid-column:1/-1;display:flex;gap:10px;justify-content:flex-end}button{border:1px solid #315940;border-radius:9px;padding:11px 16px;background:#fff;color:#24432e;font-weight:800;cursor:pointer}.primary{background:#315940;color:#fff}.notice{padding:13px 16px;border-radius:9px}.ok{background:#d9f2df}.error{background:#f7dddd}@media(max-width:650px){main{margin-top:24px}section{padding:16px;overflow-x:auto}form{grid-template-columns:1fr}.actions{justify-content:stretch}.actions button{flex:1}}
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=Path(os.environ.get("TARA_WEB_STORAGE_ROOT", "/data/runtime")),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(
            os.environ.get("TARA_WEB_SQLITE_PATH", "/data/runtime/db/tara-web.sqlite3")
        ),
    )
    parser.add_argument(
        "--timezone", default=os.environ.get("TARA_ADMIN_TIMEZONE", "Europe/Paris")
    )
    arguments = parser.parse_args()
    app = create_admin_app(
        ConnectionFactory(arguments.database, arguments.storage_root),
        timezone=arguments.timezone,
    )
    uvicorn.run(app, host=arguments.host, port=arguments.port, access_log=False)


if __name__ == "__main__":
    main()
