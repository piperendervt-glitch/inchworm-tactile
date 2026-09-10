"""Genes that outlive a session.

When a session closes, the individuals still alive are ranked by how long
they have lived, and the top few of each species are written to a file. The
next session's founders are drawn from that file (and mutated once), so a
lineage carries on from one sortie to the next instead of starting over.

A species with no survivors keeps what the file already held: a wipe-out does
not erase the line.

    {"schema": 1, "saved": "...", "ecoli": [{"genome": {...}, "alive_s": 210.3}, ...],
     "jelly": [{"genome": {...}, "pacemakers": [4], "alive_s": 95.0}, ...]}
"""

import datetime
import json
import os
from pathlib import Path

SCHEMA = 1
KEEP = 8


def load(path):
    """The saved lineage, or an empty one."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return dict(schema=SCHEMA, ecoli=[], jelly=[])
    data.setdefault('ecoli', [])
    data.setdefault('jelly', [])
    return data


def clear(path, session=None):
    """Both lines died out: forget them, so the next founders come from the norm."""
    data = dict(schema=SCHEMA, ecoli=[], jelly=[],
                saved=datetime.datetime.now().isoformat(timespec='seconds'),
                extinct=str(session) if session else True)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, indent=1) + '\n', encoding='utf-8')
    os.replace(temporary, path)
    return data


def survivors(colony, species, keep=KEEP):
    """The longest-lived individuals alive in a colony, as lineage entries."""
    if species == 'ecoli':
        living = colony.creatures.values()
        rows = [dict(genome=dict(c.genome), alive_s=round(c.alive_seconds, 1),
                     divisions=c.body.divisions, bites=c.bites) for c in living]
    else:
        living = colony.jellies.values()
        rows = [dict(genome=dict(j.genome), pacemakers=[c for c, _ in j.timers],
                     alive_s=round(j.alive_seconds, 1), divisions=j.body.divisions) for j in living]
    rows.sort(key=lambda r: r['alive_s'], reverse=True)
    return rows[:keep]


def save(path, ecoli_colony=None, jelly_colony=None, keep=KEEP, session=None):
    """Write the survivors over the file, species by species. Returns what was written."""
    data = load(path)
    if ecoli_colony is not None:
        rows = survivors(ecoli_colony, 'ecoli', keep)
        if rows:
            data['ecoli'] = rows
    if jelly_colony is not None:
        rows = survivors(jelly_colony, 'jelly', keep)
        if rows:
            data['jelly'] = rows
    data['schema'] = SCHEMA
    data['saved'] = datetime.datetime.now().isoformat(timespec='seconds')
    if session:
        data['session'] = str(session)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, indent=1) + '\n', encoding='utf-8')
    os.replace(temporary, path)
    return data
