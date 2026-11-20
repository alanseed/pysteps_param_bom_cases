#!/usr/bin/env python3
"""Fetch native NCI Rainfields3 daily archives and extract rain-rate NetCDFs.

Python 3.10+, standard library only. Restart by rerunning the same command.
One process per data root; completed files are verified before reuse.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
import xml.etree.ElementTree as ET
import zipfile

BASE = 'https://thredds.nci.org.au/thredds'
PRODUCT = 'prcp-crate'
NS = {'t': 'http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0'}


def get(url):
    """Retry transient network failures; permanent HTTP errors remain visible."""
    for attempt in range(4):
        try:
            return urlopen(url, timeout=120)
        except (URLError, TimeoutError) as exc:
            if isinstance(exc, HTTPError) and exc.code not in (429, 500, 502, 503, 504):
                raise
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + '.part')
    part.write_text(json.dumps(obj, indent=2) + '\n')
    part.replace(path)


def download(url, path):
    """Stream to a temporary file; interrupted downloads restart at that file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + '.part')
    try:
        with get(url) as source, part.open('wb') as target:
            expected = source.headers.get('Content-Length')
            shutil.copyfileobj(source, target, length=1024 * 1024)
        if expected is not None and part.stat().st_size != int(expected):
            raise ValueError(f'Truncated response: {url}')
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)


def extract(archive, destination, radar, day):
    """CRC-check ZIP; accept only flat, expected NetCDF names and _file_list."""
    pattern = re.compile(rf'{radar}_{day:%Y%m%d}_(\d{{6}})\.prcp-crate\.nc')
    records = []
    stamps = []
    with zipfile.ZipFile(archive) as z:
        members = z.infolist()
        names = [m.filename for m in members]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate ZIP member names')
        for m in members:
            if m.filename == '_file_list':
                continue
            match = pattern.fullmatch(m.filename)
            if not match:
                raise ValueError(f'Unexpected archive member: {m.filename}')
            stamps.append(datetime.strptime(f'{day:%Y%m%d}{match[1]}', '%Y%m%d%H%M%S'))
        if not stamps:
            raise ValueError('Archive contains no rainfields')
        bad = z.testzip()
        if bad:
            raise ValueError(f'ZIP CRC failure: {bad}')
        destination.mkdir(parents=True, exist_ok=True)
        for m in members:
            if m.filename == '_file_list':
                continue
            path = destination / m.filename
            part = path.with_suffix('.nc.part')
            try:
                with z.open(m) as source, part.open('wb') as target:
                    magic = source.read(8)
                    if not (magic.startswith(b'CDF') or magic == b'\x89HDF\r\n\x1a\n'):
                        raise ValueError(f'Not a NetCDF container: {m.filename}')
                    target.write(magic)
                    shutil.copyfileobj(source, target)
                part.replace(path)
            finally:
                part.unlink(missing_ok=True)
            records.append({'name': path.name, 'bytes': path.stat().st_size, 'sha256': digest(path)})
    expected = {datetime.combine(day, datetime.min.time()) + timedelta(minutes=5*i) for i in range(288)}
    actual = set(stamps)
    return {'files': sorted(records, key=lambda r: r['name']),
            'missing_5min_utc': [s.isoformat() + 'Z' for s in sorted(expected - actual)],
            'off_grid_utc': [s.isoformat() + 'Z' for s in sorted(actual - expected)]}


