# snmp-query-engine (Python client)

Python client library for the
[snmp-query-engine](https://github.com/tobez/snmp-query-engine) daemon — a
multiplexing SNMP query engine that performs throttled SNMP queries towards
many destinations on behalf of its clients.

Distribution name `snmp-query-engine`, import name `sqe`.

```sh
pip install snmp-query-engine        # or: uv add snmp-query-engine
```

## Synchronous client

```python
import sqe

with sqe.Client() as c:  # daemon on 127.0.0.1:7667
    c.setopt("10.0.0.1", 161, {"community": "secret", "version": 2})
    for vb in c.get("10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"]):
        print(vb.oid, vb.value if vb.ok else f"error: {vb.error}")
    for vb in c.gettable("10.0.0.1", 161, "1.3.6.1.2.1.2.2.1.2"):
        print(vb.oid, vb.value)
```

The client is thread-safe; call it from a thread pool for concurrency.

## asyncio client

```python
import asyncio
import sqe

async def main():
    async with sqe.AsyncClient() as c:
        vbs = await c.get("10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"])
        print(vbs[0].value)

asyncio.run(main())
```

Both clients expose the same six methods: `setopt`, `getopt`, `get`,
`gettable`, `info`, `dest_info`. See the
[daemon manual](https://github.com/tobez/snmp-query-engine/blob/main/manual.mdwn)
for the available options and the semantics behind them.

## Semantics worth knowing

- **Values are msgpack-native.** SNMP string values arrive as `bytes`
  (they are genuinely binary); OIDs, map keys, and error strings are `str`.
- **Per-OID errors are values, not exceptions**: check `VarBind.ok` /
  `VarBind.error`. Request-level daemon errors raise `sqe.RequestError`.
- **Reconnect is automatic by default**: on connection loss, in-flight
  requests fail with `sqe.ConnectionLost`, the client reconnects with
  capped exponential backoff, replays your accumulated `setopt` options,
  and only then releases new traffic. Opt out with `reconnect=False`.
- **Per-call safety timeout**: every method takes `timeout=` (seconds) as a
  guard against a wedged daemon; `None` (default) trusts the daemon's own
  timeout/retry machinery, which always answers eventually.
- **SETOPT options pass through verbatim** as a dict, named exactly as in
  the daemon manual. Convenience: `bytes` values for the hexstring options
  (`engineid`, `authkul`, `privkul`) are hex-encoded automatically.

## Development

```sh
uv sync
uv run pytest                  # unit + transport tiers
uv run pytest -m integration   # live tier: needs a daemon binary
                               # ($SQE_BINARY, PATH, or ../snmp-query-engine)
uv run ruff check . && uv run mypy
```

The sans-I/O protocol core lives in `sqe/protocol.py`; alternative event
loops (trio, ...) can drive it directly.

## License

BSD 2-clause, matching the daemon and the Perl client
[Net::SNMP::QueryEngine::AnyEvent](https://metacpan.org/pod/Net::SNMP::QueryEngine::AnyEvent).
