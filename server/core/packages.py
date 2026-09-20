"""Validate bounded static packages without extracting or executing their code."""
import hashlib
import io
import json
import re
import stat
import struct
import unicodedata
import zipfile
from pathlib import PurePosixPath
from jsonschema import Draft202012Validator
from .protocol import require, parse, Rejected

MAX_ARCHIVE=128*1024*1024
MAX_EXPANDED=256*1024*1024
# Complete native distribution programs are larger than a Web build: the real
# synthetic macOS arm64 export is ~62 MiB packed / ~175 MiB expanded and the real
# Windows x64 one is ~109 MiB packed. The bounds below are the ceiling a
# researcher may upload, not a target size.
MAX_NATIVE_ARCHIVE=256*1024*1024
MAX_NATIVE_EXPANDED=512*1024*1024
MAX_NATIVE_FILES=4096
MAX_COMPRESSION_RATIO=200
# The native platforms with an explicit, validated program contract. An unknown
# platform fails closed and never reaches a Web or a legacy branch.
NATIVE_PLATFORMS=('macos_arm64','windows_x64')
# A native descriptor may carry immutable package metadata: the program root
# directory, the entry file, the native dependencies the program needs at
# runtime and the GDExtension manifest that declares them. Required for
# windows_x64; optional for macos_arm64, whose bundle layout is fixed by the
# platform. Declaring the root keeps the freeze deterministic: the artifact
# config member and the frozen entry path follow from the descriptor alone.
PACKAGE_FIELDS=('root','entry','dependencies','extension')
DESCRIPTOR_FIELDS=frozenset({
    'version','platform','host_version','sdk_version','protocol_version','schemas','codebook','program_sha256'})
_MAX_DEPENDENCIES=8
HOST_VERSION=(4,7,2)
# The complete-artifact freeze generates these members for every native release:
# the public configuration, the schema documents, the codebook, the project
# license and the third-party notices. The artifact assembler owns the actual
# bytes; these names are the shared contract so a program archive can be refused
# before the freeze would ever replace program bytes with a generated member.
CONFIG_MEMBER='connection.json'
SCHEMA_DIR='schemas'
FROZEN_METADATA_MEMBERS=('LICENSE','THIRD_PARTY_NOTICES.txt','metadata/codebook.json')
# NTFS refuses these characters in a file name regardless of the extension.
_WINDOWS_ILLEGAL_CHARACTERS=frozenset('<>"|?*')
# An executable member with one of these suffixes is a script, not a program
# binary or framework: the platform never accepts script payloads in a native
# distribution program, even inside the bundle. The Windows entries cover the
# script hosts Windows would run from a double click.
SCRIPT_SUFFIXES=('.sh','.command','.bash','.zsh','.py','.js','.mjs','.rb','.pl','.scpt','.applescript',
                 '.bat','.cmd','.ps1','.psm1','.vbs','.vbe','.wsf','.wsh','.hta','.scr','.pif')
# Windows resolves these names as devices no matter the extension or directory.
WINDOWS_RESERVED_NAMES=frozenset(
    ['con','prn','aux','nul','clock$']
    +[f'com{d}' for d in '123456789']+[f'lpt{d}' for d in '123456789']
    +[f'com{d}' for d in '\u00b9\u00b2\u00b3']+[f'lpt{d}' for d in '\u00b9\u00b2\u00b3'])
# Executable images Windows would run without an explicit declaration. Only the
# declared entry and the declared dependencies may be executable images.
WINDOWS_IMAGE_SUFFIXES=('.exe','.dll','.sys','.com','.scr','.ocx','.cpl','.drv','.efi','.pif','.msi')
WINDOWS_PE_MACHINE=0x8664
WINDOWS_PE_MAGIC=0x20b
MAX_PATH_COMPONENT=255
MAX_MEMBER_PATH=1024
_HEADER_BYTES=4096


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


