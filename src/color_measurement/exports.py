"""Lossless schema-1 JSON and CSV; retrying persistence never acquires a reading."""

import csv
import errno
import hashlib
import io
import json
import logging
import os
import stat
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Callable

from .json_values import unique_object
from .models import Observation, observation_from_dict


class SaveError(OSError):
    """Carries the still-valid observation and serialized record after IO failure."""

    def __init__(self, path: Path, observation: Observation, serialized: str | bytes,
                 cause: OSError, *, format: str):
        super().__init__(f"Could not save {path}: {cause}")
        self.path = path
        self.observation = observation
        self.serialized = serialized
        self.payload = serialized.encode("utf-8") if isinstance(serialized, str) else serialized
        self.format = format


@dataclass(frozen=True)
class SaveResult:
    """A durable saved path and independent, visible TI3 companion outcomes."""

    path: Path
    ti3: dict


@dataclass(frozen=True)
class PublicationResult:
    """Exact published bytes and observed synchronization, not power-loss proof."""

    path: Path
    payload: bytes
    file_sync: str
    directory_synced: bool

    @property
    def limitations(self) -> tuple[str, ...]:
        limits = []
        if self.file_sync != "fullsync":
            limits.append("Full device-cache synchronization is unconfirmed")
        if not self.directory_synced:
            limits.append("Directory synchronization is unsupported or unconfirmed")
        return tuple(limits)


def _preserve_publication_failure(first, later, context):
    """Preserve the first control and any real receipt through recovery failure."""
    reason = "; ".join([str(later), *getattr(later, "__notes__", ())])
    if isinstance(first, Exception) and not isinstance(later, Exception):
        earlier = "; ".join([str(first), *getattr(first, "__notes__", ())])
        later.add_note(f"{context}; earlier {type(first).__name__}: {earlier}")
        publication = getattr(first, "publication", None)
        if isinstance(publication, PublicationResult):
            later.publication = publication
        return later
    first.add_note(f"{context}: {type(later).__name__}: {reason}")
    return first


def to_json(observation: Observation) -> str:
    """Serialize complete metadata, explicit nulls, and finite scientific values."""
    data = asdict(observation)
    observation_from_dict(data)  # Also validate records constructed directly by callers.
    # Escaped Unicode remains lossless and can reach stdout in legacy encodings.
    return json.dumps(data, ensure_ascii=True, allow_nan=False, indent=2) + "\n"


def from_json(text: str) -> Observation:
    """Reject duplicate/unknown fields, unsupported schema, nonfinite values."""
    return observation_from_dict(json.loads(text, object_pairs_hook=unique_object))


def to_mat(observation: Observation) -> bytes:
    """Encode the current complete observation in the shared JSON-bearing MAT format."""
    from .document_mat import to_mat as document_to_mat

    return document_to_mat(json.loads(to_json(observation)), kind="observation")


def from_mat(payload: bytes) -> Observation:
    """Validate the declared current observation MAT; no terminal operation is invented."""
    from .document_mat import from_mat as document_from_mat

    return observation_from_dict(document_from_mat(payload, kind="observation"))


_COLUMNS = (
    "schema_version",
    "observation_id",
    "provenance",
    "sample_id",
    "xyz_X",
    "xyz_Y",
    "xyz_Z",
    "xyz_unit",
    "xyz_scale",
    "record_json",
)
_CSV_LOCK = Lock()


def _safe_cell(value: str) -> str:
    # Prefix only text; numerical signed samples remain numerical CSV cells.
    if value.startswith(("=", "+", "-", "@", "\t", "\r", "\n", "'")):
        return "'" + value
    return value


def _csv_row(observation: Observation) -> list[str]:
    data = json.loads(to_json(observation))
    xyz = observation.backend_reported.xyz
    return [
        str(observation.schema_version),
        _safe_cell(observation.observation_id),
        observation.provenance,
        _safe_cell(observation.sample_id),
        *((str(v) for v in xyz.values) if xyz else ("", "", "")),
        _safe_cell(xyz.unit) if xyz else "",
        _safe_cell(xyz.scale) if xyz else "",
        json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
    ]


