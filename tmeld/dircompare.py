"""Folder-comparison model: directory trees -> per-pane row states.

Pure logic, no UI imports — the DirDiffView renders what this produces.

Provenance (identical behavior is the point, as with the diff engine):
  * StatItem through _files_same are copied verbatim from upstream
    meld/dirdiff.py:68-260 (module-level, UI-free already).
  * ComparisonOptions and CanonicalListing are copied verbatim from
    dirdiff.py:284-341.
  * The STATE_* constants are meld/vc/_vc.py:46-62 (PARITY.md §2 keys
    tree styling off them); re-point to the vendored vc package when
    Phase 7 vendors it.
  * DirComparison.scan_iter is a UI-free port of
    DirDiff._search_recursively_iter (dirdiff.py:967) and _entry_state
    of _update_item_state (dirdiff.py:1797).
  * DEFAULT_NAME_FILTERS / comparison args mirror the gschema defaults
    (data/org.gnome.Meld.gschema.xml).

Not ported (yet): state-based row filtering (F8 menu), the newest-file
emblem, symlink "name -> target" display overrides.
"""

import collections
import errno
import os
import stat
import unicodedata
from collections import namedtuple
from decimal import Decimal
from mmap import ACCESS_COPY, mmap
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from tmeld._vendor.meld.filters import FilterEntry
from tmeld.misc import all_same, apply_text_filters

# Tree states (upstream meld/vc/_vc.py:46; comments theirs)
# ignored, new, normal, ignored changes,
# error, placeholder, vc added
# vc modified, vc renamed, vc conflict, vc removed
# locally removed, end
(
    STATE_IGNORED,
    STATE_NONE,
    STATE_NORMAL,
    STATE_NOCHANGE,
    STATE_ERROR,
    STATE_EMPTY,
    STATE_NEW,
    STATE_MODIFIED,
    STATE_RENAMED,
    STATE_CONFLICT,
    STATE_REMOVED,
    STATE_MISSING,
    STATE_NONEXIST,
    STATE_SPINNER,
    STATE_MAX,
) = list(range(15))


class StatItem(namedtuple("StatItem", "mode size time")):
    __slots__ = ()

    @classmethod
    def _make(cls, stat_result):
        return StatItem(
            stat.S_IFMT(stat_result.st_mode), stat_result.st_size, stat_result.st_mtime
        )

    def shallow_equal(self, other: "StatItem", time_resolution_ns: int) -> bool:
        if self.size != other.size:
            return False

        # Check for the ignore-timestamp configuration first
        if time_resolution_ns == -1:
            return True

        # Shortcut to avoid expensive Decimal calculations. 2 seconds is our
        # current accuracy threshold (for VFAT), so should be safe for now.
        if abs(self.time - other.time) > 2:
            return False

        dectime1 = Decimal(self.time).scaleb(Decimal(9)).quantize(1)
        dectime2 = Decimal(other.time).scaleb(Decimal(9)).quantize(1)
        mtime1 = dectime1 // time_resolution_ns
        mtime2 = dectime2 // time_resolution_ns

        return mtime1 == mtime2


CacheResult = namedtuple("CacheResult", "stats result")


_cache: Dict[tuple, CacheResult] = {}
Same, SameFiltered, DodgySame, DodgyDifferent, Different, FileError = list(range(6))
# TODO: Get the block size from os.stat
CHUNK_SIZE = 4096


def remove_blank_lines(text):
    """
    Remove blank lines from text.
    And normalize line ending
    """
    return b"\n".join(filter(bool, text.splitlines()))


def _files_contents(files, stats):
    mmaps = []
    is_bin = False
    contents = [b"" for file_obj in files]

    for index, file_and_stat in enumerate(zip(files, stats)):
        file_obj, stat_ = file_and_stat
        # use mmap for files with size > CHUNK_SIZE
        data = b""
        if stat_.size > CHUNK_SIZE:
            data = mmap(file_obj.fileno(), 0, access=ACCESS_COPY)
            mmaps.append(data)
        else:
            data = file_obj.read()
        contents[index] = data

        # Rough test to see whether files are binary.
        chunk_size = min([stat_.size, CHUNK_SIZE])
        if b"\0" in data[:chunk_size]:
            is_bin = True

    return contents, mmaps, is_bin


def _contents_same(contents, file_size):
    other_files_index = list(range(1, len(contents)))
    chunk_range = zip(
        range(0, file_size, CHUNK_SIZE),
        range(CHUNK_SIZE, file_size + CHUNK_SIZE, CHUNK_SIZE),
    )

    for start, end in chunk_range:
        chunk = contents[0][start:end]
        for index in other_files_index:
            if not chunk == contents[index][start:end]:
                return Different