def _raw_components(value,code,windows):
    """Validate the raw path string before any normalization can hide a component.

    ``PurePosixPath`` silently collapses ``.`` and empty components, so every
    rule is applied to the raw ``/``-split first: empty, ``.`` and ``..``
    components, absolute/trailing separators, drive/UNC shapes and backslashes
    are refused for both platforms. The Windows rules additionally refuse the
    characters NTFS rejects, trailing dots/spaces, control characters and
    reserved device names. Declared paths and ZIP members share this function,
    so a declaration can never name a file the archive rules would refuse.
    """
    require(isinstance(value,str) and 0<len(value)<=MAX_MEMBER_PATH,code)
    require('\\' not in value and ':' not in value and not value.startswith('/')
            and not value.endswith('/') and '//' not in value,code)
    parts=value.split('/')
    require(all(part not in ('','.','..') for part in parts),code)
    require(all(len(part)<=MAX_PATH_COMPONENT for part in parts),code)
    if windows:
        for part in parts:
            require(not (_WINDOWS_ILLEGAL_CHARACTERS & set(part)),code)
            require(part==part.rstrip(' .'),code)
            require(not any(ord(character)<32 or ord(character)==127 for character in part),code)
            require(part.split('.')[0].casefold() not in WINDOWS_RESERVED_NAMES,code)
    return parts


def _package_path(value,platform=None):
    """A bounded, relative, POSIX-shaped path inside one program package."""
    require(isinstance(value,str) and 0<len(value)<=MAX_MEMBER_PATH,'package_metadata')
    _raw_components(value,'package_metadata',platform=='windows_x64')
    return value


def config_member_relative(entry):
    """The frozen public configuration path relative to the program root."""
    parent=PurePosixPath(entry).parent
    return CONFIG_MEMBER if str(parent) in ('','.') else str(parent/CONFIG_MEMBER)


def config_member_path(descriptor):
    """Where the freeze stores the public configuration, or ``None`` if undecidable.

    A macOS bundle is a directory next to the configuration; the Windows program
    root directory contains the executable, so its configuration lives inside
    that directory, exactly where the program looks for it. The value follows
    from the registered descriptor alone.
    """
    if not isinstance(descriptor,dict):
        return None
    if descriptor.get('platform')=='windows_x64':
        package=descriptor.get('package')
        if not isinstance(package,dict):
            return None
        root=package.get('root')
        entry=package.get('entry')
        if not (isinstance(root,str) and root and isinstance(entry,str) and entry):
            return None
        return f'{root}/{config_member_relative(entry)}'
    return CONFIG_MEMBER


def frozen_member_names(descriptor):
    """The member names the complete-artifact freeze generates for one descriptor."""
    names=set(FROZEN_METADATA_MEMBERS)
    schemas=descriptor.get('schemas') if isinstance(descriptor,dict) else None
    if isinstance(schemas,dict):
        names.update(f'{SCHEMA_DIR}/{key}.json' for key in schemas if isinstance(key,str) and key)
    config=config_member_path(descriptor)
    if config is not None:
        names.add(config)
    return names


def frozen_conflict(member_names,descriptor):
    """The program members that collide with a generated freeze member.

    A collision is any member equal to a generated path or below it, compared
    case- and Unicode-normalized: the freeze must never silently replace program
    bytes, and a program directory must never make a generated member
    unextractable on Windows.
    """
    frozen={_fold(name) for name in frozen_member_names(descriptor)}
    conflicts=[]
    for name in member_names:
        parts=[_fold(part) for part in name.split('/')]
        if any('/'.join(parts[:depth]) in frozen for depth in range(1,len(parts)+1)):
            conflicts.append(name)
    return conflicts


