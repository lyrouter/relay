"""The **only** package permitted to import a gateway client (TA-1).

Empty in S1 by design: TA-1 is an interface declaration with no implementation
and no adapter. The package exists now so the ``.importlinter`` contract has a
real allowed-source to name, rather than being written against a module that
does not yet exist and therefore silently matching nothing.

TA-2…TA-4 land here. Everything else in the codebase talks to
``relay.ports.telemetry.TelemetryAdapter``.
"""
