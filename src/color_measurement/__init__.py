"""Software-only color measurement core. No hardware is accessed on import."""

__version__ = "0.1.0"

#: Client contract level. A client built against this backend requires it to be
#: equal. Increment it in the same commit as any client-visible change to
#: descriptors, requests, events, records or the imported API, and say what the
#: new level adds under "Client contract level" in docs/generic-frontend.md.
#: Nothing here detects such a change for you: the published fixtures carry
#: per-run identifiers, so their bytes move on every regeneration and cannot
#: stand in for the contract. Importing this package must stay free of side
#: effects and submodule imports, because a client reads the level before it
#: loads anything else.
CLIENT_CONTRACT = 49