def package_metadata(package,platform=None):
    """Validate the declared root, entry, native dependencies and extension manifest.

    For ``windows_x64`` the declared paths are validated with the same raw
    component rules the ZIP intake applies, and no declared file may name the
    path where the freeze generates the public configuration: that member would
    otherwise overwrite a declared program file.
    """
    require(isinstance(package,dict) and set(package)==set(PACKAGE_FIELDS),'package_metadata')
    root=package['root']
    require(isinstance(root,str) and 0<len(root)<=MAX_PATH_COMPONENT and '/' not in root,'package_metadata')
    _raw_components(root,'package_metadata',True)
    entry=_package_path(package['entry'],platform)
    dependencies=package['dependencies']
    require(isinstance(dependencies,list) and 0<len(dependencies)<=_MAX_DEPENDENCIES,'package_metadata')
    for dependency in dependencies:
        _package_path(dependency,platform)
    require(len(set(dependencies))==len(dependencies),'package_metadata')
    extension=_package_path(package['extension'],platform)
    if platform=='windows_x64':
        config=config_member_relative(entry)
        require(all(name!=config for name in (entry,*dependencies,extension)),'package_metadata')
    return package


def descriptor_valid(d):
    fields=set(d) if isinstance(d,dict) else set()
    require(fields in (DESCRIPTOR_FIELDS,DESCRIPTOR_FIELDS|{'package'}),'descriptor_fields')
    require(d['protocol_version']=='gep/1' and d['sdk_version']=='0.1.0' and d['host_version']=='4.7.2','incompatible_build')
    require(d['platform'] in ('godot_web',*NATIVE_PLATFORMS),'unsupported_platform')
    require(isinstance(d['version'],str) and 0<len(d['version'])<=64,'build_version')
    require(isinstance(d['program_sha256'],str) and len(d['program_sha256'])==64 and all(c in '0123456789abcdef' for c in d['program_sha256']),'program_digest')
    require(isinstance(d['schemas'],dict) and 1<=len(d['schemas'])<=16,'schema_limit')
    for definition in d['schemas'].values():
        require(set(definition)=={'id','version','schema'},'schema_definition')
        schema_safe(definition['schema'])
    require(isinstance(d['codebook'],dict) and bool(d['codebook']),'codebook_required')
    if 'package' in d:
        package_metadata(d['package'],d['platform'])
    elif d['platform']=='windows_x64':
        require(False,'package_metadata')


def _fold(name):
    """Windows/zip collision key: Unicode-normalized and case-insensitive."""
    return unicodedata.normalize('NFC',name).casefold()


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


def _windows_member_path(name):
    """Reject every path shape Windows would resolve to something else.

    Backslashes, drive/UNC prefixes, alternate data streams and device names are
    not portable names: a single extraction path must stay one ordinary file on
    Windows as it is in the archive. Raw dot/empty components are refused before
    ``PurePosixPath`` could normalize them away, and trailing dots/spaces and
    control characters are stripped or reinterpreted by Windows, so they are
    refused too. A trailing ``/`` marks a directory member only.
    """
    require(isinstance(name,str) and 0<len(name)<=MAX_MEMBER_PATH,'unsafe_path')
    base=name[:-1] if name.endswith('/') else name
    return PurePosixPath('/'.join(_raw_components(base,'unsafe_path',True)))


def _pe_image(head):
    """Parse the PE headers kept from the bounded member read.

    Only the documented headers are inspected; nothing is loaded or executed.
    """
    require(isinstance(head,bytes) and len(head)>=0x40 and head[:2]==b'MZ','invalid_image')
    offset=struct.unpack_from('<I',head,0x3c)[0]
    require(0x40<=offset and offset+24<=len(head),'invalid_image')
    require(head[offset:offset+4]==b'PE\x00\x00','invalid_image')
    machine=struct.unpack_from('<H',head,offset+4)[0]
    characteristics=struct.unpack_from('<H',head,offset+22)[0]
    optional_size=struct.unpack_from('<H',head,offset+20)[0]
    require(optional_size>=64 and offset+24+optional_size<=len(head),'invalid_image')
    magic=struct.unpack_from('<H',head,offset+24)[0]
    require(magic in (0x10b,0x20b),'invalid_image')
    return {'machine':machine,'magic':magic,'executable':bool(characteristics&0x0002),'dll':bool(characteristics&0x2000)}