def _normalize(contents, ignore_blank_lines, regexes=()):
    contents = (bytes(c) for c in contents)
    # For probable text files, discard newline differences to match
    if ignore_blank_lines:
        contents = (remove_blank_lines(c) for c in contents)
    else:
        contents = (b"\n".join(c.splitlines()) for c in contents)

    if regexes:
        contents = (apply_text_filters(c, regexes) for c in contents)
        if ignore_blank_lines:
            # We re-remove blank lines here in case applying text
            # filters has caused more lines to be blank.
            contents = (remove_blank_lines(c) for c in contents)

    return contents


def _files_same(files, regexes, comparison_args):
    """Determine whether a list of files are the same.

    Possible results are:
      Same: The files are the same
      SameFiltered: The files are identical only after filtering with 'regexes'
      DodgySame: The files are superficially the same (i.e., type, size, mtime)
      DodgyDifferent: The files are superficially different
      FileError: There was a problem reading one or more of the files
    """

    if all_same(files):
        return Same

    files = tuple(files)
    stats = tuple([StatItem._make(os.stat(f)) for f in files])

    shallow_comparison = comparison_args["shallow-comparison"]
    time_resolution_ns = comparison_args["time-resolution"]
    ignore_blank_lines = comparison_args["ignore_blank_lines"]
    apply_text_filters = comparison_args["apply-text-filters"]

    need_contents = ignore_blank_lines or apply_text_filters

    regexes = tuple(regexes) if apply_text_filters else ()

    # If all entries are directories, they are considered to be the same
    if all([stat.S_ISDIR(s.mode) for s in stats]):
        return Same

    # If any entries are not regular files, consider them different
    if not all([stat.S_ISREG(s.mode) for s in stats]):
        return Different

    # Compare files superficially if the options tells us to
    if shallow_comparison:
        all_same_timestamp = all(
            s.shallow_equal(stats[0], time_resolution_ns) for s in stats[1:]
        )
        return DodgySame if all_same_timestamp else Different

    same_size = all_same([s.size for s in stats])
    # If there are no text filters, unequal sizes imply a difference
    if not need_contents and not same_size:
        return Different

    # Check the cache before doing the expensive comparison
    cache_key = (files, need_contents, regexes, ignore_blank_lines)
    cache = _cache.get(cache_key)
    if cache and cache.stats == stats:
        return cache.result

    # Open files and compare bit-by-bit
    result = None

    try:
        mmaps = []
        handles = [open(file_path, "rb") for file_path in files]
        try:
            contents, mmaps, is_bin = _files_contents(handles, stats)

            # compare files chunk-by-chunk
            if same_size:
                result = _contents_same(contents, stats[0].size)
            else:
                result = Different

            # normalize and compare files again
            if result == Different and need_contents and not is_bin:
                contents = _normalize(contents, ignore_blank_lines, regexes)
                result = SameFiltered if all_same(contents) else Different

        # Files are too large; we can't apply filters
        except (MemoryError, OverflowError):
            result = DodgySame if all_same(stats) else DodgyDifferent
        finally:
            for m in mmaps:
                m.close()
            for h in handles:
                h.close()
    except IOError:
        # Don't cache generic errors as results
        return FileError

    if result is None:
        result = Same

    _cache[cache_key] = CacheResult(stats, result)
    return result


class ComparisonOptions:
    def __init__(
        self,
        *,
        ignore_case: bool = False,
        normalize_encoding: bool = False,
    ):
        self.ignore_case = ignore_case
        self.normalize_encoding = normalize_encoding


class CanonicalListing:
    """Multi-pane lists with canonicalised matching and error detection"""

    def __init__(self, n: int, options: ComparisonOptions):
        self.items = collections.defaultdict(lambda: [None] * n)
        self.stripped_items = {}
        self.errors = []
        self.whitespace = []
        self.options = options

    def add(self, pane: int, item: str):
        # normalize the name depending on settings
        ci = item
        if self.options.ignore_case:
            ci = ci.lower()
        if self.options.normalize_encoding:
            # NFC or NFD will work here, changing all composed or decomposed
            # characters to the same set for matching only.
            ci = unicodedata.normalize("NFC", ci)

        # add the item to the comparison tree
        existing_item = self.items[ci][pane]
        if existing_item is None:
            self.items[ci][pane] = item
        else:
            self.errors.append((pane, item, existing_item))

        stripped_item = ci.strip()
        if stripped_item in self.stripped_items:
            # If we have an existing stripped item and its pre-stripping
            # value differs, then we have a case of misleading whitespace
            if self.stripped_items[stripped_item] != ci:
                self.whitespace.append((pane, item))
        else:
            self.stripped_items[stripped_item] = ci

    def get(self):
        def filled(seq):
            fill_value = next(s for s in seq if s)
            return tuple(s or fill_value for s in seq)

        return sorted(filled(v) for v in self.items.values())


