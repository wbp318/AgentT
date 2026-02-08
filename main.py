"""
AgentT — Farm Office Automation Agent
CLI entry point.
"""

import logging
import sys
import threading

import click
from rich.console import Console
from rich.logging import RichHandler

console = Console()


def setup_logging(level: str = "INFO"):
    """Configure logging with Rich handler for console output."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


@click.group()
@click.option("--log-level", default=None, help="Override log level (DEBUG, INFO, WARNING, ERROR)")
def cli(log_level):
    """AgentT — Farm Office Automation Agent"""
    from config.settings import LOG_LEVEL
    setup_logging(log_level or LOG_LEVEL)


@cli.command()
def init_db():
    """Initialize the database and seed default entities."""
    from database.db import init_db as _init_db, get_session
    from core.entity_context import seed_entities, seed_vendor_mappings

    console.print("[bold blue]Initializing database...[/bold blue]")
    _init_db()
    console.print("[green]Tables created.[/green]")

    with get_session() as session:
        seed_entities(session)
    console.print("[green]Entities seeded.[/green]")

    with get_session() as session:
        seed_vendor_mappings(session)
    console.print("[green]Vendor mappings seeded.[/green]")

    console.print("[bold green]Database ready.[/bold green]")


@cli.command()
def run():
    """Start the full agent (scanner watcher + web dashboard)."""
    from core.agent import AgentT
    from core.events import EventBus
    from modules.scanner.watcher import ScannerWatcher
    from modules.scanner.ocr import OCRProcessor
    from modules.scanner.classifier import DocumentClassifier
    from modules.scanner.extractor import DataExtractor
    from modules.documents.manager import DocumentManager
    from modules.quickbooks.categorizer import ExpenseCategorizer
    from modules.quickbooks.iif_generator import IIFGenerator
    from modules.billing.invoice_generator import InvoiceGenerator
    from modules.scheduler.task_scheduler import TaskScheduler
    from core.approval import ApprovalEngine
    from config.settings import WEB_HOST, WEB_PORT, SCANNER_WATCH_DIR

    # Ensure DB is initialized
    from database.db import init_db as _init_db, get_session
    from core.entity_context import seed_entities, seed_vendor_mappings
    _init_db()
    with get_session() as session:
        seed_entities(session)
    with get_session() as session:
        seed_vendor_mappings(session)

    console.print("[bold blue]Starting AgentT...[/bold blue]")

    # Build agent
    agent = AgentT()
    agent.register_module("scanner_watcher", ScannerWatcher())
    agent.register_module("ocr", OCRProcessor())
    agent.register_module("classifier", DocumentClassifier())
    agent.register_module("extractor", DataExtractor())
    agent.register_module("document_manager", DocumentManager())

    # Phase 2 modules
    categorizer = ExpenseCategorizer()
    iif_generator = IIFGenerator()
    approval_engine = ApprovalEngine()
    agent.register_module("categorizer", categorizer)
    agent.register_module("iif_generator", iif_generator)
    agent.register_module("approval_engine", approval_engine)

    # Phase 3 modules
    invoice_generator = InvoiceGenerator()
    agent.register_module("invoice_generator", invoice_generator)

    # Phase 4 scheduler
    scheduler = TaskScheduler()
    agent.register_module("scheduler", scheduler)

    agent.start()
    console.print(f"[green]Scanner watching:[/green] {SCANNER_WATCH_DIR}")
    console.print(f"[green]Dashboard:[/green] http://{WEB_HOST}:{WEB_PORT}")

    # Run web server in a thread
    import uvicorn
    from web.app import app

    # Set app.state references for web routes
    app.state.event_bus = agent.event_bus
    app.state.categorizer = categorizer
    app.state.iif_generator = iif_generator
    app.state.approval_engine = approval_engine
    app.state.invoice_generator = invoice_generator
    app.state.scheduler = scheduler

    server_thread = threading.Thread(
        target=uvicorn.run,
        kwargs={"app": app, "host": WEB_HOST, "port": WEB_PORT, "log_level": "warning"},
        daemon=True,
    )
    server_thread.start()

    console.print("[bold green]AgentT is running. Press Ctrl+C to stop.[/bold green]")

    try:
        server_thread.join()
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down...[/yellow]")
        agent.stop()
        console.print("[bold green]AgentT stopped.[/bold green]")


@cli.command()
def web():
    """Start only the web dashboard (no scanner)."""
    from config.settings import WEB_HOST, WEB_PORT
    from database.db import init_db as _init_db
    from core.events import EventBus
    from modules.quickbooks.categorizer import ExpenseCategorizer
    from modules.quickbooks.iif_generator import IIFGenerator
    from modules.billing.invoice_generator import InvoiceGenerator
    from modules.scheduler.task_scheduler import TaskScheduler
    from core.approval import ApprovalEngine

    _init_db()

    console.print(f"[bold blue]Starting dashboard at http://{WEB_HOST}:{WEB_PORT}[/bold blue]")

    # Create standalone instances with a minimal EventBus
    event_bus = EventBus()
    categorizer = ExpenseCategorizer()
    categorizer.setup(event_bus)
    iif_generator = IIFGenerator()
    iif_generator.setup(event_bus)
    approval_engine = ApprovalEngine()
    approval_engine.setup(event_bus)
    invoice_generator = InvoiceGenerator()
    invoice_generator.setup(event_bus)
    scheduler = TaskScheduler()
    scheduler.setup(event_bus)
    scheduler.start()

    import uvicorn
    from web.app import app

    app.state.event_bus = event_bus
    app.state.categorizer = categorizer
    app.state.iif_generator = iif_generator
    app.state.approval_engine = approval_engine
    app.state.invoice_generator = invoice_generator
    app.state.scheduler = scheduler

    try:
        uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)
    finally:
        scheduler.stop()


@cli.command()
def scan():
    """Start only the scanner watcher (no web dashboard)."""
    from core.agent import AgentT
    from modules.scanner.watcher import ScannerWatcher
    from modules.scanner.ocr import OCRProcessor
    from modules.scanner.classifier import DocumentClassifier
    from modules.scanner.extractor import DataExtractor
    from modules.documents.manager import DocumentManager
    from modules.quickbooks.categorizer import ExpenseCategorizer
    from modules.quickbooks.iif_generator import IIFGenerator
    from modules.billing.invoice_generator import InvoiceGenerator
    from modules.scheduler.task_scheduler import TaskScheduler
    from core.approval import ApprovalEngine
    from config.settings import SCANNER_WATCH_DIR

    from database.db import init_db as _init_db, get_session
    from core.entity_context import seed_entities, seed_vendor_mappings
    _init_db()
    with get_session() as session:
        seed_entities(session)
    with get_session() as session:
        seed_vendor_mappings(session)

    agent = AgentT()
    agent.register_module("scanner_watcher", ScannerWatcher())
    agent.register_module("ocr", OCRProcessor())
    agent.register_module("classifier", DocumentClassifier())
    agent.register_module("extractor", DataExtractor())
    agent.register_module("document_manager", DocumentManager())

    # Phase 2 modules
    categorizer = ExpenseCategorizer()
    iif_generator = IIFGenerator()
    approval_engine = ApprovalEngine()
    agent.register_module("categorizer", categorizer)
    agent.register_module("iif_generator", iif_generator)
    agent.register_module("approval_engine", approval_engine)

    # Phase 3 modules
    invoice_generator = InvoiceGenerator()
    agent.register_module("invoice_generator", invoice_generator)

    # Phase 4 scheduler
    scheduler = TaskScheduler()
    agent.register_module("scheduler", scheduler)

    agent.start()
    console.print(f"[green]Scanner watching:[/green] {SCANNER_WATCH_DIR}")
    console.print("[bold green]Scanner running. Press Ctrl+C to stop.[/bold green]")

    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down...[/yellow]")
        agent.stop()


@cli.command()
def status():
    """Show current system status."""
    from database.db import get_session
    from database.models import (
        Document, Entity, ApprovalRequest, Transaction, Invoice,
        DocumentStatus, ApprovalStatus, QBSyncStatus, InvoiceStatus,
    )

    with get_session() as session:
        entities = session.query(Entity).filter(Entity.active == True).all()
        entity_info = [(e.name, e.entity_type.value, e.state) for e in entities]
        total_docs = session.query(Document).count()
        filed_docs = session.query(Document).filter(Document.status == DocumentStatus.FILED).count()
        error_docs = session.query(Document).filter(Document.status == DocumentStatus.ERROR).count()
        pending_approvals = session.query(ApprovalRequest).filter(ApprovalRequest.status == ApprovalStatus.PENDING).count()

        total_txns = session.query(Transaction).count()
        pending_txns = session.query(Transaction).filter(Transaction.qb_sync_status == QBSyncStatus.PENDING).count()
        iif_generated = session.query(Transaction).filter(Transaction.qb_sync_status == QBSyncStatus.IIF_GENERATED).count()
        synced_txns = session.query(Transaction).filter(Transaction.qb_sync_status == QBSyncStatus.SYNCED).count()

        total_invoices = session.query(Invoice).count()
        draft_invoices = session.query(Invoice).filter(Invoice.status == InvoiceStatus.DRAFT).count()
        sent_invoices = session.query(Invoice).filter(Invoice.status == InvoiceStatus.SENT).count()
        paid_invoices = session.query(Invoice).filter(Invoice.status == InvoiceStatus.PAID).count()
        overdue_invoices = session.query(Invoice).filter(Invoice.status == InvoiceStatus.OVERDUE).count()

    console.print("\n[bold]AgentT Status[/bold]")
    console.print(f"  Entities:          {len(entity_info)}")
    for name, etype, state in entity_info:
        console.print(f"    - {name} ({etype}, {state})")
    console.print(f"  Total Documents:   {total_docs}")
    console.print(f"  Filed:             [green]{filed_docs}[/green]")
    console.print(f"  Errors:            [red]{error_docs}[/red]")
    console.print(f"  Pending Approvals: [yellow]{pending_approvals}[/yellow]")
    console.print()
    console.print("[bold]Transactions[/bold]")
    console.print(f"  Total:             {total_txns}")
    console.print(f"  Pending QB:        [yellow]{pending_txns}[/yellow]")
    console.print(f"  IIF Generated:     [blue]{iif_generated}[/blue]")
    console.print(f"  Synced:            [green]{synced_txns}[/green]")
    console.print()
    console.print("[bold]Invoices[/bold]")
    console.print(f"  Total:             {total_invoices}")
    console.print(f"  Draft:             {draft_invoices}")
    console.print(f"  Sent:              [blue]{sent_invoices}[/blue]")
    console.print(f"  Paid:              [green]{paid_invoices}[/green]")
    console.print(f"  Overdue:           [red]{overdue_invoices}[/red]")
    console.print()


if __name__ == "__main__":
    cli()
