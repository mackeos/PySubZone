# subf2m (compatibility alias)

This PyPI project is a compatibility alias. The Python subtitle downloader for
[subzone.ir](https://subzone.ir) - a [Subf2m](https://subf2m.co) subtitle
database engine - is published on PyPI as **pysubzone**.

Installing `subf2m` pulls in `pysubzone`, which provides:

- the `pysubzone` and `subf2m` import names,
- the `subz` and `subf2m` CLI commands,
- everything documented at https://github.com/mackeos/PySubZone.

New code should use `pip install pysubzone` and import `pysubzone`.

## Usage

```bash
pip install subf2m          # or: pip install pysubzone

subf2m search "arcane"      # same CLI as `subz`
subz auto /media/shows -l fa -o subs --zip

python -c "import subf2m; print(subf2m.SubZoneClient())"
```

The version of this alias tracks the version of `pysubzone` it is released
with (the alias version always tracks the pysubzone release it ships with).
