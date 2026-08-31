# ruff: noqa: E501
"""Private operator dashboard with aggregate, privacy-safe statistics."""

from __future__ import annotations

import argparse
import base64
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
_STATUS_LABELS = {
    "queued": "En attente",
    "running": "En cours",
    "cancel_requested": "Annulation demandée",
    "stopping": "Arrêt en cours",
    "completed": "Terminée",
    "failed": "Échouée",
    "timed_out": "Délai dépassé",
    "cancelled": "Annulée",
    "cancel_failed": "Échec de l’annulation",
    "expired": "Expirée",
    "deleted": "Supprimée",
}
_STAGE_LABELS = {
    "queued": "En attente",
    "input_validation": "Validation des entrées",
    "transcription": "Transcription",
    "session_preparation": "Préparation de la session",
    "narrative_analysis": "Analyse narrative",
    "synthesis": "Synthèse",
    "verification": "Vérification",
    "result_ready": "Résultat prêt",
}
_ERROR_LABELS = {
    "input_invalid": "Entrées invalides",
    "input_too_large": "Entrées trop volumineuses",
    "prompt_injection_detected": "Instructions suspectes détectées dans un document",
    "prompt_security_check_failed": "Vérification de sécurité indisponible",
    "transcription_failed": "Moteur de transcription indisponible ou en erreur",
    "processing_failed": "Traitement interrompu par une erreur",
    "timeout": "Durée maximale de traitement dépassée",
    "cancel_failed": "Annulation impossible",
    "server_interrupted": "Traitement interrompu par le serveur",
    "artifact_write_failed": "Écriture du résultat impossible",
    "result_integrity_failed": "Vérification de l’intégrité du résultat impossible",
}
_EVENT_LABELS = {
    "run_failed": "Échec du traitement",
    "warning_raised": "Avertissement",
    "retry_scheduled": "Nouvelle tentative planifiée",
}


def create_admin_app(
    database: ConnectionFactory,
    *,
    timezone: str = "Europe/Helsinki",
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver"),
    admin_password: str | None = None,
) -> FastAPI:
    non_loopback = set(allowed_hosts) - {"127.0.0.1", "localhost", "::1", "testserver"}
    if non_loopback and (admin_password is None or len(admin_password) < 20):
        raise ValueError("non-loopback admin hosts require a strong password")
    zone = ZoneInfo(timezone)
    csrf_token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        connection = database.connect()
        try:
            if schema_version(connection) != MIGRATIONS[-1].version:
                raise RuntimeError(
                    "admin dashboard requires the latest database schema"
                )
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
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))

    @app.middleware("http")
    async def admin_authentication(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path == "/health" or admin_password is None:
            return await call_next(request)
        supplied = _basic_password(request.headers.get("authorization"))
        if supplied is None or not hmac.compare_digest(supplied, admin_password):
            return PlainTextResponse(
                "Authentication required",
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Basic realm="Tara administration", charset="UTF-8"'
                },
            )
        return await call_next(request)

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
            _dashboard_html(
                database, zone, csrf_token, request.query_params.get("saved")
            )
        )

    @app.post("/consumption-adjustments")
    async def adjust_consumption(request: Request) -> RedirectResponse:
        try:
            form = await _form(request)
            supplied = form.get("csrf", [""])
            if len(supplied) != 1 or not hmac.compare_digest(supplied[0], csrf_token):
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


def _basic_password(authorization: str | None) -> str | None:
    scheme, separator, encoded = (authorization or "").partition(" ")
    if not separator or scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    username, separator, password = decoded.partition(":")
    return password if separator and username == "tara-admin" else None


