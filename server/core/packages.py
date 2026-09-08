"""Validate bounded static packages without extracting or executing their code."""
import hashlib
import io
import json
import stat
import zipfile
from pathlib import PurePosixPath
from jsonschema import Draft202012Validator
from .protocol import require, parse

MAX_ARCHIVE=128*1024*1024
MAX_EXPANDED=256*1024*1024

def schema_safe(schema):
    require(isinstance(schema,dict),'schema_object')
    def walk(node):
        if isinstance(node,dict):
            require(not any(k in node for k in ('$ref','$dynamicRef','$id')), 'schema_references_unsupported')
            for value in node.values(): walk(value)
        elif isinstance(node,list):
            for value in node: walk(value)
    walk(schema)
    Draft202012Validator.check_schema(schema)

def descriptor_valid(d):
    require(isinstance(d,dict) and set(d)=={'version','platform','host_version','sdk_version','protocol_version','schemas','codebook','program_sha256'},'descriptor_fields')
    require(d['protocol_version']=='gep/1' and d['sdk_version']=='0.1.0' and d['host_version']=='4.7.2','incompatible_build')
    require(d['platform'] in ('godot_web','macos_arm64'),'unsupported_platform')
    require(isinstance(d['version'],str) and 0<len(d['version'])<=64,'build_version')
    require(isinstance(d['program_sha256'],str) and len(d['program_sha256'])==64 and all(c in '0123456789abcdef' for c in d['program_sha256']),'program_digest')
    require(isinstance(d['schemas'],dict) and 1<=len(d['schemas'])<=16,'schema_limit')
    for definition in d['schemas'].values():
        require(set(definition)=={'id','version','schema'},'schema_definition')
        schema_safe(definition['schema'])
    require(isinstance(d['codebook'],dict) and bool(d['codebook']),'codebook_required')

def validate_package(raw):
    require(len(raw)<=MAX_ARCHIVE,'archive_limit',413)
    try:
        archive=zipfile.ZipFile(io.BytesIO(raw))
        entries=archive.infolist()
        require(0<len(entries)<=256,'file_count')
        paths=set(); total=0
        for item in entries:
            path=PurePosixPath(item.filename)
            require(not path.is_absolute() and '..' not in path.parts and '\\' not in item.filename and ':' not in item.filename and not item.filename.endswith('/'),'unsafe_path')
            require(str(path)==item.filename and item.filename.casefold() not in paths,'duplicate_path')
            require(stat.S_IFMT(item.external_attr>>16) in (0,stat.S_IFREG),'non_regular_file')
            require(path.parts[0] in ('web','manifest.json'),'reserved_path')
            paths.add(item.filename.casefold())
            total+=item.file_size
            require(total<=MAX_EXPANDED and item.file_size<=MAX_EXPANDED,'expanded_limit',413)
            require(item.file_size/max(item.compress_size,1)<=200,'compression_ratio')
        manifest=parse(archive.read('manifest.json'))
        descriptor_valid(manifest)
        require(manifest['platform']=='godot_web' and 'web/index.html' in paths,'entry_missing')
        # Read every byte for CRC and actual bounds; validation does not trust directory metadata alone.
        count=0;program=hashlib.sha256()
        for item in sorted(entries,key=lambda e:e.filename):
            if item.filename!='manifest.json':program.update(item.filename.encode())
            with archive.open(item) as stream:
                while chunk:=stream.read(65536):
                    count+=len(chunk)
                    if item.filename!='manifest.json':program.update(chunk)
                    require(count<=MAX_EXPANDED,'expanded_limit',413)
        require(program.hexdigest()==manifest['program_sha256'],'program_digest_mismatch')
        return manifest,hashlib.sha256(raw).hexdigest()
    except (zipfile.BadZipFile,KeyError,RuntimeError):
        from .protocol import Rejected
        raise Rejected('invalid_archive')