def to_csv(observation: Observation) -> str:
    """One observation per row, with complete canonical JSON in record_json.

    XYZ convenience columns retain native units/scale. The JSON cell preserves
    every field, optional spectrum/grid, null, original text, and computed value.
    Potential spreadsheet formula text is escaped in convenience columns only.
    """
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(_COLUMNS)
    writer.writerow(_csv_row(observation))
    return stream.getvalue()


def from_csv(text: str) -> Observation:
    """Load one complete record and reject conflicting convenience columns."""
    # csv's field limit is process-global. Serialize our changes and restore it
    # after decoding so complete native output is not truncated at 128 KiB.
    with _CSV_LOCK:
        previous_limit = csv.field_size_limit()
        try:
            csv.field_size_limit(max(previous_limit, len(text)))
            rows = list(csv.reader(io.StringIO(text, newline="")))
        except csv.Error as error:
            raise ValueError(f"Malformed CSV: {error}") from error
        finally:
            csv.field_size_limit(previous_limit)
    if len(rows) != 2 or tuple(rows[0]) != _COLUMNS or len(rows[1]) != len(_COLUMNS):
        raise ValueError("Expected schema-1 CSV header and exactly one complete observation")
    observation = from_json(rows[1][-1])
    if rows[1] != _csv_row(observation):
        raise ValueError("CSV convenience columns disagree with the canonical record")
    return observation


def save(observation: Observation, path: str | Path, *, format: str = "json",
         previous_ti3: dict | None = None) -> SaveResult:
    """Publish a complete new file atomically, refusing any existing destination.

    Uses a temporary sibling and an exclusive hard link; unsupported filesystems
    fail explicitly. SaveError retains the observation and payload for retry.
    The canonical destination is never overwritten. After it is durable, the
    shared TI3 writer refreshes optional owned companions and returns their
    independent current/unavailable/stale outcomes in SaveResult.ti3.
    No acquisition, directory creation or automatic retry occurs.
    """
    if format not in ("json", "csv", "mat"):
        raise ValueError("format must be json, csv or mat")
    serialized = (to_json(observation) if format == "json" else to_csv(observation)
                  if format == "csv" else to_mat(observation))
    target = Path(path)
    payload = serialized.encode("utf-8") if isinstance(serialized, str) else serialized
    _publish_new_file(
        target,
        payload,
        lambda cause: SaveError(target, observation, serialized, cause, format=format),
    )
    from .ti3_exports import save_companions
    return SaveResult(target, save_companions(observation, target, payload, previous_ti3))


def _publish_new_file(
    target: Path, payload: bytes, error_factory: Callable[[OSError], OSError]
) -> PublicationResult:
    """Shared atomic no-overwrite publication for already validated payloads."""
    return _publish_file(target, payload, error_factory)


class _OwnedFileReplacedDuringOpen(OSError):
    """Two ordinary inodes crossed one strict read; no bytes were accepted."""


def _open_regular_file(path: Path, *, sync: bool = False, delete: bool = False) -> int:
    """Open for strict authentication, sharing Windows owner renames safely.

    The caller checks file type, link count and descriptor/path identity.
    Windows opens the final path component's reparse point itself, not its
    target. Deletion sharing permits a POSIX-semantic owner rename during a read.
    Descriptor/path identity is checked at opening; retained-source validation
    owns any later authorized replacement while the old descriptor is read.
    Windows CRT synchronization requires write access, unlike a plain read.
    """
    if os.name != "nt":
        return os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # GENERIC_READ[/WRITE] plus DELETE only for rename, FILE_SHARE_READ|WRITE|DELETE, OPEN_EXISTING,
    # FILE_FLAG_OPEN_REPARSE_POINT. No creation, truncation or delete-on-close.
    handle = create(os.fspath(path), 0x80000000 | (0x40000000 if sync else 0)
                    | (0x00010000 if delete else 0),
                    0x7, None, 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_BINARY | (os.O_RDWR if sync else os.O_RDONLY))
    except BaseException as error:
        if not close(handle):
            failure = _preserve_publication_failure(
                error, ctypes.WinError(ctypes.get_last_error()), "Windows handle release failed",
            )
            if failure is not error:
                raise failure from error
        raise


def _owned_link_count(metadata, linked_source):
    """Allow only the publisher's observed temporary alias of the same inode."""
    if linked_source is not None:
        try:
            source = linked_source.lstat()
        except FileNotFoundError:
            pass
        else:
            if (stat.S_ISREG(source.st_mode)
                    and (source.st_dev, source.st_ino) == (metadata.st_dev, metadata.st_ino)):
                return 2
    return 1


