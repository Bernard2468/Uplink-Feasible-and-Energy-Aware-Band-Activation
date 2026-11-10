"""Fetch the Milan Telecom Italia daily activity files used by P3.3 / P3.4 from Harvard Dataverse and verify them.

Dataset: Telecom Italia, "Telecommunications - SMS, Call, Internet - MI", Harvard Dataverse doi:10.7910/DVN/EGZHFV
(Barlacchi et al., Sci. Data 2, 150055, 2015), released under ODbL 1.0. The dataset's guestbook (ID 96) requires an
email address, read from the environment variable MILAN_GUESTBOOK_EMAIL (never stored in the repository).

Each file is downloaded unchanged to external/milan_telecom/ (git-ignored), verified against Dataverse's size and MD5,
hashed with SHA-256 and made read-only. download_manifest.json records the verification. Files already present and
verified are not downloaded again.
"""
import hashlib
import json
import os
import stat
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'external' / 'milan_telecom'
API = 'https://dataverse.harvard.edu/api/access/datafile/'
DOI = 'doi:10.7910/DVN/EGZHFV'
UA = {'User-Agent': 'p3_3_fetch_milan/1.0 (research data download)', 'Accept': '*/*'}   # urllib's default agent is refused (403)
# date: (Dataverse file id, size in bytes, MD5), from the dataset's version 1.3 metadata (2020-02-09)
FILES = {
    '2013-11-04': (2674282, 361097689, '151f85ab92fc0fea449ff19cb7e50a0a'),
    '2013-11-05': (2674279, 372894401, 'f8f22dfdce95bf66d6fa9f699afbdf5e'),
    '2013-11-06': (2674283, 374625924, '1c4e7de6a559357560927796b5c63c06'),
    '2013-11-07': (2674271, 373036285, 'dc0fac4c7fccb4c3328a77f450230c5c'),
    '2013-11-08': (2674261, 376123111, 'e2c02fde5673256145b29eb73321ca12'),
    '2013-11-09': (2674268, 332440742, 'ceee41ef748750475d57f16770b0abe0'),
    '2013-11-10': (2674259, 314441422, '4b7a506876b06565272af93048aa54b8'),
    '2013-11-18': (2674260, 348325345, '3455490d465be13d6a107e5982672008'),
    '2013-11-19': (2674280, 356545029, 'f9d64c99a62e560e754dbe40c0510786'),
    '2013-11-20': (2674263, 360144384, 'ea4904a581e9a829cebf2fdbaf679f25'),
    '2013-11-21': (2674276, 358700264, 'd2943e3d4f31a5d8f7575796d3b47368'),
    '2013-11-22': (2674269, 360316105, '07a02618f46af85618247104bc46e974'),
    '2013-11-23': (2674277, 317406788, 'c0f44412f4d49cd7fa0edfff31ecd887'),
    '2013-11-24': (2674258, 303987293, '8bffcfdffdfa73a18c9bcc64bf1de2b5'),
}


def filename(date):
    return f'sms-call-internet-mi-{date}.txt'


def hashes(path):
    md5, sha = hashlib.md5(), hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b''):
            md5.update(chunk)
            sha.update(chunk)
    return md5.hexdigest(), sha.hexdigest()


def signed_url(file_id, email):
    body = json.dumps({'guestbookResponse': {'email': email}}).encode()
    req = urllib.request.Request(API + str(file_id), data=body, method='POST', headers={'Content-Type': 'application/json', **UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.loads(r.read().decode())
    if out.get('status') != 'OK':
        raise RuntimeError(f'guestbook response refused: {out}')
    return out['data']['signedUrl']


def fetch(date, email):
    fid, size, md5_ref = FILES[date]
    dst = DEST / filename(date)
    if dst.exists():
        if dst.stat().st_size == size:
            md5, sha = hashes(dst)
            if md5 == md5_ref:
                return {'status': 'present, verified', 'md5': md5, 'sha256': sha}
        raise SystemExit(f'{dst} exists but does not verify; remove it by hand after inspection')
    part = dst.with_suffix('.part')
    url = signed_url(fid, email)
    t0 = time.time()
    with urllib.request.urlopen(urllib.request.Request(url, method='GET', headers=UA), timeout=600) as r, open(part, 'wb') as fh:
        while True:
            chunk = r.read(1 << 22)
            if not chunk:
                break
            fh.write(chunk)
    got = part.stat().st_size
    md5, sha = hashes(part)
    if got != size or md5 != md5_ref:
        part.rename(part.with_suffix('.failed'))
        raise SystemExit(f'{date}: size {got} (expected {size}) or MD5 {md5} (expected {md5_ref}) does not verify')
    part.rename(dst)
    dst.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return {'status': 'downloaded, verified', 'md5': md5, 'sha256': sha, 'seconds': round(time.time() - t0, 1)}


def main():
    email = os.environ.get('MILAN_GUESTBOOK_EMAIL')
    DEST.mkdir(parents=True, exist_ok=True)
    man_p = DEST / 'download_manifest.json'
    man = json.loads(man_p.read_text()) if man_p.exists() else {'doi': DOI, 'license': 'ODbL 1.0', 'files': {}}
    for date in sorted(FILES):
        need = not (DEST / filename(date)).exists()
        if need and not email:
            raise SystemExit('MILAN_GUESTBOOK_EMAIL is not set')
        rec = fetch(date, email)
        fid, size, md5_ref = FILES[date]
        man['files'][filename(date)] = {'date': date, 'dataverse_file_id': fid, 'size_bytes': size, 'md5_dataverse': md5_ref,
                                        'md5': rec['md5'], 'sha256': rec['sha256'], 'verified': rec['md5'] == md5_ref}
        man_p.write_text(json.dumps(man, indent=1))
        print(date, rec['status'], rec.get('seconds', ''), rec['sha256'], flush=True)
    print('all verified:', all(v['verified'] for v in man['files'].values()) and len(man['files']) == len(FILES))


if __name__ == '__main__':
    sys.exit(main())
