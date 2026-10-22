"""The pending queue is the only deployment input; final trees are archives."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re

NAME = re.compile(r'[A-Za-z0-9_.=-]+\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')
VARIANT = re.compile(r'(.+)_v\d{1,3}\Z')
PUBLIC_SKIN_NAME = re.compile(r'(0|[1-9][0-9]*)_(male|female)_(white|black|asian|hispanic)(?:_v([1-9][0-9]{0,2}))?\Z')
PRIVATE_SKIN_NAME = re.compile(r'([1-9][0-9]*)_([1-3])_(0|[1-9][0-9]*)_(male|female)_(white|black|asian|hispanic)\Z')
MAX_BATCH_BYTES = 256 * 1024 * 1024
# Public skins carry skintags.inc gender/race bits in the wire slot column.
GENDERS = {'female': 0, 'male': 1}
RACES = {'white': 0, 'black': 2, 'asian': 4, 'hispanic': 6}
PENDING = [('pending_skins', 'skin'), ('pending_graffiti', 'graffiti'), ('pending_public_skins', 'public')]


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

    @property
    def sidecar(self):
        """Pending metadata path; public skins key metadata by texture, others by model."""
        source = self.source_txd if self.kind == 'public' else self.source_dff
        return str(Path(source).with_suffix('.model.json')).replace('\\', '/')

    @property
    def final_sidecar(self):
        final = self.final_txd if self.kind == 'public' else self.final_dff
        return str(Path(final).with_suffix('.model.json')).replace('\\', '/')


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


def _owner(relative):
    owner = relative.parts[1]
    if not owner.isascii() or not owner.isdecimal() or str(int(owner)) != owner:
        raise ValueError('owner folder must be a canonical positive DBID')
    return _integer(int(owner), 1, 2147483647, 'owner DBID')


def _payload(root, source, hashes):
    if source.is_symlink() or not source.is_file():
        raise ValueError('missing pair or symbolic link')
    data = source.read_bytes()
    if not data or data.startswith(b'version https://git-lfs.github.com/spec/'):
        raise ValueError('empty file or Git LFS pointer')
    if len(data) > 100 * 1024 * 1024:
        raise ValueError('model exceeds 100 MiB upload limit')
    hashes[source.relative_to(root).as_posix()] = digest(data)
    return data


def skin_filename(stem, kind, owner):
    """Numeric skin names carry all operator-supplied metadata."""
    pattern = PUBLIC_SKIN_NAME if kind == 'public' else PRIVATE_SKIN_NAME
    match = pattern.fullmatch(stem)
    if not match:
        if stem and stem[0].isdigit():
            raise ValueError('invalid skin filename metadata')
        return None  # Retain identities of the existing queued collection.
    if kind == 'public':
        base, gender, race, _ = match.groups()
        slot = GENDERS[gender] | RACES[race]
    else:
        character, slot, base, gender, race = match.groups()
        if int(character) != owner:
            raise ValueError('filename character DBID must match owner folder')
        slot = int(slot)
    base = _integer(int(base), 0, 311, 'skin base')
    if base == 74:
        raise ValueError('invalid vanilla skin base 74')
    return dict(base_model=base, slot=slot, gender=gender, race=race)


def public_dff_name(txd_name):
    """A public texture shares <ped>.dff when named <ped>_v<N>.txd."""
    stem = txd_name[:-4]
    if PUBLIC_SKIN_NAME.fullmatch(stem):
        return stem + '.dff'
    match = VARIANT.fullmatch(stem)
    return (match.group(1) if match else stem) + '.dff'


def _public_item(root, path):
    relative = path.relative_to(root)
    collection = _owner(relative)
    filename = relative.parts[2]
    if not NAME.fullmatch(filename) or '..' in filename or path.suffix != '.txd':
        raise ValueError('invalid filename')
    dff = path.with_name(public_dff_name(filename))
    sidecar = path.with_suffix('.model.json')
    # On Windows exists() is case-insensitive; compare names too.
    if dff.name not in {p.name for p in path.parent.iterdir()}:
        raise ValueError('missing exact-case shared model filename')
    hashes = {}
    dff_data = _payload(root, dff, hashes)
    txd_data = _payload(root, path, hashes)
    metadata = skin_filename(path.stem, 'public', collection)
    if metadata is not None:
        if sidecar.exists():
            raise ValueError('filename skin metadata must not have a JSON sidecar')
    else:
        if sidecar.is_symlink() or not sidecar.is_file() or sidecar.stat().st_size > 4096:
            raise ValueError('legacy public skins need a <texture>.model.json sidecar')
        raw = sidecar.read_bytes()
        metadata = json.loads(raw)
        if not isinstance(metadata, dict) or metadata.keys() - {'base_model', 'gender', 'race'}:
            raise ValueError('invalid metadata fields')
        hashes[sidecar.relative_to(root).as_posix()] = digest(raw)
    base = _integer(metadata.get('base_model'), 0, 311, 'skin base')
    if base == 74:
        raise ValueError('invalid vanilla skin base 74')
    if metadata.get('gender') not in GENDERS or metadata.get('race') not in RACES:
        raise ValueError('gender must be male/female and race white/black/asian/hispanic')
    final = f'custom_skins/public_skins/{collection}/'
    if len(final + filename) > 63 or len(final + dff.name) > 63:
        raise ValueError('final model path exceeds 63 characters')
    fields = dict(kind='public', owner_dbid=collection, base_model=base,
                  slot=GENDERS[metadata['gender']] | RACES[metadata['race']],
                  source_dff=dff.relative_to(root).as_posix(), source_txd=relative.as_posix(),
                  final_dff=final + dff.name, final_txd=final + filename,
                  dff_bytes=len(dff_data), txd_bytes=len(txd_data),
                  dff_sha256=digest(dff_data), txd_sha256=digest(txd_data), source_hashes=hashes)
    return ModelItem(item_id=digest(canonical(fields)), **fields)


def _item(root, path, kind):
    relative = path.relative_to(root)
    owner_id = _owner(relative)
    filename = relative.parts[2]
    if not NAME.fullmatch(filename) or '..' in filename or path.suffix != '.dff':
        raise ValueError('invalid filename')
    stem = filename[:-4]
    txd = path.with_suffix('.txd')
    sidecar = path.with_suffix('.model.json')
    hashes = {}
    payloads = [_payload(root, source, hashes) for source in (path, txd)]
    filename_metadata = skin_filename(stem, kind, owner_id) if kind == 'skin' else None
    metadata = {}
    if filename_metadata is not None and sidecar.exists():
        raise ValueError('filename skin metadata must not have a JSON sidecar')
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
    values = filename_metadata if filename_metadata is not None else {**tokens, **metadata}
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
    for folder, kind in PENDING:
        directory = root / folder
        if not directory.exists():
            continue
        if directory.is_symlink():
            errors.append(f'{folder}: symbolic link root')
            continue
        # Public items are keyed by texture; their model may be shared.
        key = '.txd' if kind == 'public' else '.dff'
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
                if path.suffix != key:
                    continue
                if kind == 'public':
                    item = _public_item(root, path)
                else:
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
            if path.is_file() and path.name != '.gitkeep' and path.suffix != key and rel not in consumed:
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
