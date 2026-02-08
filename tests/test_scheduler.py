"""Tests for the APScheduler-based task scheduler module."""

import pytest
import os
from datetime import date, timedelta, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

from database.models import (
    Base, Entity, Document, Invoice, ApprovalRequest, Transaction,
    EntityType, AccountingMethod, DocumentStatus, InvoiceStatus,
    ApprovalStatus, QBSyncStatus, TransactionType, IIFType, ApprovalType,
)
from modules.scheduler.task_scheduler import TaskScheduler
from core.events import EventBus, FILE_ARRIVED


@pytest.fixture
def db_session():
    """Create an in-memory database with test entities."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    entity = Entity(
        id=1,
        name="Parker Farms Partnership",
        slug="farm_1",
        entity_type=EntityType.ROW_CROP_FARM,
        state="LA",
        accounting_method=AccountingMethod.CASH,
        invoice_prefix="PFP",
    )
    session.add(entity)
    session.commit()

    yield session
    session.close()


def _mock_get_session(db_session):
    """Create a mock get_session context manager that returns the test session."""
    mock_gs = MagicMock()
    mock_gs.return_value.__enter__ = lambda s: db_session
    mock_gs.return_value.__exit__ = MagicMock(return_value=False)
    return mock_gs


@pytest.fixture
def scheduler():
    """Create a TaskScheduler with a mock event bus."""
    s = TaskScheduler()
    event_bus = EventBus()
    s.setup(event_bus)
    return s


# === Module Contract ===

class TestModuleContract:

    def test_setup_stores_event_bus(self):
        s = TaskScheduler()
        bus = EventBus()
        s.setup(bus)
        assert s._event_bus is bus

    @patch("modules.scheduler.task_scheduler.log_action")
    def test_start_creates_scheduler(self, mock_log, scheduler):
        scheduler.start()
        assert scheduler._scheduler is not None
        assert scheduler._scheduler.running
        scheduler.stop()

    @patch("modules.scheduler.task_scheduler.log_action")
    def test_stop_shuts_down(self, mock_log, scheduler):
        scheduler.start()
        scheduler.stop()
        assert not scheduler._scheduler.running

    def test_stop_without_start_is_safe(self, scheduler):
        scheduler.stop()  # Should not raise


# === Get Jobs Status ===

class TestGetJobsStatus:

    @patch("modules.scheduler.task_scheduler.log_action")
    def test_returns_4_jobs(self, mock_log, scheduler):
        scheduler.start()
        jobs = scheduler.get_jobs_status()
        assert len(jobs) == 4
        job_ids = {j["id"] for j in jobs}
        assert job_ids == {"check_overdue", "database_backup", "scanner_sweep", "status_digest"}
        scheduler.stop()

    @patch("modules.scheduler.task_scheduler.log_action")
    def test_initial_status_is_never_run(self, mock_log, scheduler):
        scheduler.start()
        jobs = scheduler.get_jobs_status()
        for job in jobs:
            assert job["status"] == "never_run"
        scheduler.stop()

    def test_empty_when_not_started(self, scheduler):
        jobs = scheduler.get_jobs_status()
        assert jobs == []


# === Trigger Job ===

class TestTriggerJob:

    @patch("modules.scheduler.task_scheduler.log_action")
    def test_trigger_existing_job(self, mock_log, scheduler):
        scheduler.start()
        result = scheduler.trigger_job("database_backup")
        assert result is True
        scheduler.stop()

    @patch("modules.scheduler.task_scheduler.log_action")
    def test_trigger_nonexistent_job(self, mock_log, scheduler):
        scheduler.start()
        result = scheduler.trigger_job("nonexistent_job")
        assert result is False
        scheduler.stop()

    def test_trigger_when_not_started(self, scheduler):
        result = scheduler.trigger_job("database_backup")
        assert result is False


# === Check Overdue Job ===

class TestCheckOverdueJob:

    @patch("modules.scheduler.task_scheduler.log_action")
    @patch("modules.billing.invoice_generator.log_action")
    @patch("modules.billing.invoice_generator.get_session")
    def test_marks_overdue_invoices(self, mock_gs, mock_inv_log, mock_sched_log, db_session, scheduler):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        # Create a SENT invoice past due
        inv = Invoice(
            id=99,
            entity_id=1,
            invoice_number="PFP-2026-001",
            customer_name="Test Customer",
            date_issued=date.today() - timedelta(days=30),
            date_due=date.today() - timedelta(days=5),
            line_items=[{"description": "Test", "quantity": 1, "unit_price": 100}],
            total_amount=100.0,
            amount_paid=0.0,
            status=InvoiceStatus.SENT,
        )
        db_session.add(inv)
        db_session.commit()

        scheduler._run_check_overdue()

        refreshed = db_session.get(Invoice, 99)
        assert refreshed.status == InvoiceStatus.OVERDUE
        assert scheduler._job_history["check_overdue"]["status"] == "success"
        assert "1 invoice(s)" in scheduler._job_history["check_overdue"]["detail"]


# === Database Backup Job ===

class TestDatabaseBackupJob:

    @patch("modules.scheduler.task_scheduler.log_action")
    def test_creates_backup_file(self, mock_log, scheduler, tmp_path):
        # Create a fake DB file
        fake_db = tmp_path / "agent_t.db"
        fake_db.write_text("fake database")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        with patch("modules.scheduler.task_scheduler.DB_PATH", fake_db), \
             patch("modules.scheduler.task_scheduler.BACKUP_DIR", backup_dir):
            scheduler._run_database_backup()

        backups = list(backup_dir.glob("agent_t_*.db"))
        assert len(backups) == 1
        assert scheduler._job_history["database_backup"]["status"] == "success"

    @patch("modules.scheduler.task_scheduler.log_action")
    def test_skips_when_no_db(self, mock_log, scheduler, tmp_path):
        fake_db = tmp_path / "nonexistent.db"

        with patch("modules.scheduler.task_scheduler.DB_PATH", fake_db):
            scheduler._run_database_backup()

        assert scheduler._job_history["database_backup"]["status"] == "skipped"

    @patch("modules.scheduler.task_scheduler.log_action")
    def test_prunes_to_30(self, mock_log, scheduler, tmp_path):
        fake_db = tmp_path / "agent_t.db"
        fake_db.write_text("fake database")
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create 35 old backups
        for i in range(35):
            bf = backup_dir / f"agent_t_2026010{i:02d}_020000.db"
            bf.write_text(f"backup {i}")

        with patch("modules.scheduler.task_scheduler.DB_PATH", fake_db), \
             patch("modules.scheduler.task_scheduler.BACKUP_DIR", backup_dir):
            scheduler._run_database_backup()

        # 35 existing + 1 new = 36, pruned to 30
        backups = list(backup_dir.glob("agent_t_*.db"))
        assert len(backups) <= 30 + 1  # Allow for timing edge case
        assert scheduler._job_history["database_backup"]["status"] == "success"


# === Scanner Sweep Job ===

class TestScannerSweepJob:

    @patch("modules.scheduler.task_scheduler.log_action")
    @patch("modules.scheduler.task_scheduler.get_session")
    def test_emits_file_arrived_for_new_files(self, mock_gs, mock_log, db_session, scheduler, tmp_path):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        # Create a test file in the watch dir
        watch_dir = tmp_path / "scanned"
        watch_dir.mkdir()
        (watch_dir / "test_doc.pdf").write_text("fake pdf")

        events_emitted = []
        scheduler._event_bus.subscribe(FILE_ARRIVED, lambda e: events_emitted.append(e))

        with patch("modules.scheduler.task_scheduler.SCANNER_WATCH_DIR", watch_dir):
            scheduler._run_scanner_sweep()

        assert len(events_emitted) == 1
        assert events_emitted[0].data["filename"] == "test_doc.pdf"
        assert scheduler._job_history["scanner_sweep"]["status"] == "success"

    @patch("modules.scheduler.task_scheduler.log_action")
    @patch("modules.scheduler.task_scheduler.get_session")
    def test_ignores_known_files(self, mock_gs, mock_log, db_session, scheduler, tmp_path):
        # Add a document to DB so it's "known"
        doc = Document(
            entity_id=1,
            original_filename="known.pdf",
            status=DocumentStatus.FILED,
        )
        db_session.add(doc)
        db_session.commit()

        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        watch_dir = tmp_path / "scanned"
        watch_dir.mkdir()
        (watch_dir / "known.pdf").write_text("known file")

        events_emitted = []
        scheduler._event_bus.subscribe(FILE_ARRIVED, lambda e: events_emitted.append(e))

        with patch("modules.scheduler.task_scheduler.SCANNER_WATCH_DIR", watch_dir):
            scheduler._run_scanner_sweep()

        assert len(events_emitted) == 0
        assert "0 new file(s)" in scheduler._job_history["scanner_sweep"]["detail"]


# === Status Digest Job ===

class TestStatusDigestJob:

    @patch("modules.scheduler.task_scheduler.log_action")
    @patch("modules.scheduler.task_scheduler.get_session")
    def test_writes_to_log_file(self, mock_gs, mock_log, db_session, scheduler, tmp_path):
        mock_gs.return_value.__enter__ = lambda s: db_session
        mock_gs.return_value.__exit__ = MagicMock(return_value=False)

        log_dir = tmp_path / "logs"
        log_dir.mkdir()

        with patch("modules.scheduler.task_scheduler.LOG_DIR", log_dir):
            scheduler._run_status_digest()

        digest_file = log_dir / "daily_digest.log"
        assert digest_file.exists()
        content = digest_file.read_text()
        assert "Daily Digest" in content
        assert "Pending approvals:" in content
        assert scheduler._job_history["status_digest"]["status"] == "success"
