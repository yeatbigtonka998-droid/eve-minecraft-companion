"""Skill registry: load, hash, validate params, intersect with the body's caps."""
from pathlib import Path

from jsonschema import Draft202012Validator

from .config import sha256, strict_loads

EFFECTS = {"move", "break", "place", "attack", "use_item", "container", "craft", "chat", "hazard"}


class Registry:
    def __init__(self, path: Path):
        self.raw = strict_loads(Path(path).read_text("utf-8"))
        self.version = self.raw["registry_version"]
        self.hash = sha256(self.raw)
        self.skills = {s["name"]: s for s in self.raw["skills"]}
        self._validators = {}
        for name, s in self.skills.items():
            schema = {"$defs": self.raw["$defs"], **s["params"]}
            Draft202012Validator.check_schema(schema)
            assert set(s["effects"]) <= EFFECTS, f"{name}: unknown effect"
            self._validators[name] = Draft202012Validator(schema)

    def validate(self, name: str, params) -> list[str]:
        if name not in self.skills:
            return [f"unknown skill {name!r}"]
        return [f"{'/'.join(map(str, e.path)) or '<params>'}: {e.message}"
                for e in self._validators[name].iter_errors(params)]

    def dispatchable(self, caps) -> dict:
        """Registry ∩ caps: a skill is dispatchable only when the body reports the same version."""
        have = {(c.get("skill"), c.get("version")) for c in caps or [] if isinstance(c, dict)}
        return {n: s for n, s in self.skills.items() if (n, s["version"]) in have}
