"""``python -m desktop_controller pair|run`` (what the VM's Task Scheduler entry runs)."""

from .cli import controller

raise SystemExit(controller())