# gschema filename-filters defaults, (name, active, shell pattern)
DEFAULT_NAME_FILTERS: List[Tuple[str, bool, str]] = [
    ("Backups", True, "#*# .#* ~* *~ *.{orig,bak,swp}"),
    ("OS-Specific Metadata", True,
     ".DS_Store ._* .Spotlight-V100 .Trashes Thumbs.db Desktop.ini"),
    ("Version Control", True,
     "_MTN .bzr .svn .svn .hg .fslckout _FOSSIL_ .fos CVS _darcs .git "
     ".svn .osc"),
    ("Binaries", True, "*.{pyc,a,obj,o,so,la,lib,dll,exe}"),
    ("Media", False,
     "*.{jxl,jpg,jpeg,gif,png,avif,webp,heif,heic,bmp,tif,tiff,raw,dng,"
     "cr2,wav,wave,mp3,ogg,oga,vorbis,spx,opus,flac,ac3,aac,aif,aiff,"
     "aifc,alac,m4a,3gp,wma,aup,aup3,avi,mov,mpg,mpeg,mp4,m4v,webm,ogv,"
     "flv,xcf,xpm,ora,kra,psd,psp}"),
]

# gschema folder-* defaults, keyed as _files_same expects
DEFAULT_COMPARISON_ARGS = {
    "shallow-comparison": False,
    "time-resolution": 100,
    "ignore_blank_lines": False,
    "apply-text-filters": False,
}


def make_name_filters(
    specs: Sequence[Tuple[str, bool, str]] = tuple(DEFAULT_NAME_FILTERS),
) -> List[FilterEntry]:
    return [
        FilterEntry.new_from_gsetting(spec, FilterEntry.SHELL)
        for spec in specs
    ]


class DirEntry:
    """One comparison row: the same-named item across all panes."""

    __slots__ = ("names", "paths", "exists", "isdir", "state", "children",
                 "error")

    def __init__(
        self,
        names: Tuple[str, ...],
        paths: Tuple[str, ...],
        exists: Tuple[bool, ...],
        isdir: bool,
        state: int,
        error: Optional[str] = None,
    ) -> None:
        self.names = names
        self.paths = paths
        self.exists = exists
        self.isdir = isdir
        self.state = state
        self.children: List["DirEntry"] = []
        self.error = error

    def pane_state(self, pane: int) -> int:
        """The state painted in a given pane (upstream _update_item_state:
        present panes share the row state, absent panes strike through)."""
        return self.state if self.exists[pane] else STATE_NONEXIST

    @property
    def different(self) -> bool:
        # upstream: differences drive auto-expansion and chunk-style nav
        return self.state not in (STATE_NORMAL, STATE_NOCHANGE)

    def walk(self) -> Iterator["DirEntry"]:
        for child in self.children:
            yield child
            yield from child.walk()