def _require_windows_x64(head,code,executable=True,dll=False):
    image=_pe_image(head)
    require(image['machine']==WINDOWS_PE_MACHINE and image['magic']==WINDOWS_PE_MAGIC,'wrong_architecture')
    require(image['executable']==executable and image['dll']==dll,code)
    return image


def pck_engine_version(head):
    """The Godot version recorded in a standalone PCK header (format v1-v4)."""
    require(isinstance(head,bytes) and len(head)>=20 and head[:4]==b'GDPC','invalid_pck')
    pack_format=struct.unpack_from('<I',head,4)[0]
    require(2<=pack_format<=4,'unsupported_pck')
    return tuple(struct.unpack_from('<III',head,8))


def _gdextension_sections(raw):
    """Parse one packaged GDExtension manifest into its effective sections.

    The manifest is an INI-shaped document (``[configuration]``,
    ``[libraries]``). Whole-line comments start with ``;`` or ``#``; a later key
    assignment overrides an earlier one; a malformed line fails closed. Nothing
    is loaded or executed: only the declaration text is read.
    """
    try:
        text=raw.decode('utf-8')
    except UnicodeDecodeError:
        raise Rejected('invalid_extension')
    sections={};current=None
    for line in text.splitlines():
        stripped=line.strip()
        if not stripped or stripped.startswith((';','#')):
            continue
        if stripped.startswith('['):
            require(stripped.endswith(']'),'invalid_extension')
            current=stripped[1:-1].strip()
            require(bool(current),'invalid_extension')
            sections.setdefault(current,{})
            continue
        require(current is not None and '=' in stripped,'invalid_extension')
        key,_,value=stripped.partition('=')
        key=key.strip();value=value.strip()
        require(bool(key) and bool(value),'invalid_extension')
        sections[current][key]=value
    return sections


def _quoted(value):
    """One double-quoted manifest string, or ``None`` for anything else."""
    if isinstance(value,str) and len(value)>=2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return None


def extension_release_dependency(raw,dependencies):
    """The packaged GDExtension manifest's effective Windows release x86_64 reference.

    A real ``[configuration]`` entry symbol and a ``[libraries]``
    ``windows.release.x86_64`` entry are required; comments, a different
    platform key and unquoted values do not count. The referenced ``res://``
    path must stay inside the project (no traversal, drive, device name or
    character Windows rejects) and its file name must be the declared root-level
    dependency that Godot's Windows exporter flattens next to the executable.
    This proves the manifest's own declaration only; the PCK interior is not
    inspected here.
    """
    sections=_gdextension_sections(raw)
    configuration=sections.get('configuration')
    require(isinstance(configuration,dict),'extension_configuration_missing')
    symbol=_quoted(configuration.get('entry_symbol'))
    require(symbol is not None and 0<len(symbol)<=128 and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',symbol) is not None,
            'extension_entry_symbol')
    libraries=sections.get('libraries')
    require(isinstance(libraries,dict),'extension_libraries_missing')
    target=_quoted(libraries.get('windows.release.x86_64'))
    require(target is not None and target.startswith('res://'),'extension_library_missing')
    parts=_raw_components(target[len('res://'):],'unsafe_path',True)
    basename=parts[-1]
    matches=[dependency for dependency in dependencies if PurePosixPath(dependency).name==basename]
    require(len(matches)==1 and matches[0]==basename,'extension_dependency_missing')
    return basename


