"""subf2m - compatibility alias import name for pysubzone.

The implementation lives in the ``pysubzone`` distribution (the Python API,
CLI and TUI subtitle downloader for subzone.ir, a Subf2m subtitle database
engine, https://github.com/mackeos/PySubZone).  Every public name of
``pysubzone`` is re-exported here unchanged, so ``import subf2m`` is a
drop-in for ``import pysubzone``.
"""

from pysubzone import *  # noqa: F401,F403 - re-export the public API
from pysubzone import __version__  # noqa: F401
from pysubzone.scoring import (  # noqa: F401 - scorer entry points
    pick_best,
    rank_versions,
    score_release,
    score_version,
    season_info,
)
