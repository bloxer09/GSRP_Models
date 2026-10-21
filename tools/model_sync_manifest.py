"""The pending queue is the only deployment input; final trees are archives."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re

NAME = re.compile(r'[A-Za-z0-9_.=-]+\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')
MAX_BATCH_BYTES = 256 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ModelItem:
    item_id: str
    kind: str
    owner_dbid: int
    base_model: int
    slot: int
    source_dff: str
    source_txd: str
    final_dff: str
    final_txd: str
    dff_bytes: int
    txd_bytes: int
    dff_sha256: str
    txd_sha256: str
    source_hashes: dict


@dataclass(frozen=True)
class BatchManifest:
    batch_id: str
    items: list[ModelItem]
    version: int = 1

    def to_dict(self):
        return asdict(self)


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'invalid {label}')
    return value


def _item(root, path, kind):
    relative = path.relative_to(root)
    _, owner, filename = relative.parts
    if not owner.isascii() or not owner.isdecimal() or str(int(owner)) != owner:
        raise ValueError('owner folder must be a canonical positive DBID')
    owner_id = _integer(int(owner), 1, 2147483647, 'owner DBID')
    if not NAME.fullmatch(filename) or '..' in filename or path.suffix != '.dff':
        raise ValueError('invalid filename')
    stem = filename[:-4]
    txd = path.with_suffix('.txd')
    sidecar = path.with_suffix('.model.json')
    hashes = {}
    payloads = []
    for source in (path, txd):
        if source.is_symlink() or not source.is_file():
            raise ValueError('missing pair or symbolic link')
        data = source.read_bytes()
        if not data or data.startswith(b'version https://git-lfs.github.com/spec/'):
            raise ValueError('empty file or Git LFS pointer')
        if len(data) > 100 * 1024 * 1024:
            raise ValueError('model exceeds 100 MiB upload limit')
        hashes[source.relative_to(root).as_posix()] = digest(data)
        payloads.append(data)
    metadata = {}
    if sidecar.exists():
        if sidecar.is_symlink() or sidecar.stat().st_size > 4096:
            raise ValueError('invalid metadata file')
        raw = sidecar.read_bytes()
        metadata = json.loads(raw)
        if not isinstance(metadata, dict) or metadata.keys() - {'base_model', 'slot', 'name'}:
            raise ValueError('invalid metadata fields')
        hashes[sidecar.relative_to(root).as_posix()] = digest(raw)
    tokens = {}
    for key, pattern in [('base_model', r'(?:^|_)base(?:id)?=(\d+)(?=_|$)'), ('slot', r'(?:^|_)slot=(\d+)(?=_|$)')]:
        matches = re.findall(pattern, stem)
        if len(matches) > 1:
            raise ValueError('duplicate filename metadata')
        if matches:
            tokens[key] = int(matches[0])
    for key, value in tokens.items():
        if key in metadata and metadata[key] != value:
            raise ValueError('conflicting filename/sidecar metadata')
    values = {**tokens, **metadata}
    if kind == 'skin':
        base = _integer(values.get('base_model'), 0, 311, 'skin base')
        if base == 74:
            raise ValueError('invalid vanilla skin base 74')
        slot = _integer(values.get('slot'), 1, 3, 'skin slot')
        final = f'custom_skins/private_skins/{owner_id}/{stem}'
    else:
        base = _integer(values.get('base_model', 19477), 1, 19999, 'graffiti base')
        slot = 0
        final = f'custom_graffiti/{owner_id}/{stem}'
    if len(final + '.dff') > 63:
        raise ValueError('final model path exceeds 63 characters')
    fields = dict(kind=kind, owner_dbid=owner_id, base_model=base, slot=slot,
                  source_dff=relative.as_posix(), source_txd=txd.relative_to(root).as_posix(),
                  final_dff=final + '.dff', final_txd=final + '.txd',
                  dff_bytes=len(payloads[0]), txd_bytes=len(payloads[1]),
                  dff_sha256=digest(payloads[0]), txd_sha256=digest(payloads[1]), source_hashes=hashes)
    return ModelItem(item_id=digest(canonical(fields)), **fields)


def discover_pending(root):
    root = Path(root)
    items, errors = [], []
    for folder, kind in [('pending_skins', 'skin'), ('pending_graffiti', 'graffiti')]:
        directory = root / folder
        if not directory.exists():
            continue
        if directory.is_symlink():
            errors.append(f'{folder}: symbolic link root')
            continue
        files = sorted(directory.rglob('*'))
        folded = {}
        for path in files:
            folded.setdefault(path.relative_to(root).as_posix().casefold(), []).append(path)
        consumed = set()
        for path in files:
            rel = path.relative_to(root).as_posix()
            if path.is_dir() and not path.is_symlink():
                continue
            if path.name == '.gitkeep':
                continue
            try:
                if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and root in p.parents):
                    raise ValueError('symbolic link input')
                if len(path.relative_to(root).parts) != 3:
                    raise ValueError('expected pending/<DBID>/<filename>')
                if len(folded[rel.casefold()]) != 1 or path.suffix not in ('.dff', '.txd', '.json'):
                    raise ValueError('invalid extension or case collision')
                if path.suffix != '.dff':
                    continue
                # On Windows exists() is case-insensitive; compare names too.
                siblings = {p.name for p in path.parent.iterdir()}
                if path.with_suffix('.txd').name not in siblings:
                    raise ValueError('missing exact-case texture filename')
                item = _item(root, path, kind)
                items.append(item)
                consumed.update(item.source_hashes)
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                errors.append(f'{rel}: {exc}')
        for path in files:
            rel = path.relative_to(root).as_posix()
            if path.is_file() and path.name != '.gitkeep' and path.suffix != '.dff' and rel not in consumed:
                errors.append(f'{rel}: unpaired or invalid input')
    return sorted(items, key=lambda item: item.item_id), sorted(set(errors))


def build_batches(items):
    ordered = sorted(items, key=lambda item: item.item_id)
    groups, group, size = [], [], 0
    for item in ordered:
        length = item.dff_bytes + item.txd_bytes
        if length > MAX_BATCH_BYTES:
            raise ValueError('Item exceeds batch byte limit')
        if group and (len(group) == 32 or size + length > MAX_BATCH_BYTES):
            groups.append(group); group, size = [], 0
        group.append(item); size += length
    if group:
        groups.append(group)
    return [BatchManifest(digest(canonical([asdict(i) for i in group])), group) for group in groups]


def serialize_wire(batch):
    lines = [f'MODELSYNC\t1\t{batch.batch_id}\t{len(batch.items)}']
    for item in batch.items:
        fields = [item.item_id, item.kind, item.owner_dbid, item.base_model, item.slot,
                  Path(item.source_dff).name, Path(item.source_txd).name,
                  item.dff_bytes, item.txd_bytes, item.dff_sha256, item.txd_sha256]
        line = '\t'.join(map(str, fields))
        if len(line.encode('ascii')) >= 512:
            raise ValueError('wire row exceeds protocol bound')
        lines.append(line)
    return ('\n'.join(lines) + '\n').encode('ascii')