@contextmanager
def _regular_descriptor(
    path: Path, *, sync: bool = False, delete: bool = False, linked_source: Path | None = None,
):
    """Open without blocking on a raced FIFO, optionally allowing an owned publication alias."""
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode)
            or before.st_nlink != _owned_link_count(before, linked_source)):
        raise OSError(f"Owned destination is not one ordinary file: {path}")
    descriptor = _open_regular_file(path, sync=sync, delete=delete)
    with _OwnedDescriptor(descriptor) as descriptor:
        metadata = os.fstat(descriptor)
        current = path.lstat()
        links = _owned_link_count(current, linked_source)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or current.st_nlink != links
            or metadata.st_nlink not in (0, links)
        ):
            raise OSError(f"Owned destination is not one ordinary file: {path}")
        if (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino):
            raise _OwnedFileReplacedDuringOpen(
                f"Owned destination changed while opening an ordinary file: {path}")
        if metadata.st_nlink != links:
            raise OSError(f"Owned destination is not one ordinary file: {path}")
        yield descriptor


def _replace_file(source: Path, target: Path, descriptor: int):
    """Rename an already authenticated source; its caller owns the descriptor.

    FileRenameInfoEx with REPLACE_IF_EXISTS|POSIX_SEMANTICS keeps existing
    destination handles usable while the name refers to the new file. Unsupported
    filesystem/API and readonly/access refusals propagate; there is no copy,
    unlink, legacy rename fallback or ignored readonly protection.
    """
    if os.name != "nt":
        return os.replace(source, target)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class RenameInfo(ctypes.Structure):
        _fields_ = [("Flags", ctypes.c_uint32), ("RootDirectory", ctypes.c_void_p),
                    ("FileNameLength", ctypes.c_uint32), ("FileName", ctypes.c_wchar * 1)]

    name = os.fspath(target.absolute()).encode("utf-16-le", errors="surrogatepass")
    information = ctypes.create_string_buffer(RenameInfo.FileName.offset + len(name) + 2)
    header = ctypes.cast(information, ctypes.POINTER(RenameInfo)).contents
    header.Flags = 0x3  # FILE_RENAME_FLAG_REPLACE_IF_EXISTS | FILE_RENAME_FLAG_POSIX_SEMANTICS
    header.RootDirectory = None
    header.FileNameLength = len(name)
    ctypes.memmove(ctypes.addressof(information) + RenameInfo.FileName.offset, name, len(name))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    replace = kernel.SetFileInformationByHandle
    replace.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    replace.restype = wintypes.BOOL
    if not replace(msvcrt.get_osfhandle(descriptor), 22, information, len(information)):
        raise ctypes.WinError(ctypes.get_last_error())


def _read_regular(path: Path, *, limit_bytes: int | None = None) -> bytes:
    """Refuse links/non-files when authenticating an owned publication."""
    if limit_bytes is not None and (type(limit_bytes) is not int or limit_bytes < 1):
        raise ValueError("File read limit must be a positive integer")
    with _regular_descriptor(path) as descriptor:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read() if limit_bytes is None else stream.read(limit_bytes + 1)
            if limit_bytes is not None and len(payload) > limit_bytes:
                raise ValueError(f"Selected file exceeds {limit_bytes} bytes: {path}")
            return payload


_LOCK_NAMES = (".batch.lock", ".ti3-export.lock")
# Exclusive lock descriptors whose close did not confirm, keyed by absolute lock
# path for diagnostics, with its acquired directory identity. Each retained
# lock is never closed again, so a reused descriptor number
# cannot be touched through it; only its own destination is retired, because the
# ambiguity is whether that one kernel lock is still held. Other destinations
# share none of it and keep publishing. A retained lock also keeps its
# directory descriptor open, so the retired directory's inode stays allocated
# for the process lifetime and a directory created later cannot inherit the
# identity (POSIX; see ``_hold_directory``).
_RETIRED_PUBLICATION_LOCKS = {}


