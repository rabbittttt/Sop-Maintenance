"""Keep test data independent of the user's sibling order-analysis project."""
from pathlib import Path

from sop import source_rules as sop_source_rules

# Production always reads the sibling project. Tests use a tracked, empty
# baseline; generation-mode tests patch their own temporary live configuration.
sop_source_rules.DEFAULT_CONFIG_PATH = Path(__file__).parent / "fixtures" / "shared_config.py"
