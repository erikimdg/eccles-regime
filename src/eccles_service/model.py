"""Portable frozen model bundle, exact distances and assigned-class NEON/LRP."""
import hashlib
import json
import re
from pathlib import Path

import numpy as np

from .features import OPERATORS

VERSION = re.compile(r"^[1-9][0-9]*\.[0-9]+$")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def percentile(sorted_values, value):
    # Midrank empirical CDF, in percent; no extrapolation beyond [0, 100].
    return float((np.searchsorted(sorted_values, value, side="left") +
                  np.searchsorted(sorted_values, value, side="right")) * 50 / len(sorted_values))


class Bundle:
    def __init__(self, directory):
        self.path = Path(directory)
        if self.path.is_symlink():
            raise ValueError("Model directory must not be a symlink")
        manifest_path = self.path / "manifest.json"
        if manifest_path.is_symlink() or manifest_path.stat().st_size > 100_000:
            raise ValueError("Invalid manifest")
        self.manifest = json.loads(manifest_path.read_text())
        if self.manifest.get("schema_version") != 1:
            raise ValueError("Unsupported bundle schema")
        for name in ("model.json", "reference.npz", "golden.json"):
            p = self.path / name
            if p.is_symlink() or p.stat().st_size > 16_000_000:
                raise ValueError(f"Invalid bundle file: {name}")
            if digest(p) != self.manifest["files"].get(name):
                raise ValueError(f"Bundle checksum failed: {name}")
        self.hash = digest(manifest_path)
        self.spec = json.loads((self.path / "model.json").read_text())
        self.version = self.spec["model_version"]
        if not VERSION.fullmatch(self.version) or self.path.name != self.version:
            raise ValueError("Invalid model version or directory")
        if self.spec.get("engine_schema") != 1:
            raise ValueError("Unsupported feature engine")
        features = self.spec["features"]
        self.names = [f["id"] for f in features]
        if len(self.names) != len(set(self.names)) or not 1 <= len(features) <= 256:
            raise ValueError("Invalid feature names or count")
        sources = self.spec["sources"]
        source_ids = [s["id"] for s in sources]
        if len(source_ids) != len(set(source_ids)) or self.spec["anchor_source"] not in source_ids:
            raise ValueError("Invalid sources")
        for f in features:
            if f["operator"] not in OPERATORS or not set(f["sources"]) <= set(source_ids):
                raise ValueError("Unsupported feature recipe")
            arity = len(f["sources"])
            valid_arity = arity >= 2 if f["operator"] == "pc1_share" else arity == (2 if f["operator"] in {"spread", "correlation"} else 1)
            if not valid_arity or not 1 <= f.get("window", 1) <= 252 or not 1 <= f.get("rank_window", 1) <= 504:
                raise ValueError("Invalid feature window or source count")
        for s in sources:
            if s["provider"] not in {"fmp", "yahoo", "cboe", "stoxx", "msci"}:
                raise ValueError("Unsupported original source provider")
            if s["calendar"] not in {"XNYS", "XLON", "published"}:
                raise ValueError("Unsupported source calendar")
        self.mean = np.asarray(self.spec["mean"], dtype=float)
        self.scale = np.asarray(self.spec["scale"], dtype=float)
        self.weights = np.asarray(self.spec["weights"], dtype=float)
        self.centers = np.asarray(self.spec["centers"], dtype=float)
        n = len(features)
        if any(a.shape != (n,) for a in [self.mean, self.scale, self.weights]):
            raise ValueError("Invalid transform shape")
        if self.centers.shape != (3, n) or len(self.spec["classes"]) != 3:
            raise ValueError("Expected three frozen classes")
        if any(not np.isfinite(a).all() for a in [self.mean, self.scale, self.weights, self.centers]):
            raise ValueError("Nonfinite model parameters")
        if (self.scale <= 0).any() or (self.weights <= 0).any():
            raise ValueError("Invalid transform scales")
        self.beta = float(self.spec["neon_beta"])
        if not np.isfinite(self.beta) or self.beta <= 0:
            raise ValueError("Invalid NEON beta")
        with np.load(self.path / "reference.npz", allow_pickle=False) as r:
            self.reference = {k: r[k].copy() for k in r.files}
        for k, a in self.reference.items():
            if not np.isfinite(a).all() or not a.size:
                raise ValueError(f"Invalid reference: {k}")
        if self.reference["features"].shape != (self.spec["training"]["n"], n):
            raise ValueError("Invalid training reference shape")
        if (np.diff(self.reference["features"], axis=0) < 0).any():
            raise ValueError("Unsorted training features")
        for k in range(3):
            for field in [f"margin_C{k}", f"distance_C{k}"]:
                a = self.reference[field]
                if a.ndim != 1 or (np.diff(a) < 0).any():
                    raise ValueError("Invalid class reference")
        self.validate_golden()

    def infer(self, x):
        x = np.asarray(x, dtype=float)
        if x.ndim != 2 or x.shape[1] != len(self.names) or not np.isfinite(x).all():
            raise ValueError("A complete finite feature row is required")
        z = ((x - self.mean) / self.scale) * self.weights
        d = ((z[:, None, :] - self.centers[None, :, :]) ** 2).sum(axis=2)
        order = np.argsort(d, axis=1, kind="stable")
        state, runner = order[:, 0], order[:, 1]
        rr = np.arange(len(x))
        margin = d[rr, runner] - d[rr, state]
        relevance = np.full_like(z, np.nan)
        allocations = np.full((len(x), 2), np.nan)
        competitors = np.empty((len(x), 2), dtype=int)
        pair_margins = np.full((len(x), 2), np.nan)
        for k in range(3):
            ids = np.where(state == k)[0]
            others = np.array([j for j in range(3) if j != k])
            competitors[ids] = others
            a = (z[ids, None, :] - (self.centers[k] + self.centers[others])[None, :, :] / 2) * (2 * (self.centers[k] - self.centers[others]))[None, :, :]
            h = a.sum(axis=2)
            m = h.min(axis=1)
            pair_margins[ids] = h
            strict = (m > 0) & (margin[ids] > 0)
            if strict.any():
                p = np.exp(-self.beta * (h[strict] - m[strict, None]))
                p /= p.sum(axis=1, keepdims=True)
                relevance[ids[strict]] = (a[strict] * (p * m[strict, None] / h[strict])[:, :, None]).sum(axis=1)
                allocations[ids[strict]] = p
        return dict(z=z, distances=d, state=state, runner=runner, margin=margin,
                    relevance=relevance, allocations=allocations, competitors=competitors,
                    pair_margins=pair_margins)

    def validate_golden(self):
        g = json.loads((self.path / "golden.json").read_text())
        out = self.infer(g["inputs"])
        if not np.array_equal(out["state"], g["states"]):
            raise ValueError("Golden assignments differ")
        for key in ("distances", "relevance"):
            if not np.allclose(out[key], g[key], atol=1e-9, rtol=1e-9):
                raise ValueError(f"Golden {key} differ")

    def describe(self, values, raw_values=None):
        values = np.asarray(values, dtype=float)
        out = self.infer(values[None, :])
        k, runner = int(out["state"][0]), int(out["runner"][0])
        d, m, r = out["distances"][0], float(out["margin"][0]), out["relevance"][0]
        strict = bool(np.isfinite(r).all())
        pct = percentile(self.reference[f"margin_C{k}"], m)
        labels = self.spec["classes"]
        rows = []
        for i, feature in enumerate(self.spec["features"]):
            ref = self.reference["features"][:, i]
            raw = raw_values.get(feature["id"]) if raw_values else None
            rows.append(dict(feature=feature["id"], label=feature["label"], value=float(values[i]),
                             raw_value=float(raw) if raw is not None and np.isfinite(raw) else None,
                             raw_unit=feature["raw_unit"], training_percentile=percentile(ref, values[i]),
                             relevance=float(r[i]) if strict else None,
                             outside_training_range=bool(values[i] < ref[0] or values[i] > ref[-1])))
        positive = sorted([f for f in rows if f["relevance"] is not None and f["relevance"] > 0], key=lambda f: -f["relevance"])[:5]
        negative = sorted([f for f in rows if f["relevance"] is not None and f["relevance"] < 0], key=lambda f: f["relevance"])[:5]
        assigned, closest = f"C{k}", f"C{runner}"
        return dict(regime=assigned, label=labels[k]["label"], label_ko=labels[k]["label_ko"],
                    closest_competitor=closest, competitor_label=labels[runner]["label"],
                    squared_distances={f"C{j}": float(d[j]) for j in range(3)},
                    winning_margin=m, normalized_gap=m / float(d[k] + d[runner]) if d[k] + d[runner] else 0,
                    margin_training_percentile=pct, distance_training_percentile=percentile(self.reference[f"distance_C{k}"], d[k]),
                    boundary_flag=pct <= 20, atypical_distance=bool(d[k] > self.spec["distance_q99"]),
                    outside_training_feature_count=sum(f["outside_training_range"] for f in rows),
                    neon=dict(status="explained" if strict else "tie_or_numeric_boundary", beta=self.beta,
                              target=assigned, relevance_sum=float(r.sum()) if strict else None,
                              conservation_error=float(r.sum() - m) if strict else None,
                              competitor_allocation={f"C{int(c)}": float(p) if strict else None for c, p in zip(out["competitors"][0], out["allocations"][0])},
                              positive=positive, negative=negative, features=rows),
                    interpretation=interpretation(labels, k, runner, pct, positive, negative, rows))


