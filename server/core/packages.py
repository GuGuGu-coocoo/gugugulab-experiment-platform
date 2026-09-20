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
# Complete native distribution programs are larger than a Web build: the real
# synthetic macOS arm64 export is ~62 MiB packed / ~175 MiB expanded. The bounds
# below are the ceiling a researcher may upload, not a target size.
MAX_NATIVE_ARCHIVE=256*1024*1024
MAX_NATIVE_EXPANDED=512*1024*1024
MAX_NATIVE_FILES=4096
MAX_COMPRESSION_RATIO=200
# An executable member with one of these suffixes is a script, not a program
# binary or framework: the platform never accepts script payloads in a native
# distribution program, even inside the bundle.
SCRIPT_SUFFIXES=('.sh','.command','.bash','.zsh','.py','.js','.mjs','.rb','.pl','.scpt','.applescript')

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
            require(item.file_size/max(item.compress_size,1)<=MAX_COMPRESSION_RATIO,'compression_ratio')
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


def _regular_native_member(item):
    """Reject absolute/traversal/colliding paths, links and non-regular members."""
    name=item.filename
    path=PurePosixPath(name)
    require(not path.is_absolute() and '..' not in path.parts and name and '\\' not in name and ':' not in name and '//' not in name,'unsafe_path')
    kind=stat.S_IFMT(item.external_attr>>16)
    if name.endswith('/'):
        require(kind in (0,stat.S_IFDIR),'non_regular_file')
        return path,True
    require(kind in (0,stat.S_IFREG),'non_regular_file')
    return path,False


def native_program_valid(raw,descriptor):
    """Validate one bounded macOS program archive against its registered descriptor.

    The archive holds exactly one ``*.app`` bundle and nothing else: traversal,
    absolute, colliding, linked, script and non-regular members are refused, the
    bundle must ship its executable, ``Info.plist`` and PCK resources, the
    declared dependency directory must not be empty, and the archive digest must
    equal the descriptor's program digest (which deliberately excludes the
    external connection configuration). Nothing is extracted, executed or
    fetched here; the caller stores the raw bytes unchanged.
    """
    require(isinstance(descriptor,dict) and descriptor.get('platform')=='macos_arm64','native_platform')
    expected=descriptor.get('program_sha256')
    require(isinstance(expected,str) and len(expected)==64 and all(c in '0123456789abcdef' for c in expected),'program_digest')
    require(len(raw)<=MAX_NATIVE_ARCHIVE,'archive_limit',413)
    digest=hashlib.sha256(raw).hexdigest()
    require(digest==expected,'program_digest_mismatch')
    try:
        archive=zipfile.ZipFile(io.BytesIO(raw))
        entries=archive.infolist()
        require(0<len(entries)<=MAX_NATIVE_FILES,'file_count')
        seen=set();roots=set();files=0;total=0;binary=False;plist=False;pck=False
        frameworks_declared=False;framework_files=0
        for item in entries:
            path,is_directory=_regular_native_member(item)
            require(item.filename.casefold() not in seen,'duplicate_path')
            seen.add(item.filename.casefold())
            if len(path.parts)>=2 and path.parts[0].endswith('.app') and path.parts[1:2]==('Contents',) and path.parts[2:3]==('Frameworks',):
                frameworks_declared=True
            if is_directory:
                continue
            require(len(path.parts)>=2 and path.parts[0].endswith('.app'),'reserved_path')
            roots.add(path.parts[0])
            require(len(roots)==1,'multiple_bundles')
            mode=(item.external_attr>>16)&0o7777
            require(not (mode&0o111 and path.suffix.lower() in SCRIPT_SUFFIXES),'unsafe_script')
            inner=path.parts[1:]
            if inner[:1]==('Contents',):
                if inner[1:2]==('MacOS',) and mode&0o111:
                    binary=True
                if path.name=='Info.plist':
                    plist=True
                if inner[1:2]==('Resources',) and path.suffix.lower()=='.pck':
                    pck=True
                if inner[1:2]==('Frameworks',):
                    framework_files+=1
            files+=1
            total+=item.file_size
            require(total<=MAX_NATIVE_EXPANDED and item.file_size<=MAX_NATIVE_EXPANDED,'expanded_limit',413)
            require(item.file_size/max(item.compress_size,1)<=MAX_COMPRESSION_RATIO,'compression_ratio')
        require(binary,'missing_binary');require(plist,'missing_info_plist');require(pck,'missing_pck')
        require(not frameworks_declared or framework_files>0,'missing_dependencies')
        # Read every byte for CRC and real bounds; directory metadata is never trusted alone.
        count=0
        for item in entries:
            if item.filename.endswith('/'):
                continue
            with archive.open(item) as stream:
                while chunk:=stream.read(65536):
                    count+=len(chunk)
                    require(count<=MAX_NATIVE_EXPANDED,'expanded_limit',413)
        return {'platform':'macos_arm64','app':next(iter(roots)),'members':files,'expanded':total,'digest':digest}
    except (zipfile.BadZipFile,KeyError,RuntimeError):
        from .protocol import Rejected
        raise Rejected('invalid_archive')