def _read_native_payload(archive,entries):
    """Read every member once for CRC and real bounds; keep the first bytes.

    Directory metadata is never trusted alone: the streamed read also proves the
    member really decodes to the bounded size the archive declares.
    """
    heads={};count=0
    for item in entries:
        if item.filename.endswith('/'):
            continue
        head=bytearray()
        with archive.open(item) as stream:
            while chunk:=stream.read(65536):
                count+=len(chunk)
                require(count<=MAX_NATIVE_EXPANDED,'expanded_limit',413)
                if len(head)<_HEADER_BYTES:
                    head.extend(chunk[: _HEADER_BYTES-len(head)])
        heads[item.filename]=bytes(head)
    return heads


def _macos_program(entries,descriptor):
    """The macOS contract: one ``*.app`` bundle with its declared frameworks."""
    seen=set();roots=set();files=0;total=0;binary='';plist=False;pck=False;framework_files=0
    names=set()
    for item in entries:
        path,is_directory=_regular_native_member(item)
        require(_fold(item.filename) not in seen,'duplicate_path')
        seen.add(_fold(item.filename))
        names.add(item.filename)
        if is_directory:
            require(len(path.parts)>=1 and path.parts[0].endswith('.app'),'reserved_path')
            continue
        require(len(path.parts)>=2 and path.parts[0].endswith('.app'),'reserved_path')
        roots.add(path.parts[0])
        require(len(roots)==1,'multiple_bundles')
        mode=(item.external_attr>>16)&0o7777
        require(not (mode&0o111 and path.suffix.lower() in SCRIPT_SUFFIXES),'unsafe_script')
        inner=path.parts[1:]
        if inner[:1]==('Contents',):
            if inner[1:2]==('MacOS',) and mode&0o111:
                binary=item.filename
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
    require(bool(binary),'missing_binary')
    require(plist,'missing_info_plist')
    require(pck,'missing_pck')
    # The bundle must really ship its native dependency, not just declare an
    # empty (or entirely absent) Frameworks directory that would evade the check.
    require(framework_files>0,'missing_dependencies')
    package=descriptor.get('package')
    if isinstance(package,dict):
        for dependency in package.get('dependencies',[]):
            require(any(name==dependency or name.startswith(dependency+'/') for name in names),'missing_dependencies')
    return {'platform':'macos_arm64','app':next(iter(roots)),'entry':binary,'members':files,'expanded':total}


