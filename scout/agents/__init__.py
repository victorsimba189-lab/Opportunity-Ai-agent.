from .base import Context, Scout
from .internships import InternshipScout
from .programmes import ProgrammeScout
from .startups import StartupScout

SCOUTS: dict[str, type[Scout]] = {
    "internships": InternshipScout,
    "programmes": ProgrammeScout,
    "startups": StartupScout,
}

__all__ = ["Context", "Scout", "SCOUTS"]
