from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SKILL_REGISTRY_VERSION = "shujuan-skill-registry-v11.0"


@dataclass(frozen=True)
class SkillSpec:
    name: str
    version: str
    required: bool
    asset_path: str
    description: str
    compatibility: str = "required"

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RoleProfileSpec:
    name: str
    role: str
    version: str
    required: bool
    asset_path: str
    compatibility: str
    description: str

    def payload(self) -> dict[str, Any]:
        return asdict(self)


REQUIRED_SKILLS: tuple[SkillSpec, ...] = (
    SkillSpec("shujuan-harness", "11.0", True, "skills/shujuan-harness", "First 90 seconds route/mode/endpoint/role selection."),
    SkillSpec("shujuan-recall", "11.0", True, "skills/shujuan-recall", "History, rationale, lineage, why, and evidence-bounded retrieval."),
    SkillSpec("shujuan-capture", "11.0", True, "skills/shujuan-capture", "Provenance capture without task/check/closure inference."),
    SkillSpec("shujuan-execute", "11.0", True, "skills/shujuan-execute", "Scoped implementation with runtime gate, tests, and material handoff."),
    SkillSpec("shujuan-delegate", "11.0", True, "skills/shujuan-delegate", "Role-bounded worker/reviewer/provider packet and return material lane."),
    SkillSpec("shujuan-close", "11.0", True, "skills/shujuan-close", "Controller-only evidence adoption and closeout gates."),
    SkillSpec("shujuan-evolve", "11.0", True, "skills/shujuan-evolve", "Shujuan ontology, policy, skill, hook, installer, package evolution."),
    SkillSpec("shujuan-core", "10-compat", True, "skills/shujuan-core", "Explicit v10 compatibility shim; not ordinary primary routing.", "compatibility_shim"),
)


REQUIRED_ROLE_PROFILES: tuple[RoleProfileSpec, ...] = (
    RoleProfileSpec("shujuan-controller.toml", "controller_agent", "11.0", True, "agents/shujuan-controller.toml", "required", "Controller governance and closeout authority profile."),
    RoleProfileSpec("shujuan-worker.toml", "worker_agent", "11.0", True, "agents/shujuan-worker.toml", "required", "Scoped implementation material return profile."),
    RoleProfileSpec("shujuan-reviewer.toml", "reviewer_agent", "11.0", True, "agents/shujuan-reviewer.toml", "required", "Read-only independent review profile."),
    RoleProfileSpec("shujuan-researcher.toml", "researcher_agent", "11.0", True, "agents/shujuan-researcher.toml", "required", "Source-backed fact gathering profile."),
    RoleProfileSpec("shujuan-writer.toml", "writer_agent", "11.0", True, "agents/shujuan-writer.toml", "required", "Drafting and prose material profile."),
)
ROLE_PROFILE_NAMES = tuple(spec.name for spec in REQUIRED_ROLE_PROFILES)


def skill_specs() -> tuple[SkillSpec, ...]:
    return REQUIRED_SKILLS


def role_profile_specs() -> tuple[RoleProfileSpec, ...]:
    return REQUIRED_ROLE_PROFILES


def registry_payload() -> dict[str, Any]:
    return {
        "version": SKILL_REGISTRY_VERSION,
        "skills": [spec.payload() for spec in REQUIRED_SKILLS],
        "role_profiles": [spec.payload() for spec in REQUIRED_ROLE_PROFILES],
    }


def skill_target_dir(repo: Path, spec: SkillSpec) -> Path:
    return repo / ".agents" / "skills" / spec.name


def role_target_dir(repo: Path) -> Path:
    return repo / ".codex" / "agents"