def _admin_password() -> str | None:
    path = os.environ.get("TARA_ADMIN_PASSWORD_FILE")
    if path:
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("unable to read TARA_ADMIN_PASSWORD_FILE") from exc
        return value or None
    value = os.environ.get("TARA_ADMIN_PASSWORD")
    return value.strip() if value and value.strip() else None


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
    failed_jobs = analytics.recent_failed_jobs()
    technical_logs = analytics.recent_technical_logs()
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
    status_rows = (
        "".join(
            f"<tr><th>{html.escape(_STATUS_LABELS.get(status, status))}</th><td>{count}</td></tr>"
            for status, count in statuses.items()
        )
        or '<tr><td colspan="2">Aucune analyse enregistrée.</td></tr>'
    )
    failed_job_rows = (
        "".join(
            f"<tr><td>{_date(row.failed_at, zone)}</td><td><code>{html.escape(row.public_id)}</code></td>"
            f'<td><span class="badge">{html.escape(_STATUS_LABELS.get(row.status, row.status))}</span></td>'
            f"<td>{html.escape(_STAGE_LABELS.get(row.stage, row.stage))}</td>"
            f"<td>{html.escape(_failure_label(row.error_code, row.stage))}<br>"
            f"<code>{html.escape(row.error_code or 'cause_non_renseignee')}</code></td></tr>"
            for row in failed_jobs
        )
        or '<tr><td colspan="5">Aucune analyse échouée.</td></tr>'
    )
    technical_log_rows = (
        "".join(
            f"<tr><td>{_date(row.created_at, zone)}</td>"
            f"<td><code>{html.escape(row.public_id)}</code><br><small>Tentative {row.attempt_number}</small></td>"
            f"<td>{html.escape(_EVENT_LABELS.get(row.event_type, row.event_type))}</td>"
            f"<td>{html.escape(_STAGE_LABELS.get(row.stage, row.stage))}</td>"
            f"<td><code>{html.escape(row.code)}</code></td></tr>"
            for row in technical_logs
        )
        or '<tr><td colspan="5">Aucun événement technique enregistré.</td></tr>'
    )
    adjustment_rows = (
        "".join(
            f'<tr><td>{_date(row.created_at, zone)}</td><td class="money">{_money(row.amount_micro_eur, signed=True)}</td>'
            f"<td>{html.escape(row.note)}</td></tr>"
            for row in adjustments
        )
        or '<tr><td colspan="3">Aucun ajustement manuel.</td></tr>'
    )
    kofi_rows = (
        "".join(
            f"<tr><td>{_date(row.received_at, zone)}</td><td>{html.escape(row.event_type)}</td>"
            f"<td>{'Oui' if row.is_test_transaction else 'Non'}</td>"
            f'<td class="money">{_money(row.amount_micros)} {html.escape(row.currency)}</td></tr>'
            for row in kofi
        )
        or '<tr><td colspan="4">Aucun webhook Ko-fi reçu.</td></tr>'
    )
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
<section><h2>Dernières analyses échouées</h2><table><thead><tr><th>Date</th><th>Analyse</th><th>État</th><th>Étape</th><th>Raison</th></tr></thead><tbody>{failed_job_rows}</tbody></table>
<p class="muted">Les codes techniques permettent de retrouver rapidement la catégorie d’erreur dans les journaux, sans afficher leur contenu sensible.</p></section>
<section><h2>Journal technique</h2><table><thead><tr><th>Date</th><th>Analyse</th><th>Événement</th><th>Étape</th><th>Code</th></tr></thead><tbody>{technical_log_rows}</tbody></table>
<p class="muted">Extrait structuré persistant en fuseau Europe/Helsinki (EET/EEST). Seuls les identifiants d’analyse, étapes, événements et codes autorisés sont affichés ; aucun contenu utilisateur, secret, message provider ou chemin interne n’est exposé.</p></section>
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


def _failure_label(error_code: str | None, stage: str) -> str:
    if error_code == "processing_failed" and stage == "transcription":
        return "Échec pendant la transcription"
    if error_code is None:
        return "Cause non renseignée"
    return _ERROR_LABELS.get(error_code, "Erreur non classée")


def _money(micros: int, *, signed: bool = False) -> str:
    value = Decimal(micros) / Decimal(1_000_000)
    prefix = "+" if signed and value > 0 else ""
    return f"{prefix}{value:.2f} €".replace(".", ",")


