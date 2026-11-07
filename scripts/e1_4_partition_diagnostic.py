"""Which outcomes fall outside K1-K4 in E1.4 run 1? Reads the raw records only."""
import csv, gzip, sys
from collections import Counter, defaultdict
path = sys.argv[1]
rows = defaultdict(dict)
with gzip.open(path, 'rt', encoding='utf-8') as fh:
    for r in csv.DictReader(fh):
        k = (r['case'], r['state'], r['ue_id'], r['realization'])
        tag = ('35' if r['frequency_hz'].startswith('35') else '72') + r['direction']
        rows[k][tag] = (r['feasible_F1_link_closure'] == 'True', r['feasible_F2_m2410_equivalent'] == 'True')
outside = Counter(); total = Counter(); pattern = Counter()
for (case, state, ue, real), v in rows.items():
    for p, name in ((0, 'F1'), (1, 'F2')):
        d35, u35, d72, u72 = (v['35DL'][p], v['35UL'][p], v['72DL'][p], v['72UL'][p])
        k1 = d35 and u35 and d72 and u72
        k2 = d72 and not u72 and u35
        k3 = d72 and not u72 and not u35
        k4 = not d72
        total[(state, name)] += 1
        if not (k1 or k2 or k3 or k4):
            outside[(state, name)] += 1
            pattern[(state, name, 'd35=%d u35=%d d72=%d u72=%d' % (d35, u35, d72, u72))] += 1
print('state, profile: outcomes outside K1-K4 / total (all cases pooled)')
for key in sorted(total):
    print(f'  {key[0]:18s} {key[1]}: {outside[key]:6d} / {total[key]}')
print('patterns:')
for key, n in sorted(pattern.items()):
    print('  ', key, n)
