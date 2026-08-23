"""Reserved for gateway client code. **Importable only from relay.infra.telemetry.**

Nothing lives here in S1. The package is claimed now so that the architecture
contract in ``.importlinter`` has a stable name to forbid, and so that the first
person to add a gateway client puts it somewhere the guard already covers
instead of inventing a module the guard does not know about.
"""