def _hold_directory(folder):
    """Open ``folder`` itself, pinning its inode while the descriptor is held.

    Windows has no stdlib directory handle, so there the identity is read by
    path and a reused file index after deletion is not distinguished.
    """
    if os.name == "nt":
        return None
    return os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))


def _publication_cleanup_reason(folder):
    """Explain why publication into ``folder`` is refused in this process, or None."""
    if not _RETIRED_PUBLICATION_LOCKS:
        return None
    folder = Path(folder).absolute()
    retired = any(folder / name in _RETIRED_PUBLICATION_LOCKS for name in _LOCK_NAMES)
    if not retired:
        try:
            metadata = folder.stat()
        except OSError:
            # A destination that cannot be identified is not a retired one:
            # the path lookup above did not match it, and any publication
            # into it reports its own failure. Snapshots must not raise here.
            return None
        identity = metadata.st_dev, metadata.st_ino
        retired = any(
            lock.directory_identity == identity
            for lock in tuple(_RETIRED_PUBLICATION_LOCKS.values())
        )
    if retired:
        return (
            f"Publication lock cleanup for {folder} is unconfirmed; this destination "
            "is retired in this process. Rescue retained work to another folder"
        )
    return None


def _require_publication_cleanup(folder):
    reason = _publication_cleanup_reason(folder)
    if reason is not None:
        raise OSError(reason)


class _OwnedDescriptor:
    """Close once, preserving the first control and receipt if close also fails."""

    def __init__(self, descriptor=None):
        self.descriptor = descriptor
        self.release_failure = None

    def __enter__(self):
        return self.descriptor

    def __exit__(self, error_type, error, traceback):
        if self.release_failure is not None:
            failure = OSError("Descriptor release remains unconfirmed; no descriptor retry")
        elif self.descriptor is None:
            return False
        else:
            try:
                os.close(self.descriptor)
            except BaseException as failure:
                self.release_failure = failure
            else:
                self.descriptor = None
                return False
            failure = self.release_failure
        if error is not None:
            failure = _preserve_publication_failure(error, failure, "Descriptor release failed")
        raise failure


