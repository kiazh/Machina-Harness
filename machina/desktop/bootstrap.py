"""Own one desktop database lease around the existing framed gateway lifetime."""

from importlib import import_module


def gateway_entry():
    return import_module("machina.gateway.__main__")


def acquire(role: str):
    from machina.db.lifecycle import acquire as acquire_lease

    return acquire_lease(role)


def release(lease) -> None:
    from machina.db.lifecycle import release as release_lease

    release_lease(lease)


def main() -> None:
    entry = gateway_entry()
    # Provisioning can print or spawn children. Isolate the pipes first, using
    # the same mechanism as the terminal gateway's entry point.
    entry.isolate_protocol_streams()
    lease = acquire("desktop")
    try:
        entry.main()
    finally:
        release(lease)