def interpretation(labels, k, runner, pct, positive, negative, rows):
    p = ", ".join(f["label"] for f in positive[:3]) or "none"
    n = ", ".join(f["label"] for f in negative[:3]) or "none"
    directional = [f for f in rows if f["feature"].endswith("absolute_return21") and f["raw_value"] is not None]
    up, total = sum(f["raw_value"] > 0 for f in directional), len(directional)
    return dict(
        en=f"C{k}: {labels[k]['label']}. The closest alternative is C{runner}: {labels[runner]['label']}. The winning margin is at the {pct:.1f}th percentile among training C{k} days. Positive NEON contributions: {p}. Opposing contributions: {n}.",
        ko=f"C{k}: {labels[k]['label_ko']}. 가장 가까운 대안은 C{runner}: {labels[runner]['label_ko']}입니다. 판단 여유는 training C{k}일 중 {pct:.1f} percentile입니다. NEON에서 지지하는 feature는 {p}, 반대하는 feature는 {n}입니다.",
        selected_msci_21d_positive=up if total else None, selected_msci_21d_total=total,
        participation_basis="Cyclical, Defensive, Japan, EM, EMU; overlapping indices, positive 21-session price returns",
        definitions={"neon": "Signed contribution to the assigned-class minimum squared-distance advantage, using both competitors",
                     "percentile": "Empirical midrank against frozen 2001-2022 references",
                     "margin": "Runner-up squared distance minus assigned squared distance",
                     "competitor_allocation": "LRP allocation weights, not regime probabilities"})
