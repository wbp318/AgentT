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
    from core.entity_context import seed_entities

    console.print("[bold blue]Initializing database...[/bold blue]")
    _init_db()
    console.print("[green]Tables created.[/green]")

    with get_session() as session:
        seed_entities(session)
    console.print("[green]Entities seeded.[/green]")
    console.print("[bold green]Database ready.[/bold green]")


@cli.command()
def run():
    """Start the full agent (scanner watcher + web dashboard)."""
    from core.agent import AgentT
    from modules.scanner.watcher import ScannerWatcher
    from modules.scanner.ocr import OCRProcessor
    from modules.scanner.classifier import DocumentClassifier
    from modules.scanner.extractor import DataExtractor
    from modules.documents.manager import DocumentManager
    from config.settings import WEB_HOST, WEB_PORT, SCANNER_WATCH_DIR

    # Ensure DB is initialized
    from database.db import init_db as _init_db, get_session
    from core.entity_context import seed_entities
    _init_db()
    with get_session() as session:
        seed_entities(session)

    console.print("[bold blue]Starting AgentT...[/bold blue]")

    # Build agent
    agent = AgentT()
    agent.register_module("scanner_watcher", ScannerWatcher())
    agent.register_module("ocr", OCRProcessor())
    agent.register_module("classifier", DocumentClassifier())
    agent.register_module("extractor", DataExtractor())
    agent.register_module("document_manager", DocumentManager())

    agent.start()
    console.print(f"[green]Scanner watching:[/green] {SCANNER_WATCH_DIR}")
    console.print(f"[green]Dashboard:[/green] http://{WEB_HOST}:{WEB_PORT}")

    # Run web server in a thread
    import uvicorn
    from web.app import app

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
    _init_db()

    console.print(f"[bold blue]Starting dashboard at http://{WEB_HOST}:{WEB_PORT}[/bold blue]")

    import uvicorn
    from web.app import app
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)


@cli.command()
def scan():
    """Start only the scanner watcher (no web dashboard)."""
    from core.agent import AgentT
    from modules.scanner.watcher import ScannerWatcher
    from modules.scanner.ocr import OCRProcessor
    from modules.scanner.classifier import DocumentClassifier
    from modules.scanner.extractor import DataExtractor
    from modules.documents.manager import DocumentManager
    from config.settings import SCANNER_WATCH_DIR

    from database.db import init_db as _init_db, get_session
    from core.entity_context import seed_entities
    _init_db()
    with get_session() as session:
        seed_entities(session)

    agent = AgentT()
    agent.register_module("scanner_watcher", ScannerWatcher())
    agent.register_module("ocr", OCRProcessor())
    agent.register_module("classifier", DocumentClassifier())
    agent.register_module("extractor", DataExtractor())
    agent.register_module("document_manager", DocumentManager())

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
    from database.models import Document, Entity, ApprovalRequest, DocumentStatus, ApprovalStatus

    with get_session() as session:
        entities = session.query(Entity).filter(Entity.active == True).all()
        total_docs = session.query(Document).count()
        filed_docs = session.query(Document).filter(Document.status == DocumentStatus.FILED).count()
        error_docs = session.query(Document).filter(Document.status == DocumentStatus.ERROR).count()
        pending_approvals = session.query(ApprovalRequest).filter(ApprovalRequest.status == ApprovalStatus.PENDING).count()

    console.print("\n[bold]AgentT Status[/bold]")
    console.print(f"  Entities:          {len(entities)}")
    for e in entities:
        console.print(f"    - {e.name} ({e.entity_type.value}, {e.state})")
    console.print(f"  Total Documents:   {total_docs}")
    console.print(f"  Filed:             [green]{filed_docs}[/green]")
    console.print(f"  Errors:            [red]{error_docs}[/red]")
    console.print(f"  Pending Approvals: [yellow]{pending_approvals}[/yellow]")
    console.print()


if __name__ == "__main__":
    cli()