def _windows_program(entries,descriptor,heads):
    """The Windows x64 contract: one program root with the declared files.

    The entry and every declared dependency must be a real PE32+ x86-64 image,
    the PCK must record the descriptor's engine version and the packaged
    GDExtension manifest must name the declared dependencies. Nothing is
    extracted or executed; only the bounded header bytes and the declaration
    text are inspected.
    """
    package=descriptor.get('package')
    require(isinstance(package,dict) and set(package)==set(PACKAGE_FIELDS),'package_metadata')
    declared_root=package['root']
    entry=package['entry']
    dependencies=list(package['dependencies'])
    extension=package['extension']
    declared={entry,extension,*dependencies}
    # Collision tracking is case- and Unicode-normalized per component, so a
    # file can never share a path with a directory (``root/Foo`` next to
    # ``root/foo/bar``) or with another spelling of the same member.
    seen_files=set();seen_dirs=set();implied_dirs=set();roots=set();names=set();files=0;total=0
    for item in entries:
        path=_windows_member_path(item.filename)
        normalized=[_fold(part) for part in path.parts]
        key='/'.join(normalized)
        ancestors=['/'.join(normalized[:depth]) for depth in range(1,len(normalized))]
        require(key not in seen_files,'duplicate_path')
        require(not (set(ancestors) & seen_files),'duplicate_path')
        is_directory=_regular_native_member(item)[1]
        if is_directory:
            require(key not in seen_dirs,'duplicate_path')
            seen_dirs.add(key)
            roots.add(path.parts[0])
            require(roots=={declared_root},'multiple_bundles')
            implied_dirs.update(ancestors)
            continue
        require(key not in seen_dirs and key not in implied_dirs,'duplicate_path')
        seen_files.add(key)
        implied_dirs.update(ancestors)
        require(len(path.parts)>=2,'reserved_path')
        roots.add(path.parts[0])
        require(roots=={declared_root},'multiple_bundles')
        relative='/'.join(path.parts[1:])
        names.add(relative)
        require(path.suffix.lower() not in SCRIPT_SUFFIXES,'unsafe_script')
        if relative not in declared and path.suffix.lower() in WINDOWS_IMAGE_SUFFIXES:
            require(False,'undeclared_executable')
        files+=1
        total+=item.file_size
        require(total<=MAX_NATIVE_EXPANDED and item.file_size<=MAX_NATIVE_EXPANDED,'expanded_limit',413)
        require(item.file_size/max(item.compress_size,1)<=MAX_COMPRESSION_RATIO,'compression_ratio')
    root=declared_root
    require(entry in names,'missing_entry')
    _require_windows_x64(heads[f'{root}/{entry}'],'invalid_entry',executable=True,dll=False)
    pcks=sorted(name for name in names if name.lower().endswith('.pck'))
    require(bool(pcks) and len(pcks)<=8,'missing_pck')
    for name in pcks:
        require(pck_engine_version(heads[f'{root}/{name}'])==HOST_VERSION,'engine_version_mismatch')
    for dependency in dependencies:
        require(dependency in names,'missing_dependencies')
        _require_windows_x64(heads[f'{root}/{dependency}'],'invalid_dependency',executable=True,dll=True)
    require(extension in names,'missing_extension')
    extension_release_dependency(heads[f'{root}/{extension}'],dependencies)
    return {'platform':'windows_x64','app':root,'entry':f'{root}/{entry}','dependencies':[f'{root}/{name}' for name in dependencies],
            'extension':f'{root}/{extension}','pcks':[f'{root}/{name}' for name in pcks],'members':files,'expanded':total}


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


def native_program_valid(raw,descriptor):
    """Validate one bounded native program archive against its registered descriptor.

    The archive is the frozen program only: traversal, absolute, drive/UNC,
    colliding, linked, script and non-regular members are refused, the declared
    entry must be a real x86-64 PE executable, every declared native dependency
    must exist as a real x86-64 library, the PCK must record the descriptor's
    engine version and the packaged GDExtension manifest must reference a
    declared dependency from its effective Windows release x86_64 entry. A
    member that collides with a member the freeze generates (the public
    configuration, schema, codebook, license or notices) is refused here, before
    the freeze could replace program bytes. The archive digest must equal the
    descriptor's program digest (which deliberately excludes the external
    connection configuration). Nothing is extracted, executed or fetched here;
    the caller stores the raw bytes unchanged.
    """
    platform=descriptor.get('platform') if isinstance(descriptor,dict) else None
    require(platform in NATIVE_PLATFORMS,'native_platform')
    expected=descriptor.get('program_sha256')
    require(isinstance(expected,str) and len(expected)==64 and all(c in '0123456789abcdef' for c in expected),'program_digest')
    require(len(raw)<=MAX_NATIVE_ARCHIVE,'archive_limit',413)
    digest=hashlib.sha256(raw).hexdigest()
    require(digest==expected,'program_digest_mismatch')
    try:
        archive=zipfile.ZipFile(io.BytesIO(raw))
        entries=archive.infolist()
        require(0<len(entries)<=MAX_NATIVE_FILES,'file_count')
        heads=_read_native_payload(archive,entries)
        if platform=='macos_arm64':
            summary=_macos_program(entries,descriptor)
        else:
            summary=_windows_program(entries,descriptor,heads)
        require(not frozen_conflict([item.filename for item in entries],descriptor),'frozen_path_conflict')
        return {**summary,'digest':digest}
    except (zipfile.BadZipFile,KeyError,RuntimeError):
        from .protocol import Rejected
        raise Rejected('invalid_archive')
