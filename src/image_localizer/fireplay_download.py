"""Bounded public-image downloads; initialization never implies visual review."""
from __future__ import annotations

import http.client
import ipaddress
import os
from pathlib import Path
import socket
import ssl
import tempfile
import time
from urllib import error, parse, request
import warnings

from PIL import Image

from . import fireplay
from .contracts import ContractError, require
from .jobs import initialize_job
from .pipeline import sha256, write_json

MAX_BYTES = 30 * 1024 * 1024
MAX_PIXELS = 50_000_000
TIMEOUT = 15
TOTAL_TIMEOUT = 120
MAX_REDIRECTS = 5
FORMATS = {'PNG': 'png', 'JPEG': 'jpg', 'WEBP': 'webp'}
REDIRECTS = {301, 302, 303, 307, 308}


def _public_addresses(url: str) -> list[str]:
    """Validate without reflecting bearer query parameters into diagnostics."""
    require(isinstance(url, str) and not any(ord(c) <= 32 or ord(c) == 127 for c in url),
            'Source URL contains whitespace or control characters')
    try:
        parts = parse.urlsplit(url)
        require(parts.scheme in ('http', 'https') and bool(parts.hostname), 'Source requires public HTTP(S)')
        require(parts.username is None and parts.password is None and not parts.fragment and '#' not in url,
                'Source URL must not contain userinfo or a fragment')
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        addresses = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(item[4][0] for item in addresses))
        require(bool(ips), 'Source hostname has no addresses')
        for value in ips:
            ip = ipaddress.ip_address(value)
            require(ip.is_global and not ip.is_multicast and not ip.is_reserved and
                    not ip.is_loopback and not ip.is_link_local and not ip.is_private and
                    not ip.is_unspecified and '%' not in value,
                    'Source hostname must resolve only to public addresses')
        return ips
    except ContractError:
        raise
    except (ValueError, OSError, UnicodeError):
        raise ContractError('Source URL or hostname is invalid or unavailable') from None


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_url(url: str, addresses: list[str]):
    """Private network seam: pin validated IP while retaining TLS SNI and Host."""
    def connection(host, **kwargs):
        parts = parse.urlsplit(url)
        port = parts.port or (443 if parts.scheme == 'https' else 80)
        cls = http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection
        conn = cls(host, **kwargs)

        def connect():
            conn.sock = socket.create_connection((addresses[0], port), TIMEOUT)
            if parts.scheme == 'https':
                try:
                    conn.sock = ssl.create_default_context().wrap_socket(conn.sock, server_hostname=parts.hostname)
                except BaseException:
                    conn.sock.close()
                    raise
        conn.connect = connect
        return conn

    class HTTP(request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(connection, req)

    class HTTPS(request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(connection, req)

    opener = request.build_opener(request.ProxyHandler({}), HTTP(), HTTPS(), _NoRedirect())
    req = request.Request(url, headers={'User-Agent': 'image-localizer/0.2', 'Accept-Encoding': 'identity'})
    try:
        return opener.open(req, timeout=TIMEOUT)
    except error.HTTPError as response:
        if response.code in REDIRECTS:
            return response
        response.close()
        raise ContractError('Image server returned an unsuccessful HTTP status') from None


def _inspect(path: Path) -> dict:
    require(path.stat().st_size <= MAX_BYTES, 'Source exceeds 30 MiB byte limit')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path) as image:
                require(image.format in FORMATS, 'Source must be a still PNG, JPEG or WebP image')
                require(image.width * image.height <= MAX_PIXELS, 'Source exceeds pixel limit')
                require(not getattr(image, 'is_animated', False) and getattr(image, 'n_frames', 1) == 1,
                        'Animated images are unsupported')
                result = {'width': image.width, 'height': image.height, 'format': image.format}
                image.verify()
            with Image.open(path) as image:
                image.load()
    except ContractError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ContractError('Source is invalid, corrupt, or exceeds image safety limits') from None
    return {**result, 'sha256': sha256(path), 'bytes': path.stat().st_size}


def _matches(actual: dict, expected: dict) -> None:
    for field in ('sha256', 'bytes', 'width', 'height'):
        if expected.get(field) is not None:
            require(actual[field] == expected[field], f'Source {field} mismatch')


def _source(record: dict, index: int) -> dict:
    require(record.get('state') == 'accepted', 'Fireplay request must be accepted before downloading')
    require(type(index) is int and index > 0, 'Source index must be a positive integer')
    matches = [source for source in record['sources'] if source['index'] == index]
    require(len(matches) == 1, 'Source index is not present or is ambiguous')
    return matches[0]


def download_source(code: str, index: int, root: Path) -> dict:
    """Download one accepted source and bind verified bytes to its content version."""
    root = root.resolve()
    record = fireplay.load_record(code, root)
    source = _source(record, index)
    folder = (fireplay.request_folder(code, root) / 'sources').resolve()
    require(folder.is_relative_to(root), 'Source directory escapes project root')
    existing = record.get('downloads', {}).get(str(index))
    if existing:
        path = Path(existing['local_path'])
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        require(path.is_relative_to(folder) and path.is_file(), 'Recorded source is missing or escapes sources directory')
        require(existing.get('content_version') == record['content_version'], 'Recorded source content version mismatch')
        actual = _inspect(path)
        _matches(actual, existing)
        _matches(actual, source)
        require(actual['format'] == existing.get('format'), 'Recorded source format mismatch')
        return existing

    folder.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f'.source-{index}-', suffix='.tmp', dir=folder, delete=False) as stream:
            temp_path = Path(stream.name)
            url = source['url']
            started = time.monotonic()
            for hop in range(MAX_REDIRECTS + 1):
                addresses = _public_addresses(url)
                require(time.monotonic() - started < TOTAL_TIMEOUT, 'Image download timed out')
                with _open_url(url, addresses) as response:
                    status = response.status if hasattr(response, 'status') else response.code
                    if status in REDIRECTS:
                        require(hop < MAX_REDIRECTS, 'Image download exceeded redirect limit')
                        location = response.headers.get('Location')
                        require(bool(location), 'Image redirect lacks a destination')
                        url = parse.urljoin(url, location)
                        continue
                    require(status == 200, 'Image server returned an unsuccessful HTTP status')
                    length = response.headers.get('Content-Length')
                    if length is not None:
                        require(length.isascii() and length.isdecimal(), 'Invalid image Content-Length')
                        require(int(length) <= MAX_BYTES, 'Source exceeds 30 MiB byte limit')
                    require(response.headers.get('Content-Encoding', 'identity').lower() == 'identity',
                            'Encoded image responses are unsupported')
                    size = 0
                    while True:
                        require(time.monotonic() - started < TOTAL_TIMEOUT, 'Image download timed out')
                        chunk = response.read(min(64 * 1024, MAX_BYTES - size + 1))
                        if not chunk:
                            break
                        size += len(chunk)
                        require(size <= MAX_BYTES, 'Source exceeds 30 MiB byte limit')
                        stream.write(chunk)
                    if length is not None:
                        require(size == int(length), 'Image Content-Length mismatch')
                    break
        actual = _inspect(temp_path)
        _matches(actual, source)
        destination = folder / f"source-{index}-{actual['sha256']}.{FORMATS[actual['format']]}"
        require(destination.resolve().is_relative_to(folder), 'Source file escapes sources directory')
        # Atomic publication fails if a previous download already owns this name.
        try:
            os.link(temp_path, destination)
        except FileExistsError:
            require(_inspect(destination) == actual, 'Existing source file has changed')
        entry = {**actual, 'local_path': str(destination), 'content_version': record['content_version']}
        fireplay.record_download(code, index, entry, root)
        return entry
    except ContractError:
        raise
    except (OSError, ValueError, error.URLError, http.client.HTTPException):
        raise ContractError('Image download failed; verify public source availability') from None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def initialize_source_job(code: str, index: int, output: Path, root: Path) -> dict:
    """Create an unreviewed local multi-target job with origin metadata."""
    require(not output.exists(), 'Job directory already exists')
    record = fireplay.load_record(code, root)
    _source(record, index)
    entry = download_source(code, index, root)
    current = fireplay.load_record(code, root)
    require(current['content_version'] == record['content_version'] == entry['content_version']
            and current['state'] == 'accepted', 'Requirement changed during job initialization; refresh intake')
    result = initialize_job(Path(entry['local_path']), record['targets'], output)
    provenance = {'schema_version': 1, 'code': code, 'source_index': index,
                  'content_version': record['content_version'], 'source': entry,
                  'notice': 'Origin provenance only. Recognition, translation and visual review remain pending.'}
    write_json(output / 'fireplay-source.json', provenance)
    return {**result, 'fireplay_source': str((output / 'fireplay-source.json').resolve())}
