"""Versioned UTF-8 byte slices retaining the immutable parent chunk identity."""
import hashlib
from .models import CodeSearchDocument

SLICE_REVISION = 'utf8-slices-8192-v1'
MAX_SLICE_BYTES = 8192


def slice_documents(scope, source, symbol, language, *, max_bytes=MAX_SLICE_BYTES):
    if type(max_bytes) is not int or max_bytes < 4:
        raise ValueError('slice budget must fit a UTF-8 code point')
    if type(source['byte_start']) is not int or type(source['byte_end']) is not int or not 0 <= source['byte_start'] <= source['byte_end']:
        raise ValueError('invalid parent source range')
    raw = source['content'].encode('utf-8')
    if hashlib.sha256(raw).hexdigest() != source['content_hash'] or len(raw) != source['byte_end']-source['byte_start']:
        raise ValueError('source is not byte-preserving UTF-8')
    revision = SLICE_REVISION if max_bytes == MAX_SLICE_BYTES else f'utf8-slices-{max_bytes}-v1'
    documents = []
    start = 0
    while start < len(raw):
        end = min(start+max_bytes, len(raw))
        while end < len(raw) and raw[end] & 0xc0 == 0x80:
            end -= 1
        newline = raw.rfind(b'\n', start, end)
        if end < len(raw) and newline >= start:
            end = newline+1
        text = raw[start:end].decode('utf-8')
        payload = f"{source['path']}\n{symbol}\n{text}"
        documents.append(CodeSearchDocument(*scope.key, source['node_id'], source['chunk_id'],
            source['path'], symbol, language, hashlib.sha256(raw[start:end]).hexdigest(),
            hashlib.sha256(payload.encode()).hexdigest(), revision, text,
            source['byte_start']+start, source['byte_start']+end, source.get('parent_source_hash',source['content_hash'])))
        start = end
    return tuple(documents)
