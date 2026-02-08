"""
APScheduler-based task scheduler for AgentT.
Runs background jobs: overdue check, DB backup, scanner sweep, status digest.
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from config.settings import BASE_DIR, BACKUP_DIR, SCANNER_WATCH_DIR, LOG_DIR
from core.audit import log_action
from core.events import EventBus, Event, FILE_ARRIVED
from database.db import get_session
from database.models import (
    Document, Invoice, ApprovalRequest, Transaction,
    InvoiceStatus, ApprovalStatus, QBSyncStatus, DocumentStatus,
)

logger = logging.getLogger(__name__)

DB_PATH = BASE_DIR / "data" / "agent_t.db"
MAX_BACKUPS = 30


class TaskScheduler:
    """Runs scheduled background jobs. Follows module contract (setup/start/stop)."""

    def __init__(self):
        self._event_bus = None
        self._scheduler = None
        self._job_history = {}

    def setup(self, event_bus: EventBus):
        self._event_bus = event_bus

    def start(self):
        self._scheduler = BackgroundScheduler(timezone="America/Chicago")

        self._scheduler.add_job(
            self._run_check_overdue,
            "cron", hour=7, minute=0,
            id="check_overdue",
            name="Check Overdue Invoices",
        )
        self._scheduler.add_job(
            self._run_database_backup,
            "cron", hour=2, minute=0,
            id="database_backup",
            name="Database Backup",
        )
        self._scheduler.add_job(
            self._run_scanner_sweep,
            "interval", minutes=5,
            id="scanner_sweep",
            name="Scanner Sweep",
        )
        self._scheduler.add_job(
            self._run_status_digest,
            "cron", hour=18, minute=0,
            id="status_digest",
            name="Status Digest",
        )

        # Initialize history entries
        for job in self._scheduler.get_jobs():
            self._job_history[job.id] = {
                "last_run": None,
                "status": "never_run",
                "detail": "",
            }

        self._scheduler.start()
        logger.info("TaskScheduler started with 4 jobs")

    def stop(self):
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            logger.info("TaskScheduler stopped")

    def get_jobs_status(self) -> list[dict]:
        """Return status info for all jobs, for the web UI."""
        if not self._scheduler:
            return []

        result = []
        for job in self._scheduler.get_jobs():
            history = self._job_history.get(job.id, {})
            result.append({
                "id": job.id,
                "name": job.name,
                "last_run": history.get("last_run"),
                "status": history.get("status", "never_run"),
                "detail": history.get("detail", ""),
                "next_run": job.next_run_time,
            })
        return result

    def trigger_job(self, job_id: str) -> bool:
        """Manually trigger a job. Returns True if found and triggered."""
        if not self._scheduler:
            return False
        job = self._scheduler.get_job(job_id)
        if not job:
            return False
        job.modify(next_run_time=datetime.now(job.next_run_time.tzinfo))
        return True

    def _record(self, job_id: str, status: str, detail: str = ""):
        self._job_history[job_id] = {
            "last_run": datetime.now(),
            "status": status,
            "detail": detail,
        }

    # --- Job implementations ---

    def _run_check_overdue(self):
        job_id = "check_overdue"
        try:
            from modules.billing.invoice_generator import InvoiceGenerator
            gen = InvoiceGenerator()
            gen.setup(self._event_bus or EventBus())
            newly = gen.check_overdue()
            detail = f"{len(newly)} invoice(s) marked overdue"
            self._record(job_id, "success", detail)
            log_action("scheduler", "check_overdue_ran", detail={"newly_overdue": len(newly)})
            logger.info(f"[check_overdue] {detail}")
        except Exception as e:
            self._record(job_id, "error", str(e))
            log_action("scheduler", "check_overdue_error", detail={"error": str(e)}, severity="error")
            logger.error(f"[check_overdue] {e}")

    def _run_database_backup(self):
        job_id = "database_backup"
        try:
            if not DB_PATH.exists():
                self._record(job_id, "skipped", "Database file not found")
                return

            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dest = BACKUP_DIR / f"agent_t_{timestamp}.db"
            shutil.copy2(str(DB_PATH), str(dest))

            # Prune old backups
            backups = sorted(BACKUP_DIR.glob("agent_t_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in backups[MAX_BACKUPS:]:
                old.unlink()

            detail = f"Backed up to {dest.name}"
            if len(backups) > MAX_BACKUPS:
                detail += f", pruned {len(backups) - MAX_BACKUPS} old"
            self._record(job_id, "success", detail)
            log_action("scheduler", "database_backup_ran", detail={"backup_file": dest.name})
            logger.info(f"[database_backup] {detail}")
        except Exception as e:
            self._record(job_id, "error", str(e))
            log_action("scheduler", "database_backup_error", detail={"error": str(e)}, severity="error")
            logger.error(f"[database_backup] {e}")

    def _run_scanner_sweep(self):
        job_id = "scanner_sweep"
        try:
            from modules.scanner.watcher import SUPPORTED_EXTENSIONS

            watch_dir = Path(SCANNER_WATCH_DIR)
            if not watch_dir.exists():
                self._record(job_id, "skipped", "Watch directory not found")
                return

            # Get known filenames from DB
            with get_session() as session:
                known = {row[0] for row in session.query(Document.original_filename).all()}

            new_count = 0
            for f in watch_dir.iterdir():
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS:
                    if f.name not in known:
                        if self._event_bus:
                            self._event_bus.emit(Event(FILE_ARRIVED, {
                                "file_path": str(f),
                                "filename": f.name,
                            }))
                        new_count += 1

            detail = f"{new_count} new file(s) found"
            self._record(job_id, "success", detail)
            if new_count > 0:
                log_action("scheduler", "scanner_sweep_found", detail={"new_files": new_count})
            logger.info(f"[scanner_sweep] {detail}")
        except Exception as e:
            self._record(job_id, "error", str(e))
            log_action("scheduler", "scanner_sweep_error", detail={"error": str(e)}, severity="error")
            logger.error(f"[scanner_sweep] {e}")

    def _run_status_digest(self):
        job_id = "status_digest"
        try:
            with get_session() as session:
                pending_approvals = session.query(ApprovalRequest).filter(
                    ApprovalRequest.status == ApprovalStatus.PENDING
                ).count()
                overdue_invoices = session.query(Invoice).filter(
                    Invoice.status == InvoiceStatus.OVERDUE
                ).count()
                error_docs = session.query(Document).filter(
                    Document.status == DocumentStatus.ERROR
                ).count()
                pending_txns = session.query(Transaction).filter(
                    Transaction.qb_sync_status == QBSyncStatus.PENDING
                ).count()

            lines = [
                f"=== Daily Digest {datetime.now().strftime('%Y-%m-%d %H:%M')} ===",
                f"  Pending approvals: {pending_approvals}",
                f"  Overdue invoices:  {overdue_invoices}",
                f"  Document errors:   {error_docs}",
                f"  Pending QB txns:   {pending_txns}",
                "",
            ]
            digest_text = "\n".join(lines)

            digest_path = LOG_DIR / "daily_digest.log"
            with open(digest_path, "a", encoding="utf-8") as f:
                f.write(digest_text)

            detail = f"approvals={pending_approvals}, overdue={overdue_invoices}, errors={error_docs}"
            self._record(job_id, "success", detail)
            log_action("scheduler", "status_digest_ran", detail={
                "pending_approvals": pending_approvals,
                "overdue_invoices": overdue_invoices,
                "error_docs": error_docs,
                "pending_txns": pending_txns,
            })
            logger.info(f"[status_digest] {detail}")
        except Exception as e:
            self._record(job_id, "error", str(e))
            log_action("scheduler", "status_digest_error", detail={"error": str(e)}, severity="error")
            logger.error(f"[status_digest] {e}")