def _date(value: str, zone: ZoneInfo) -> str:
    try:
        parsed = datetime.fromisoformat(value).astimezone(zone)
        return parsed.strftime("%Y-%m-%d %H:%M %Z")
    except ValueError:
        return "—"


_STYLE = """
:root{font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:#172019;background:#f3f1e9}*{box-sizing:border-box}
body{margin:0}main{width:min(1100px,calc(100% - 32px));margin:48px auto 80px}header{margin-bottom:32px}h1{font-size:clamp(2rem,5vw,4rem);margin:.15em 0}h2{margin-top:0}h3{margin-top:28px}.eyebrow{text-transform:uppercase;letter-spacing:.14em;font-size:.75rem;font-weight:800;color:#56715c}
section{background:#fff;border:1px solid #d9d7cc;border-radius:18px;padding:24px;margin:20px 0;box-shadow:0 8px 30px #2036240d}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;background:none;border:0;box-shadow:none;padding:0}.cards article{background:#183d29;color:#fff;border-radius:14px;padding:20px}.cards span{display:block;color:#c6d9ca}.cards strong{display:block;font-size:1.8rem;margin-top:8px}.cards small{display:block;margin-top:5px;color:#c6d9ca}.funding article:nth-child(2){background:#73582d}.funding article:nth-child(3){background:#265f3e}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:11px;border-bottom:1px solid #e7e4da;vertical-align:top}thead th{font-size:.78rem;text-transform:uppercase;color:#647068}.money{font-variant-numeric:tabular-nums}.muted{color:#647068;font-size:.9rem}code{font-size:.82rem;overflow-wrap:anywhere}.badge{display:inline-block;padding:3px 8px;border-radius:999px;background:#f7dddd;color:#7a2929;font-size:.82rem;font-weight:800}
form{display:grid;grid-template-columns:1fr 2fr;gap:16px;margin:24px 0}label{font-weight:700}input{display:block;width:100%;margin-top:7px;padding:12px;border:1px solid #a8ada7;border-radius:9px;font:inherit}.actions{grid-column:1/-1;display:flex;gap:10px;justify-content:flex-end}button{border:1px solid #315940;border-radius:9px;padding:11px 16px;background:#fff;color:#24432e;font-weight:800;cursor:pointer}.primary{background:#315940;color:#fff}.notice{padding:13px 16px;border-radius:9px}.ok{background:#d9f2df}.error{background:#f7dddd}@media(max-width:650px){main{margin-top:24px}section{padding:16px;overflow-x:auto}form{grid-template-columns:1fr}.actions{justify-content:stretch}.actions button{flex:1}}
"""


def main() -> None:
    parser = argparse.ArgumentParser()
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
        "--timezone", default=os.environ.get("TARA_ADMIN_TIMEZONE", "Europe/Helsinki")
    )
    parser.add_argument(
        "--allowed-hosts",
        default=os.environ.get("TARA_ADMIN_ALLOWED_HOSTS", "127.0.0.1,localhost"),
    )
    arguments = parser.parse_args()
    allowed_hosts = tuple(
        host.strip() for host in arguments.allowed_hosts.split(",") if host.strip()
    )
    if not allowed_hosts or "*" in allowed_hosts:
        parser.error("--allowed-hosts must contain explicit host names or addresses")
    admin_password = _admin_password()
    if set(allowed_hosts) - {"127.0.0.1", "localhost", "::1"} and (
        admin_password is None or len(admin_password) < 20
    ):
        parser.error(
            "non-loopback admin hosts require TARA_ADMIN_PASSWORD or "
            "TARA_ADMIN_PASSWORD_FILE with at least 20 characters"
        )
    app = create_admin_app(
        ConnectionFactory(arguments.database, arguments.storage_root),
        timezone=arguments.timezone,
        allowed_hosts=allowed_hosts,
        admin_password=admin_password,
    )
    bind_host = (
        "0.0.0.0" if os.environ.get("TARA_ADMIN_CONTAINER_BIND") == "1" else "127.0.0.1"
    )
    uvicorn.run(app, host=bind_host, port=arguments.port, access_log=False)


if __name__ == "__main__":
    main()