class DirComparison:
    """Scan N root directories into a DirEntry tree.

    scan_iter() yields progress strings so the UI can run it in a
    worker and stay responsive (mirrors _search_recursively_iter's
    generator shape); scan() runs it to completion.
    """

    def __init__(
        self,
        roots: Sequence[str],
        name_filters: Optional[Sequence[FilterEntry]] = None,
        text_filters: Sequence[FilterEntry] = (),
        comparison_args: Optional[dict] = None,
        options: Optional[ComparisonOptions] = None,
        ignore_symlinks: bool = False,
    ) -> None:
        assert len(roots) in (2, 3)
        self.roots = [os.path.abspath(r) for r in roots]
        for root in self.roots:
            if not os.path.isdir(root):
                raise NotADirectoryError(
                    errno.ENOTDIR, os.strerror(errno.ENOTDIR), root
                )
        self.name_filters = (
            list(name_filters) if name_filters is not None
            else make_name_filters()
        )
        self.text_filters = list(text_filters)
        self.comparison_args = dict(
            comparison_args if comparison_args is not None
            else DEFAULT_COMPARISON_ARGS
        )
        self.options = options or ComparisonOptions()
        self.ignore_symlinks = ignore_symlinks
        self.root_entry: Optional[DirEntry] = None

    @property
    def num_panes(self) -> int:
        return len(self.roots)

    def file_compare(self, files: Sequence[str]) -> int:
        regexes = [f.byte_filter for f in self.text_filters if f.active]
        return _files_same(files, regexes, self.comparison_args)

    def scan(self) -> DirEntry:
        for _progress in self.scan_iter():
            pass
        return self.root_entry

    def scan_iter(self) -> Iterator[str]:
        names = tuple(os.path.basename(r) or r for r in self.roots)
        root = DirEntry(
            names,
            tuple(self.roots),
            tuple(True for _ in self.roots),
            isdir=True,
            state=STATE_NORMAL,
        )
        symlinks_followed = set()
        todo = [root]
        while todo:
            entry = todo.pop()
            yield entry.paths[0]
            todo.extend(reversed(self._scan_dir(entry, symlinks_followed)))
        self.root_entry = root

    def _scan_dir(
        self, entry: DirEntry, symlinks_followed: set
    ) -> List[DirEntry]:
        """Populate one directory row's children; returns child dirs
        still to scan. Port of the _search_recursively_iter loop body."""
        roots = entry.paths
        dirs = CanonicalListing(self.num_panes, self.options)
        files = CanonicalListing(self.num_panes, self.options)
        errors: List[Tuple[int, str]] = []

        for pane, root in enumerate(roots):
            if not entry.exists[pane] or not os.path.isdir(root):
                continue

            try:
                entries = os.listdir(root)
            except OSError as err:
                errors.append((pane, err.strerror))
                continue

            for f in self.name_filters:
                if not f.active or f.filter is None:
                    continue
                entries = [e for e in entries if f.filter.match(e) is None]

            for e in entries:
                try:
                    s = os.lstat(os.path.join(root, e))
                # Covers certain unreadable symlink cases; see bgo#585895
                except OSError as err:
                    errors.append((pane, e + err.strerror))
                    continue

                if stat.S_ISLNK(s.st_mode):
                    if self.ignore_symlinks:
                        continue
                    key = (s.st_dev, s.st_ino)
                    if key in symlinks_followed:
                        continue
                    symlinks_followed.add(key)
                    try:
                        s = os.stat(os.path.join(root, e))
                        if stat.S_ISREG(s.st_mode):
                            files.add(pane, e)
                        elif stat.S_ISDIR(s.st_mode):
                            dirs.add(pane, e)
                    except OSError as err:
                        if err.errno == errno.ENOENT:
                            error_string = e + ": Dangling symlink"
                        else:
                            error_string = e + err.strerror
                        errors.append((pane, error_string))
                elif stat.S_ISREG(s.st_mode):
                    files.add(pane, e)
                elif stat.S_ISDIR(s.st_mode):
                    dirs.add(pane, e)
                else:
                    # FIXME: Unhandled stat type
                    pass

        for pane, message in errors:
            error_entry = DirEntry(
                tuple(message for _ in roots),
                tuple("" for _ in roots),
                tuple(i == pane for i in range(self.num_panes)),
                isdir=False,
                state=STATE_ERROR,
                error=message,
            )
            entry.children.append(error_entry)

        child_dirs = []
        for child_names in dirs.get():
            child = self._make_entry(roots, child_names)
            entry.children.append(child)
            child_dirs.append(child)
        for child_names in files.get():
            entry.children.append(self._make_entry(roots, child_names))
        return child_dirs

    def _make_entry(
        self, roots: Sequence[str], names: Tuple[str, ...]
    ) -> DirEntry:
        paths = tuple(os.path.join(r, n) for r, n in zip(roots, names))
        return self._entry_state(names, paths)

    def _entry_state(
        self, names: Tuple[str, ...], paths: Tuple[str, ...]
    ) -> DirEntry:
        """Port of _update_item_state's state decision (dirdiff.py:1848)."""

        def none_stat(f):
            try:
                return os.stat(f)
            except OSError:
                return None

        stats = [none_stat(f) for f in paths]
        exists = tuple(s is not None for s in stats)

        if all(stats):
            same = self.file_compare(paths)
            all_present_same = same
        else:
            present = [f for f, s in zip(paths, stats) if s]
            same = Different
            all_present_same = self.file_compare(present)

        # TODO: Differentiate the DodgySame case (upstream's comment)
        if same in (Same, DodgySame):
            state = STATE_NORMAL
        elif same == SameFiltered:
            state = STATE_NOCHANGE
        elif all_present_same in (Same, SameFiltered, DodgySame):
            state = STATE_NEW
        elif same == FileError or all_present_same == FileError:
            state = STATE_ERROR
        # Different and DodgyDifferent
        else:
            state = STATE_MODIFIED

        isdir = any(
            s is not None and stat.S_ISDIR(s.st_mode) for s in stats
        )
        return DirEntry(names, paths, exists, isdir, state)