def verified(record, archive, destination):
    if not record or not archive.is_file() or digest(archive) != record['archive_sha256']:
        return False
    return bool(record['files']) and all(
        (destination / f['name']).is_file() and digest(destination / f['name']) == f['sha256']
        for f in record['files'])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--year', type=int, required=True)
    p.add_argument('--radar', type=int, default=71)
    p.add_argument('--start', type=date.fromisoformat, help='First UTC day, inclusive')
    p.add_argument('--end', type=date.fromisoformat, help='Last UTC day, inclusive')
    p.add_argument('--data-root', type=Path, default=Path('~/data/alan/psteps_advect_data'))
    p.add_argument('--dry-run', action='store_true', help='Catalogue inventory only; no local writes')
    args = p.parse_args()
    start, end = args.start or date(args.year, 1, 1), args.end or date(args.year, 12, 31)
    if start.year != args.year or end.year != args.year or start > end or args.radar < 1:
        p.error('Use a positive radar ID and ordered dates within --year')
    root = args.data_root.expanduser().resolve()
    prefix = f'rq0/rainfields3/{args.radar}/{args.year}/{PRODUCT}'
    catalogue = f'{BASE}/catalog/{prefix}/catalog.xml'
    with get(catalogue) as response:
        xml = response.read()
    tree = ET.fromstring(xml)
    available = {}
    for d in tree.findall('.//t:dataset', NS):
        path = d.get('urlPath', '')
        match = re.fullmatch(rf'{re.escape(prefix)}/{args.radar}_(\d{{8}})\.prcp-crate\.zip', path)
        if match:
            available[datetime.strptime(match[1], '%Y%m%d').date()] = f'{BASE}/fileServer/{path}'
    days = [start + timedelta(days=i) for i in range((end-start).days+1)]
    missing = [d.isoformat() for d in days if d not in available]
    print(f'{len(days)-len(missing)}/{len(days)} daily archives catalogued; absent days: {missing}', flush=True)
    if args.dry_run:
        return 1 if missing else 0
    provenance = root / 'manifests' / 'nci_rainfields3' / str(args.radar) / str(args.year)
    provenance.mkdir(parents=True, exist_ok=True)
    (provenance / 'catalog.xml').write_bytes(xml)
    save_json(provenance / f'inventory_{start}_{end}.json', {
        'catalogue': catalogue, 'retrieved_utc': datetime.now(timezone.utc).isoformat(),
        'start': str(start), 'end': str(end), 'absent_days': missing,
        'archives': {str(d): available[d] for d in days if d in available}})
    # Keep source licence and documentation alongside the acquired data.
    for name in ['rq0_rainfields3_readme.pdf', 'rq0-rainfields3-license-cc4-by-nc.pdf',
                 'RAINFIELDS3_Support_Guide_v1.0.pdf']:
        path = root / 'metadata' / 'nci_rainfields3' / name
        if not path.exists():
            download(f'{BASE}/fileServer/rq0/rainfields3/{name}', path)
    failures = bool(missing)
    for day in days:
        if day not in available:
            continue
        archive = root / 'raw' / 'nci_rainfields3' / str(args.radar) / str(args.year) / PRODUCT / f'{args.radar}_{day:%Y%m%d}.{PRODUCT}.zip'
        destination = root / 'rainfields' / 'nci_rainfields3' / str(args.radar) / str(args.year) / f'{day:%m}' / f'{day:%d}'
        manifest = provenance / f'{day}.json'
        try:
            record = json.loads(manifest.read_text()) if manifest.exists() else None
            if verified(record, archive, destination):
                print(f'{day}: verified, reused', flush=True)
                failures |= bool(record['missing_5min_utc'] or record['off_grid_utc'])
                continue
            # An interrupted extraction can reuse a CRC-valid archive.
            if archive.exists():
                try:
                    with zipfile.ZipFile(archive) as z:
                        valid = z.testzip() is None
                except zipfile.BadZipFile:
                    valid = False
                if not valid:
                    raise ValueError(f'Corrupt existing archive; move aside before retry: {archive}')
                if record and digest(archive) != record['archive_sha256']:
                    raise ValueError(f'Archive differs from recorded checksum: {archive}')
            else:
                download(available[day], archive)
            result = extract(archive, destination, args.radar, day)
            result.update(url=available[day], archive_sha256=digest(archive),
                          archive_bytes=archive.stat().st_size,
                          retrieved_utc=datetime.now(timezone.utc).isoformat(),
                          validation='ZIP CRC, NetCDF signature, filename timestamps; scientific metadata not validated')
            save_json(manifest, result)
            gaps = len(result['missing_5min_utc'])
            failures |= bool(gaps or result['off_grid_utc'])
            print(f'{day}: {len(result["files"])} fields, {gaps} missing five-minute slots', flush=True)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            failures = True
            print(f'{day}: FAILED: {exc}', file=sys.stderr, flush=True)
    return int(failures)


if __name__ == '__main__':
    sys.exit(main())
