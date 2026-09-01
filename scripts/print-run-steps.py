#!/usr/bin/env python3
"""Print per-step conclusions of a GitHub Actions run."""
import json, sys
run_file = sys.argv[1] if len(sys.argv) > 1 else '/tmp/run14-jobs.json'
for j in json.load(open(run_file))['jobs']:
    for s in j.get('steps', []):
        mark = {'failure': 'FAIL', 'success': 'ok  ', 'skipped': 'skip'}.get(s['conclusion'], '????')
        print(mark, s['name'])
