"""
claude-env :: CLI Entry Point

Thin dispatcher: the Click group is defined here; each command is a separate
module under ``cli/commands/`` imported and added below. Shared helpers live
in ``cli/context.py``.
"""
from __future__ import annotations

import logging

import click

from claudenv.adapters.logging import configure_logging

logger = logging.getLogger(__name__)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Verbose output")
@click.pass_context
def cli(ctx, verbose):
    """claude-env - Local-first AI governance platform."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    configure_logging(verbose=verbose)
    logger.debug("claude-env CLI invoked (verbose=%s)", verbose)


# ---------------------------------------------------------------------------
# Import commands to register them on the group.  Each module defines
# @click.command / @click.group decorated callables; importing the module
# is enough to register.
# ---------------------------------------------------------------------------
from claudenv.cli.commands.init import init  # noqa: E402
from claudenv.cli.commands.model import model  # noqa: E402
from claudenv.cli.commands.observability import budget, dashboard, feedback  # noqa: E402
from claudenv.cli.commands.onboard import onboard  # noqa: E402
from claudenv.cli.commands.ops import hooks, incident, replay, report, services  # noqa: E402
from claudenv.cli.commands.policy import policy_sim, validate  # noqa: E402
from claudenv.cli.commands.rag_cmds import index, rag, scan  # noqa: E402

# Register groups and commands explicitly (each submodule's decorator
# binds them to its own group; here we attach them to the top-level cli).
cli.add_command(init)
cli.add_command(onboard)
cli.add_command(scan)
cli.add_command(index)
cli.add_command(rag)
cli.add_command(hooks)
cli.add_command(report)
cli.add_command(replay)
cli.add_command(incident)
cli.add_command(services)
cli.add_command(budget)
cli.add_command(dashboard)
cli.add_command(feedback)

# Sub-groups
cli.add_command(model)
cli.add_command(validate)
cli.add_command(policy_sim)


if __name__ == "__main__":
    cli()
