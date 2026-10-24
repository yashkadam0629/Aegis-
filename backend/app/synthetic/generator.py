"""Deterministic synthetic transaction + scenario generator.

All data produced here is SYNTHETIC. Scenario labels describe *simulated* patterns and are
not legally established financial crime. Ground truth is returned separately from the
inference-facing tables and must never be used as a model input feature.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

SCENARIO_TYPES = {
    "A": "layered_chain",
    "B": "circular_flow",
    "C": "shared_beneficiary",
    "D": "coordinated_group",
    "E": "transaction_burst",
    "F": "fan_in_out",
    "G": "dormant_reactivation",
    "H": "structuring_like",
    "I": "benign_lookalike",
    "J": "combined_pattern",
}
BENIGN_SUBTYPES = ["payroll", "marketplace_settlement", "group_bill", "business_recurring", "seasonal_commercial"]
CHANNELS = ["UPI", "IMPS", "NEFT", "CARD", "WEB"]


@dataclass
class GeneratorConfig:
    seed: int = 42
    n_accounts: int = 2000
    n_days: int = 120
    n_background_txns: int = 70000
    start_date: str = "2026-01-01"
    activity_sigma: float = 1.1          # dispersion of per-account activity levels
    favorites_per_account: int = 6       # network density (stable counterparties)
    favorite_share: float = 0.7          # fraction of txns going to a favourite counterparty
    new_account_share: float = 0.08      # accounts opened during the period
    dormant_share: float = 0.03
    scenarios_per_type: dict = field(default_factory=lambda: {"A": 12, "B": 12, "C": 12, "D": 10, "E": 12, "F": 12, "G": 10, "H": 12, "I": 30, "J": 8})
    n_variants: int = 4                  # scenario template variants (used for hold-out evaluation)
    evasion_level: float = 0.0           # 0..1: stretches timing / leaks value to evade fixed-threshold heuristics
    missing_rate: float = 0.0015
    duplicate_rate: float = 0.002
    out_of_order_rate: float = 0.01
    structuring_threshold: float = 50000.0

    @staticmethod
    def quick() -> GeneratorConfig:
        return GeneratorConfig(n_accounts=600, n_days=60, n_background_txns=12000,
                               scenarios_per_type={"A": 4, "B": 4, "C": 4, "D": 3, "E": 4, "F": 4, "G": 3, "H": 4, "I": 10, "J": 3})

    @staticmethod
    def sized(n_txns: int, seed: int = 42) -> GeneratorConfig:
        """Benchmark helper: scale accounts / scenarios roughly linearly with transaction count."""
        f = n_txns / 70000
        return GeneratorConfig(seed=seed, n_accounts=max(300, int(2000 * f)), n_background_txns=n_txns,
                               n_days=120 if n_txns >= 50000 else 90,
                               scenarios_per_type={k: max(2, int(v * f)) for k, v in GeneratorConfig().scenarios_per_type.items()})


@dataclass
class SyntheticDataset:
    accounts: pd.DataFrame
    transactions: pd.DataFrame          # inference-facing (may contain injected data-quality issues)
    scenarios: list[dict]               # hidden ground truth (scenario level)
    txn_labels: pd.DataFrame            # hidden ground truth (transaction level)
    config: GeneratorConfig

    def save(self, out_dir: str | Path) -> dict:
        out = Path(out_dir)
        (out / "ground_truth").mkdir(parents=True, exist_ok=True)
        self.accounts.to_csv(out / "accounts.csv", index=False)
        self.transactions.to_csv(out / "transactions.csv", index=False)
        self.txn_labels.to_csv(out / "ground_truth" / "txn_labels.csv", index=False)
        (out / "ground_truth" / "scenarios.json").write_text(json.dumps(self.scenarios, indent=1, default=str))
        (out / "generator_config.json").write_text(json.dumps(asdict(self.config), indent=1))
        (out / "README.txt").write_text(
            "SYNTHETIC DATA. Scenario labels describe simulated patterns, not established financial crime.\n"
            "ground_truth/ must not be used as model input.\n")
        return {"dir": str(out), "n_accounts": len(self.accounts), "n_transactions": len(self.transactions), "n_scenarios": len(self.scenarios)}


class _Ctx:
    """Shared state for scenario builders."""

    def __init__(self, cfg: GeneratorConfig, rng: np.random.Generator, accounts: pd.DataFrame, t0: pd.Timestamp):
        self.cfg, self.rng, self.t0 = cfg, rng, t0
        self.acc = accounts
        self.created = accounts.set_index("account_id")["created_at"].to_dict()
        self.type_of = accounts.set_index("account_id")["account_type"].to_dict()
        self.dormant = set(accounts.loc[accounts["_dormant"], "account_id"])
        self.t_end = t0 + pd.Timedelta(days=cfg.n_days)
        ids = accounts["account_id"].to_numpy()
        types = accounts["account_type"].to_numpy()
        self.regular = ids[(types == "individual") | (types == "business")]
        self.regular = np.array([a for a in self.regular if a not in self.dormant])
        self.in_period = np.array([a for a in self.regular if self.created[a] > t0])
        self.merchants = ids[types == "merchant"]
        self.benef = ids[types == "beneficiary"]
        self.businesses = ids[types == "business"]
        self.individuals = ids[types == "individual"]
        self.aggregators = ids[types == "payment_aggregator"]
        self.used: set[str] = set()

    def pick(self, n: int, start: pd.Timestamp, new_share: float = 0.3, pool: np.ndarray | None = None, exclude=()) -> list[str]:
        pool = self.regular if pool is None else pool
        cutoff = start - pd.Timedelta(days=1)
        excl = set(exclude)
        pool_set = set(pool)
        old = [a for a in pool if self.created[a] < cutoff and a not in excl]
        newp = [a for a in self.in_period if self.created[a] < cutoff and a not in excl and a in pool_set]
        out: list[str] = []
        for _ in range(n):
            use_new = newp and self.rng.random() < new_share
            src = newp if use_new else old
            for _try in range(20):
                a = src[int(self.rng.integers(len(src)))]
                if a not in out:
                    out.append(a)
                    break
        return out

    def amt(self, lo, hi) -> float:
        return float(np.round(np.exp(self.rng.uniform(np.log(lo), np.log(hi))), 2))

    def ts(self, base: pd.Timestamp, hours: float) -> pd.Timestamp:
        return base + pd.Timedelta(hours=float(hours))

    def profile(self, variant: int) -> tuple[float, float]:
        """(gap scale hours, value retention per hop) for a template variant, stretched by evasion level."""
        gaps = [0.5, 1.5, 4.0, 9.0]
        keep = [0.985, 0.955, 0.925, 0.90]
        v = variant % 4
        ev = self.cfg.evasion_level
        return gaps[v] * (1 + 4 * ev), max(0.5, keep[v] - 0.12 * ev)


def _chain(ctx: _Ctx, accts: list[str], start: pd.Timestamp, variant: int, amount: float, types=("P2P_TRANSFER",)):
    gap, keep = ctx.profile(variant)
    t, a, rows = start, amount, []
    for u, v in zip(accts[:-1], accts[1:]):
        rows.append((u, v, t, round(a, 2), str(types[0]), str(ctx.rng.choice(["IMPS", "UPI", "NEFT"]))))
        t = ctx.ts(t, ctx.rng.lognormal(np.log(gap), 0.4))
        a *= ctx.rng.uniform(keep - 0.02, min(0.999, keep + 0.01))
    return rows


def _sc_A(ctx, start, variant):
    L = int(ctx.rng.integers(4, 8))
    accts = ctx.pick(L, start)
    return _chain(ctx, accts, start, variant, ctx.amt(60000, 400000)), accts, "layered_chain"


def _sc_B(ctx, start, variant):
    L = int(ctx.rng.integers(3, 6))
    accts = ctx.pick(L, start)
    rows = _chain(ctx, accts + [accts[0]], start, variant, ctx.amt(50000, 300000))
    return rows, accts, "circular_flow"


def _sc_C(ctx, start, variant):
    k = int(ctx.rng.integers(6, 16))
    senders = ctx.pick(k, start, new_share=0.2)
    dest = ctx.pick(1, start, pool=np.concatenate([ctx.benef, ctx.individuals]), exclude=senders)[0]
    span = [12, 24, 36, 48][variant % 4] * (1 + 2 * ctx.cfg.evasion_level)
    rows = []
    for s in senders:
        for _ in range(int(ctx.rng.integers(1, 3))):
            rows.append((s, dest, ctx.ts(start, ctx.rng.uniform(0, span)), ctx.amt(8000, 40000), "P2P_TRANSFER", str(ctx.rng.choice(["UPI", "IMPS"]))))
    return rows, senders + [dest], "shared_beneficiary"


def _sc_D(ctx, start, variant):
    k = int(ctx.rng.integers(5, 10))
    members = ctx.pick(k, start, new_share=0.4)
    shared = ctx.pick(int(ctx.rng.integers(2, 4)), start, pool=np.concatenate([ctx.benef, ctx.individuals]), exclude=members)
    rounds = int(ctx.rng.integers(3, 5))
    rows = []
    for r in range(rounds):
        base = ctx.ts(start, r * 30 * (1 + ctx.cfg.evasion_level * 2))
        win = [3, 6, 8, 10][variant % 4]
        for m in members:
            if ctx.rng.random() < 0.85:
                rows.append((m, str(ctx.rng.choice(shared)), ctx.ts(base, ctx.rng.uniform(0, win)), ctx.amt(5000, 30000), "P2P_TRANSFER", "UPI"))
    return rows, members + shared, "coordinated_group"


def _sc_E(ctx, start, variant):
    acct = ctx.pick(1, start, new_share=0.0)[0]
    n = int(ctx.rng.integers(25, 60))
    dests = ctx.pick(min(n, 25), start, pool=ctx.regular, exclude=[acct])
    rows = []
    span = 6 * (1 + 2 * ctx.cfg.evasion_level)
    for _ in range(n):
        rows.append((acct, str(ctx.rng.choice(dests)), ctx.ts(start, ctx.rng.uniform(0, span)), ctx.amt(4000, 60000), "P2P_TRANSFER", "UPI"))
    return rows, [acct] + dests, "transaction_burst"


def _sc_F(ctx, start, variant):
    k = int(ctx.rng.integers(15, 41))
    hub = ctx.pick(1, start, new_share=0.3)[0]
    others = ctx.pick(k, start, exclude=[hub], new_share=0.2)
    fan_in = ctx.rng.random() < 0.6
    span = [8, 16, 30, 48][variant % 4] * (1 + ctx.cfg.evasion_level * 2)
    rows = []
    for o in others:
        t = ctx.ts(start, ctx.rng.uniform(0, span))
        a = ctx.amt(3000, 25000)
        rows.append((o, hub, t, a, "P2P_TRANSFER", "UPI") if fan_in else (hub, o, t, a, "P2P_TRANSFER", "IMPS"))
    return rows, [hub] + others, "fan_in" if fan_in else "fan_out"


def _sc_G(ctx, start, variant):
    pool = [a for a in ctx.dormant if ctx.created[a] < start - pd.Timedelta(days=45)]
    if not pool:
        return [], [], "dormant_reactivation"
    acct = pool[int(ctx.rng.integers(len(pool)))]
    n = int(ctx.rng.integers(10, 26))
    dests = ctx.pick(10, start, exclude=[acct], new_share=0.1)
    span = [10, 20, 36, 48][variant % 4] * (1 + ctx.cfg.evasion_level)
    rows = [(acct, str(ctx.rng.choice(dests)), ctx.ts(start, ctx.rng.uniform(0, span)), ctx.amt(15000, 120000), "P2P_TRANSFER", "IMPS") for _ in range(n)]
    return rows, [acct] + dests, "dormant_reactivation"


def _sc_H(ctx, start, variant):
    acct = ctx.pick(1, start, new_share=0.2)[0]
    n = int(ctx.rng.integers(5, 11))
    dests = ctx.pick(int(ctx.rng.integers(1, 4)), start, exclude=[acct])
    thr = ctx.cfg.structuring_threshold
    days = [2, 3, 5, 7][variant % 4] * (1 + ctx.cfg.evasion_level)
    rows = []
    for _ in range(n):
        lo = 0.86 - 0.1 * ctx.cfg.evasion_level
        rows.append((acct, str(ctx.rng.choice(dests)), ctx.ts(start, ctx.rng.uniform(0, days * 24)), round(thr * ctx.rng.uniform(lo, 0.995), 2), "BUSINESS_TRANSFER" if ctx.type_of[acct] == "business" else "P2P_TRANSFER", "NEFT"))
    return rows, [acct] + dests, "structuring_like"


def _sc_J(ctx, start, variant):
    """Weak signals overlap: dormant account inside a short chain feeding a shared beneficiary."""
    gap, keep = ctx.profile(variant)
    pool = [a for a in ctx.dormant if ctx.created[a] < start - pd.Timedelta(days=45)]
    mid = pool[int(ctx.rng.integers(len(pool)))] if pool else ctx.pick(1, start)[0]
    k = int(ctx.rng.integers(4, 8))
    feeders = ctx.pick(k, start, exclude=[mid], new_share=0.4)
    dest = ctx.pick(1, start, pool=np.concatenate([ctx.benef, ctx.individuals]), exclude=feeders + [mid])[0]
    rows = []
    for f in feeders:
        rows.append((f, mid, ctx.ts(start, ctx.rng.uniform(0, 10)), ctx.amt(15000, 45000), "P2P_TRANSFER", "UPI"))
    inflow = sum(r[3] for r in rows)
    t = ctx.ts(start, 12 + gap)
    for j in range(3):
        rows.append((mid, dest, ctx.ts(t, j * gap), round(inflow / 3 * ctx.rng.uniform(keep - 0.03, keep), 2), "P2P_TRANSFER", "IMPS"))
    return rows, feeders + [mid, dest], "combined_pattern"


def _benign(ctx, start, variant):
    sub = BENIGN_SUBTYPES[int(ctx.rng.integers(len(BENIGN_SUBTYPES)))]
    rows: list = []
    if sub == "payroll":
        employer = ctx.pick(1, start, pool=ctx.businesses, new_share=0.0)[0]
        emp = ctx.pick(int(ctx.rng.integers(15, 50)), start, pool=ctx.individuals, new_share=0.0, exclude=[employer])
        base = float(ctx.rng.uniform(18000, 90000))
        for e in emp:
            rows.append((employer, e, ctx.ts(start, ctx.rng.uniform(0, 2)), round(base * ctx.rng.uniform(0.8, 1.3), 2), "BUSINESS_TRANSFER", "NEFT"))
        accts = [employer] + emp
    elif sub == "marketplace_settlement":
        mk = ctx.pick(1, start, pool=ctx.aggregators if len(ctx.aggregators) else ctx.merchants, new_share=0.0)[0]
        sellers = ctx.pick(int(ctx.rng.integers(5, 12)), start, pool=ctx.merchants, new_share=0.0, exclude=[mk])
        custs = ctx.pick(int(ctx.rng.integers(20, 50)), start, pool=ctx.individuals, new_share=0.0)
        total = 0.0
        for c in custs:
            a = ctx.amt(300, 8000)
            total += a
            rows.append((c, mk, ctx.ts(start, ctx.rng.uniform(0, 20)), a, "MERCHANT_PAYMENT", "UPI"))
        for s in sellers:
            rows.append((mk, s, ctx.ts(start, 24 + ctx.rng.uniform(0, 4)), round(total / len(sellers) * ctx.rng.uniform(0.5, 1.4) * 0.97, 2), "MERCHANT_SETTLEMENT", "NEFT"))
        accts = [mk] + sellers + custs
    elif sub == "group_bill":
        org = ctx.pick(1, start, pool=ctx.individuals, new_share=0.0)[0]
        friends = ctx.pick(int(ctx.rng.integers(4, 9)), start, pool=ctx.individuals, new_share=0.0, exclude=[org])
        share = ctx.amt(500, 3500)
        for f in friends:
            rows.append((f, org, ctx.ts(start, ctx.rng.uniform(0, 20)), round(share * ctx.rng.uniform(0.98, 1.02), 2), "P2P_TRANSFER", "UPI"))
        m = ctx.pick(1, start, pool=ctx.merchants, new_share=0.0)[0]
        rows.append((org, m, ctx.ts(start, 22), round(share * len(friends), 2), "MERCHANT_PAYMENT", "CARD"))
        accts = [org, m] + friends
    elif sub == "business_recurring":
        b = ctx.pick(3, start, pool=ctx.businesses, new_share=0.0)
        amt = ctx.amt(80000, 300000)
        for w in range(int(ctx.rng.integers(3, 6))):
            t = ctx.ts(start, w * 24 * 7)
            rows.append((b[0], b[1], t, round(amt * ctx.rng.uniform(0.99, 1.01), 2), "BUSINESS_TRANSFER", "NEFT"))
            rows.append((b[1], b[2], ctx.ts(t, 72 + ctx.rng.uniform(0, 24)), round(amt * 0.9 * ctx.rng.uniform(0.99, 1.01), 2), "BUSINESS_TRANSFER", "NEFT"))
        accts = b
    else:
        m = ctx.pick(1, start, pool=ctx.merchants, new_share=0.0)[0]
        custs = ctx.pick(int(ctx.rng.integers(30, 70)), start, pool=ctx.individuals, new_share=0.0)
        for c in custs:
            rows.append((c, m, ctx.ts(start, ctx.rng.uniform(0, 72)), ctx.amt(500, 12000), "MERCHANT_PAYMENT", str(ctx.rng.choice(["UPI", "CARD"]))))
        accts = [m] + custs
    return rows, accts, "benign_" + sub


_BUILDERS = {"A": _sc_A, "B": _sc_B, "C": _sc_C, "D": _sc_D, "E": _sc_E, "F": _sc_F, "G": _sc_G, "H": _sc_H, "I": _benign, "J": _sc_J}
_EXPECTED = {"A": ["layered_chain"], "B": ["cycle"], "C": ["shared_beneficiary"], "D": ["coordinated_group", "shared_beneficiary"], "E": ["burst"],
             "F": ["fan_in_out"], "G": ["dormant_reactivation"], "H": ["structuring_like"], "I": [], "J": ["layered_chain", "shared_beneficiary", "dormant_reactivation"]}


def _build_accounts(cfg: GeneratorConfig, rng: np.random.Generator, t0: pd.Timestamp) -> pd.DataFrame:
    n = cfg.n_accounts
    shares = {"individual": 0.80, "business": 0.10, "merchant": 0.06, "payment_aggregator": 0.005, "beneficiary": 0.035}
    types = rng.choice(list(shares), size=n, p=np.array(list(shares.values())) / sum(shares.values()))
    for i, t in enumerate(["payment_aggregator"] * 3):  # guarantee a few aggregators
        types[i] = t
    created = np.array([t0 - pd.Timedelta(days=int(rng.integers(30, 900))) for _ in range(n)], dtype="datetime64[ns]")
    new = rng.random(n) < cfg.new_account_share
    for i in np.where(new)[0]:
        created[i] = np.datetime64(t0 + pd.Timedelta(days=int(rng.integers(1, max(2, int(cfg.n_days * 0.7))))))
    dormant = (rng.random(n) < cfg.dormant_share) & (types == "individual") & ~new
    seg = np.where(types == "business", rng.choice(["retail", "services", "wholesale", "logistics"], n), "consumer")
    return pd.DataFrame({
        "account_id": [f"AC{i:06d}" for i in range(n)], "account_type": types, "created_at": pd.to_datetime(created),
        "account_status": "active", "customer_segment": seg, "_dormant": dormant})


def generate(cfg: GeneratorConfig | None = None) -> SyntheticDataset:
    cfg = cfg or GeneratorConfig()
    rng = np.random.default_rng(cfg.seed)
    t0 = pd.Timestamp(cfg.start_date)
    t_end = t0 + pd.Timedelta(days=cfg.n_days)
    acc = _build_accounts(cfg, rng, t0)
    n = len(acc)
    ids = acc["account_id"].to_numpy()
    types = acc["account_type"].to_numpy()
    created = acc["created_at"].to_numpy()

    # ---- background behaviour -------------------------------------------------------------
    act = rng.lognormal(0, cfg.activity_sigma, n)
    act[types == "merchant"] *= 0.3            # merchants mostly receive
    act[types == "payment_aggregator"] *= 8
    act[types == "beneficiary"] *= 0.1
    act[acc["_dormant"].to_numpy()] *= 1.5
    p_src = act / act.sum()
    attract = rng.lognormal(0, 1.0, n)
    attract[types == "merchant"] *= 6
    attract[types == "payment_aggregator"] *= 30
    attract[types == "beneficiary"] *= 2
    p_dst = attract / attract.sum()
    F = cfg.favorites_per_account
    fav = rng.choice(n, size=(n, F), p=p_dst)

    N = cfg.n_background_txns
    src = rng.choice(n, size=N, p=p_src)
    use_fav = rng.random(N) < cfg.favorite_share
    dst = np.where(use_fav, fav[src, rng.integers(0, F, N)], rng.choice(n, size=N, p=p_dst))
    clash = dst == src
    dst[clash] = (dst[clash] + 1) % n

    day_w = np.array([0.8 if (t0 + pd.Timedelta(days=d)).dayofweek >= 5 else 1.0 for d in range(cfg.n_days)])
    day_w *= np.array([1.25 if (t0 + pd.Timedelta(days=d)).day >= 28 or (t0 + pd.Timedelta(days=d)).day <= 2 else 1.0 for d in range(cfg.n_days)])
    day = rng.choice(cfg.n_days, size=N, p=day_w / day_w.sum())
    hour_w = np.array([1, .6, .4, .3, .3, .5, 1, 2, 3, 4, 4.5, 5, 5, 4.5, 4, 4, 4.5, 5, 5.5, 5, 4, 3, 2, 1.4])
    hour = rng.choice(24, size=N, p=hour_w / hour_w.sum())
    secs = day * 86400 + hour * 3600 + rng.integers(0, 3600, N)
    ts = t0.value // 10**9 + secs
    created_s = created.astype("datetime64[s]").astype("int64")
    dorm = acc["_dormant"].to_numpy()
    ts = np.where(dorm[src], t0.value // 10**9 + rng.integers(0, 20 * 86400, N), ts)
    lo = np.maximum(created_s[src], created_s[dst]) + 3600
    # transactions before an account exists are re-sampled uniformly after creation
    late = ts < lo
    span = np.maximum(t_end.value // 10**9 - lo, 3600)
    ts = np.where(late, lo + (rng.random(N) * np.minimum(span, 30 * 86400)).astype(int) * 1, ts)
    ts = np.minimum(ts, t_end.value // 10**9 - 1)

    st, dt = types[src], types[dst]
    ttype = np.select(
        [dt == "merchant", (st == "business") & (dt != "merchant"), dt == "payment_aggregator", rng.random(N) < 0.08],
        ["MERCHANT_PAYMENT", "BUSINESS_TRANSFER", "BILL_PAYMENT", "BILL_PAYMENT"], default="P2P_TRANSFER")
    med = {"MERCHANT_PAYMENT": 700, "BUSINESS_TRANSFER": 22000, "BILL_PAYMENT": 1800, "P2P_TRANSFER": 1500}
    sig = {"MERCHANT_PAYMENT": 1.0, "BUSINESS_TRANSFER": 1.1, "BILL_PAYMENT": 0.8, "P2P_TRANSFER": 1.2}
    acct_scale = np.exp(rng.normal(0, 0.5, n))  # account-specific spending level
    amount = np.array([rng.lognormal(np.log(med[t]), sig[t]) for t in ttype]) * acct_scale[src]
    amount = np.round(np.clip(amount, 10, 490000), 2)
    channel = np.where(ttype == "MERCHANT_PAYMENT", rng.choice(["UPI", "CARD"], N, p=[.7, .3]), rng.choice(CHANNELS, N, p=[.5, .2, .15, .05, .1]))
    bg = pd.DataFrame({"source_account_id": ids[src], "destination_account_id": ids[dst],
                       "transaction_timestamp": pd.to_datetime(ts, unit="s"), "amount": amount,
                       "transaction_type": ttype, "channel": channel})
    bg["scenario_id"] = ""
    bg["is_suspicious_sim"] = False

    # ---- scenarios ------------------------------------------------------------------------
    ctx = _Ctx(cfg, rng, acc, t0)
    scen_meta: list[dict] = []
    scen_rows: list[pd.DataFrame] = []
    sid = 0
    for letter, count in cfg.scenarios_per_type.items():
        for k in range(count):
            variant = k % cfg.n_variants
            lo_d, hi_d = 10, max(11, cfg.n_days - 8)
            # variants are stratified over time so every chronological split contains every template
            start = t0 + pd.Timedelta(days=float(rng.uniform(lo_d, hi_d)), hours=float(rng.uniform(0, 24)))
            rows, accts, subtype = _BUILDERS[letter](ctx, start, variant)
            if not rows:
                continue
            sid += 1
            scen_id = f"S{sid:04d}"
            df = pd.DataFrame(rows, columns=["source_account_id", "destination_account_id", "transaction_timestamp", "amount", "transaction_type", "channel"])
            df = df[df["transaction_timestamp"] < t_end]
            df["scenario_id"] = scen_id
            suspicious = letter != "I"
            df["is_suspicious_sim"] = suspicious
            scen_rows.append(df)
            scen_meta.append({"scenario_id": scen_id, "scenario_type": SCENARIO_TYPES[letter], "scenario_code": letter, "subtype": subtype,
                              "template_variant": variant, "is_suspicious": suspicious, "accounts": sorted(set(accts)),
                              "start_time": df["transaction_timestamp"].min(), "end_time": df["transaction_timestamp"].max(),
                              "n_transactions": len(df), "expected_detectors": _EXPECTED[letter]})
    txns = pd.concat([bg] + scen_rows, ignore_index=True).sort_values("transaction_timestamp", kind="stable").reset_index(drop=True)
    txns["transaction_id"] = [f"TX{i:08d}" for i in range(len(txns))]
    txns["currency"] = "INR"
    txns["transaction_status"] = "completed"
    for s in scen_meta:  # attach txn ids for evaluation
        s["transaction_ids"] = txns.loc[txns["scenario_id"] == s["scenario_id"], "transaction_id"].tolist()
    labels = txns[["transaction_id", "scenario_id", "is_suspicious_sim"]].copy()
    value_cols = ["transaction_id", "source_account_id", "destination_account_id", "transaction_timestamp", "amount", "currency", "transaction_type", "channel", "transaction_status"]
    out = txns[value_cols].copy()

    # ---- data quality problems (background rows only so labels stay valid) ----------------
    bgmask = (txns["scenario_id"] == "").to_numpy()
    cand = np.where(bgmask)[0]
    n_missing = int(len(cand) * cfg.missing_rate)
    out["transaction_timestamp"] = out["transaction_timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out["amount"] = out["amount"].astype(object)
    for i in rng.choice(cand, n_missing, replace=False):
        col = rng.choice(["amount", "destination_account_id", "transaction_timestamp", "currency"])
        out.at[i, col] = None
    for i in rng.choice(cand, max(1, n_missing // 2), replace=False):
        out.at[i, "amount"] = -abs(float(out.at[i, "amount"] or 100))
    for i in rng.choice(cand, max(1, n_missing // 3), replace=False):
        out.at[i, "currency"] = "XXX"
    dups = out.iloc[rng.choice(len(out), int(len(out) * cfg.duplicate_rate), replace=False)]
    out = pd.concat([out, dups], ignore_index=True)
    k = int(len(out) * cfg.out_of_order_rate)
    if k > 1:
        idx = rng.choice(len(out), k, replace=False)
        out.iloc[np.sort(idx)] = out.iloc[idx].to_numpy()

    accounts = acc.drop(columns=["_dormant"]).copy()
    accounts["created_at"] = accounts["created_at"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return SyntheticDataset(accounts=accounts, transactions=out, scenarios=scen_meta, txn_labels=labels, config=cfg)
