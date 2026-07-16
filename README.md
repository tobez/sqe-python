# snmp-query-engine (Python client)

Python client library for the
[snmp-query-engine](https://github.com/tobez/snmp-query-engine) daemon — a
multiplexing SNMP query engine that performs throttled SNMP queries towards
many destinations on behalf of its clients.

Distribution name `snmp-query-engine`, import name `sqe`.

```python
import sqe

with sqe.Client() as c:  # daemon on 127.0.0.1:7667
    for vb in c.get("10.0.0.1", 161, ["1.3.6.1.2.1.1.5.0"]):
        print(vb.oid, vb.value)
```

Work in progress; not released yet.
