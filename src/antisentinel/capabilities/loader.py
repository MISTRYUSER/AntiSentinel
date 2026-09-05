"""Hash-checked progressive loading for one immutable published package."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import errno
import hashlib
from typing import Callable

from .package import PublishedPackage


class SkillLoadError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LoadedReference:
    skill_id: str
    reference_id: str
    content: str
    content_hash: str
    release_id: str


@dataclass(frozen=True)
class ReferenceDescriptor:
    reference_id: str
    description: str


@dataclass(frozen=True)
class LoadedSkill:
    skill_id: str
    version: str
    instruction: str
    instruction_hash: str
    release_id: str
    references: tuple[ReferenceDescriptor, ...]


class ResourceCache:
    def __init__(self, max_bytes: int = 16 * 1024 * 1024) -> None:
        self.max_bytes = max_bytes
        self._values: OrderedDict[tuple[str, str], tuple[str, int]] = OrderedDict()
        self._size = 0
        self.load_count = 0

    def get_or_load(self, key: tuple[str, str], loader: Callable[[], str]) -> str:
        if key in self._values:
            value, size = self._values.pop(key)
            self._values[key] = (value, size)
            return value
        value = loader()
        size = len(value.encode("utf-8"))
        if size > self.max_bytes:
            raise SkillLoadError("resource_too_large", "resource exceeds cache limit")
        while self._values and self._size + size > self.max_bytes:
            _, (_, removed) = self._values.popitem(last=False)
            self._size -= removed
        self._values[key] = (value, size)
        self._size += size
        self.load_count += 1
        return value


class SkillLoader:
    def __init__(self, package: PublishedPackage, *, cache: ResourceCache | None = None,
                 instruction_char_budget: int = 8192, reference_char_budget: int = 16384) -> None:
        self.package = package
        self.cache = cache or ResourceCache()
        self.instruction_char_budget = instruction_char_budget
        self.reference_char_budget = reference_char_budget

    def load(self, skill_id: str) -> LoadedSkill:
        skill = next((item for item in self.package.plugin.skills if item.skill_id == skill_id), None)
        if skill is None:
            raise SkillLoadError("unknown_skill", f"skill is not published: {skill_id}")
        content = self._read(skill.instructions_path)
        if len(content) > self.instruction_char_budget:
            raise SkillLoadError("context_budget_exceeded", "skill instructions exceed context budget")
        return LoadedSkill(skill_id=skill.skill_id, version=skill.version, instruction=content,
                           instruction_hash=self.package.resource_hashes[skill.instructions_path], release_id=self.package.release_id,
                           references=tuple(ReferenceDescriptor(item.reference_id, item.description) for item in skill.references))

    def read_reference(self, skill_id: str, reference_id: str) -> LoadedReference:
        skill = next((item for item in self.package.plugin.skills if item.skill_id == skill_id), None)
        if skill is None:
            raise SkillLoadError("unknown_skill", f"skill is not published: {skill_id}")
        reference = next((item for item in skill.references if item.reference_id == reference_id), None)
        if reference is None:
            raise SkillLoadError("unknown_reference", f"reference is not registered: {reference_id}")
        content = self._read(reference.path)
        if len(content) > self.reference_char_budget:
            raise SkillLoadError("context_budget_exceeded", "reference exceeds context budget")
        return LoadedReference(skill_id=skill_id, reference_id=reference_id, content=content,
                               content_hash=self.package.resource_hashes[reference.path], release_id=self.package.release_id)

    def _read(self, relative_path: str) -> str:
        expected = self.package.resource_hashes.get(relative_path)
        if expected is None:
            raise SkillLoadError("snapshot_mismatch", "resource is absent from release lock")
        key = (self.package.release_id, expected)

        def read() -> str:
            retries = 0
            while True:
                try:
                    data = (self.package.root / relative_path).read_bytes()
                    break
                except OSError as exc:
                    if exc.errno in {errno.EAGAIN, errno.EINTR} and retries < 2:
                        retries += 1
                        continue
                    code = "resource_missing" if isinstance(exc, FileNotFoundError) else "resource_read_failed"
                    raise SkillLoadError(code, str(exc)) from exc
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SkillLoadError("invalid_manifest", "published resource is not UTF-8") from exc
            if hashlib.sha256(data).hexdigest() != expected:
                raise SkillLoadError("snapshot_mismatch", "published resource hash changed")
            return content

        return self.cache.get_or_load(key, read)