class _PublicationLock(_OwnedDescriptor):
    """Retain exclusive lock ownership when descriptor cleanup is unconfirmed."""

    def __init__(self, folder, name):
        if name not in {".ti3-export.lock", ".batch.lock"}:
            raise ValueError("Unknown publication lock name")
        super().__init__()
        self.path = Path(folder).absolute() / name
        self.directory_identity = None
        self.directory = _OwnedDescriptor()
        self._used = False

    def __enter__(self):
        _require_publication_cleanup(self.path.parent)
        if self._used:
            raise RuntimeError("Publication lock context has already been entered")
        self._used = True
        directory = self.path.parent.stat()
        self.directory_identity = directory.st_dev, directory.st_ino
        try:
            before = self.path.lstat()
        except FileNotFoundError:
            before = None
        if before and (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1):
            raise OSError("Publication lock must be one ordinary persistent file")
        self.descriptor = os.open(
            self.path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            0o600,
        )
        try:
            metadata = os.fstat(self.descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or self.path.is_symlink()
            ):
                raise OSError("Publication lock must be one ordinary persistent file")
            if os.name == "nt":
                import msvcrt

                if not metadata.st_size:
                    os.write(self.descriptor, b"\0")
                os.lseek(self.descriptor, 0, os.SEEK_SET)
                msvcrt.locking(self.descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current = self.path.lstat()
            if (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino):
                raise OSError("Publication lock was replaced")
            # Hold the directory that contains this exact lock file for as long
            # as the lock is held; its identity is the one a retirement keeps.
            self.directory.descriptor = _hold_directory(self.path.parent)
            if self.directory.descriptor is not None:
                entry = os.stat(
                    self.path.name, dir_fd=self.directory.descriptor, follow_symlinks=False
                )
                if (entry.st_dev, entry.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise OSError("Publication lock directory was replaced")
                current_directory = os.fstat(self.directory.descriptor)
            else:
                current_directory = self.path.parent.stat()
            current_identity = current_directory.st_dev, current_directory.st_ino
            expected_identity = self.directory_identity
            self.directory_identity = current_identity
            if current_identity != expected_identity:
                raise OSError("Publication lock directory was replaced")
        except BaseException as error:
            self.__exit__(type(error), error, error.__traceback__)
            raise
        return self

    def __exit__(self, error_type, error, traceback):
        try:
            super().__exit__(error_type, error, traceback)
        except BaseException:
            if self.release_failure is not None:
                # Retired: the directory descriptor stays open with the lock.
                _RETIRED_PUBLICATION_LOCKS.setdefault(self.path.absolute(), self)
            raise
        # The kernel lock is confirmed released. A directory descriptor that
        # cannot be closed is never closed again (its number may be reused) and
        # is reported, not raised: raising here would make every later release
        # of this lock fail and leave the owner unable to finish or exit.
        try:
            self.directory.__exit__(None, None, None)
        except OSError as failure:
            logging.getLogger(__name__).warning(
                "Directory descriptor for %s was not released: %s", self.path.parent, failure
            )
        return False


def _folder_lock(folder: Path, name: str):
    """Exclude cooperating writers with one owned publication-lock descriptor.

    Keep the kernel-lock inode. External edits and filesystem CAS remain outside
    this lock's guarantee. An unconfirmed descriptor close retires this one
    destination for the process; see ``_RETIRED_PUBLICATION_LOCKS``.
    """
    return _PublicationLock(folder, name)


def _sync_file(descriptor: int, target: Path) -> str:
    """Report actual file-sync calls; only unsupported full sync falls back."""
    os.fsync(descriptor)
    if sys.platform != "darwin":
        return "fsync"
    import fcntl
    full_sync = getattr(fcntl, "F_FULLFSYNC", None)
    try:
        if full_sync is None:
            raise OSError(errno.ENOTSUP, "F_FULLFSYNC is unavailable")
        fcntl.fcntl(descriptor, full_sync)
    except OSError as error:
        if error.errno not in {
            errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS, errno.ENOTTY,
        }:
            raise
        logging.getLogger(__name__).warning(
            "Full file synchronization is unsupported for %s; ordinary fsync "
            "completed, device-cache synchronization is unconfirmed: %s", target, error,
        )
        return "fsync"
    return "fullsync"


def _publish_owned_file(
    target: Path, payload: bytes, previous_sha256: str | None
) -> PublicationResult:
    """Publish owned bytes or synchronize an identical file without rewriting."""
    _require_publication_cleanup(target.parent)
    try:
        current = _read_regular(target)
    except FileNotFoundError:
        return _publish_new_file(target, payload, lambda cause: OSError(str(cause)))
    if current == payload:
        file_sync = "unconfirmed"
        authenticated = False
        try:
            with _regular_descriptor(target, sync=True) as descriptor:
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    if stream.read() != payload:
                        raise FileExistsError(
                            f"Owned bytes changed before synchronization: {target}"
                        )
                authenticated = True
                file_sync = _sync_file(descriptor, target)
            synced = _sync_directory(target.parent)
        except BaseException as error:
            if authenticated:
                error.publication = PublicationResult(target, payload, file_sync, False)
            raise
        return PublicationResult(target, payload, file_sync, synced)
    if previous_sha256 is None or hashlib.sha256(current).hexdigest() != previous_sha256:
        raise FileExistsError(f"Companion was changed or is not owned: {target}")
    return _publish_file(target, payload, lambda cause: OSError(str(cause)), previous_sha256)


def _authenticate_publication(target, payload, identity, descriptor, linked_source):
    """Confirm the actual destination, including name-based publication effects."""
    metadata = os.fstat(descriptor)
    if (metadata.st_dev, metadata.st_ino) != identity:
        raise FileExistsError(f"Published file is not the synchronized temporary inode: {target}")
    with os.fdopen(descriptor, "rb", closefd=False) as stream:
        matches = stream.read(len(payload) + 1) == payload
    current = target.lstat()
    metadata = os.fstat(descriptor)
    links = _owned_link_count(current, linked_source)
    if (not matches or not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != identity
            or current.st_nlink != links or metadata.st_nlink != links):
        raise FileExistsError(f"Published file changed before confirmation: {target}")


def _publish_file(target, payload, error_factory, previous_sha256=None):
    _require_publication_cleanup(target.parent)
    temporary = None
    temporary_identity = None
    save_error = None
    file_sync = "unconfirmed"
    published = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=".measurement-",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            metadata = os.fstat(stream.fileno())
            temporary_identity = metadata.st_dev, metadata.st_ino
            stream.write(payload)
            stream.flush()
            file_sync = _sync_file(stream.fileno(), target)
        if (previous_sha256 is not None
                and hashlib.sha256(_read_regular(target)).hexdigest() != previous_sha256):
            raise FileExistsError(f"Companion changed before replacement: {target}")
        with _regular_descriptor(
            temporary, delete=os.name == "nt" and previous_sha256 is not None,
        ) as descriptor:
            current = os.fstat(descriptor)
            if (current.st_dev, current.st_ino) != temporary_identity:
                raise FileExistsError(
                    f"Synchronized temporary file changed before publication: {temporary}")
            if previous_sha256 is None:
                os.link(temporary, target)
            else:
                _replace_file(temporary, target, descriptor)
                temporary = None
            # Name-based link/rename may consume a substituted staging entry.
            # Confirm the destination before recording its effect or closing either descriptor.
            with _regular_descriptor(target, linked_source=temporary) as destination:
                _authenticate_publication(
                    target, payload, temporary_identity, destination, temporary,
                )
                published = PublicationResult(target, payload, file_sync, False)
    except BaseException as error:
        save_error = error_factory(error) if isinstance(error, OSError) else error
        if published is not None:
            save_error.publication = published
        else:
            try:
                # Identical bytes in an earlier destination do not establish that
                # this invocation published or synchronized its inode. Another inode
                # there, such as the existing file a refused save left in place, is
                # no publication rather than a failed authentication.
                metadata = target.lstat()
                if (metadata.st_dev, metadata.st_ino) == temporary_identity:
                    with _regular_descriptor(target, linked_source=temporary) as descriptor:
                        _authenticate_publication(
                            target, payload, temporary_identity, descriptor, temporary,
                        )
                        save_error.publication = PublicationResult(
                            target, payload, file_sync, False)
            except FileNotFoundError:
                # No file means no publication; an absent receipt is a fact, not a
                # failure, and must not displace the first control's own notes.
                pass
            except BaseException as failure:
                save_error = _preserve_publication_failure(
                    save_error,
                    failure,
                    "Publication authentication failed",
                )
        if save_error is error:
            raise
        raise save_error from error
    finally:
        if temporary is not None:
            try:
                try:
                    current = temporary.lstat()
                except FileNotFoundError:
                    pass
                else:
                    if (not stat.S_ISREG(current.st_mode)
                            or (current.st_dev, current.st_ino) != temporary_identity):
                        raise FileExistsError(
                            f"Temporary entry changed; cleanup refused: {temporary}")
                    temporary.unlink(missing_ok=True)
            except BaseException as error:
                message = f"Could not remove temporary file {temporary}"
                if save_error is not None:
                    selected = _preserve_publication_failure(save_error, error, message)
                    if selected is not save_error:
                        raise selected from save_error
                elif isinstance(error, OSError):
                    logging.getLogger(__name__).warning("%s: %s", message, error)
                else:
                    # Publication succeeded before cleanup; directory sync has
                    # not run. Keep those observed facts on the same control.
                    error.publication = PublicationResult(target, payload, file_sync, False)
                    raise
    try:
        directory_synced = _sync_directory(target.parent)
    except BaseException as error:
        failure = error_factory(error) if isinstance(error, OSError) else error
        failure.add_note("Canonical bytes were published; directory synchronization is unconfirmed")
        failure.publication = PublicationResult(target, payload, file_sync, False)
        if failure is error:
            raise
        raise failure from error
    return PublicationResult(target, payload, file_sync, directory_synced)


def _sync_directory(folder: Path) -> bool:
    """Sync POSIX publication entries; Windows has no stdlib directory sync."""
    if os.name == "nt":
        return False
    owner = None
    try:
        descriptor = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        owner = _OwnedDescriptor(descriptor)
        os.fsync(descriptor)
        return True
    except OSError as error:
        if error.errno not in {
            errno.EINVAL,
            errno.ENOTSUP,
            errno.EOPNOTSUPP,
            errno.ENOSYS,
            errno.ENOTTY,
        }:
            raise
        logging.getLogger(__name__).warning(
            "Directory synchronization is unsupported for %s: %s", folder, error
        )
        return False
    finally:
        if owner is not None:
            owner.__exit__(*sys.exc_info())
