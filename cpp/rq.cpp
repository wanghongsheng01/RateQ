// RateQ core in C++: exact rule evaluator, brute-force baselines and RQ_base (index build,
// single query = best-first threshold algorithm, batch = round-robin threshold algorithm).
// Semantics mirror code/rateq/model.py (eval_service), code/rateq/index.py (cost1, brute1,
// RQIndex.build/_hull/query1/query_batch). Single thread.
//
//   rq verify <inst.rqi> <inst.idx> <par> <tru> [<par> <tru> ...]
//   rq e1     <inst.rqi> <par>     -> per-query latency (single shot) for B1, B1s, RQ_q1
//   rq e2     <inst.rqi> <par> <reps> -> batch seconds for B1, B1s, B1v, RQ_batch, RQ_q1loop
// Output: one JSON object per line on stdout.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <unordered_map>
#include <vector>
#ifdef __APPLE__
#include <mach/mach_time.h>
#include <pthread.h>
#include <sys/qos.h>
#else
#include <time.h>
#endif

typedef int64_t i64;
typedef __int128 i128;
static const i64 INF = (i64)1 << 60;
enum { F_L, F_M, F_S, F_G, F_V, F_A, F_T, F_DAS, F_RES, F_ZIP, F_ZONE, F_DAY, F_MONTH, F_SUMD, NF };

// ------------------------------------------------------------------ binary reader
struct Reader {
    FILE* f;
    explicit Reader(const char* path, const char* magic) {
        f = fopen(path, "rb");
        if (!f) { fprintf(stderr, "cannot open %s\n", path); exit(2); }
        char m[4];
        if (fread(m, 1, 4, f) != 4 || memcmp(m, magic, 4) != 0) { fprintf(stderr, "bad magic %s\n", path); exit(2); }
    }
    ~Reader() { fclose(f); }
    i64 i() { i64 x; if (fread(&x, 8, 1, f) != 1) { fprintf(stderr, "short read\n"); exit(2); } return x; }
    double d() { double x; if (fread(&x, 8, 1, f) != 1) { fprintf(stderr, "short read\n"); exit(2); } return x; }
    std::vector<i64> ai() { i64 n = i(); std::vector<i64> v(n); if (n && fread(v.data(), 8, n, f) != (size_t)n) exit(2); return v; }
    std::vector<double> ad() { i64 n = i(); std::vector<double> v(n); if (n && fread(v.data(), 8, n, f) != (size_t)n) exit(2); return v; }
};

// ------------------------------------------------------------------ predicates
struct Cond {
    int feat, op;  // op: 0 >, 1 >=, 2 <, 3 <=, 4 in, 5 notin, 6 ranges
    double v = 0;
    std::vector<double> set, lo, hi;
};
static inline bool in_set(const std::vector<double>& s, double x) {
    for (double y : s) if (x == y) return true;
    return false;
}
static inline bool cond1(const Cond& c, double x) {
    switch (c.op) {
        case 0: return x > c.v;
        case 1: return x >= c.v;
        case 2: return x < c.v;
        case 3: return x <= c.v;
        case 4: return in_set(c.set, x);
        case 5: return !in_set(c.set, x);
        default: {
            i64 i = (i64)(std::upper_bound(c.lo.begin(), c.lo.end(), x) - c.lo.begin()) - 1;
            return i >= 0 && x <= c.hi[i];
        }
    }
}
struct Pred {
    bool present = false;
    std::vector<std::vector<Cond>> cl;
    bool eval(const double* f) const {
        for (const auto& c : cl) {
            bool ok = true;
            for (const auto& x : c) if (!cond1(x, f[x.feat])) { ok = false; break; }
            if (ok) return true;
        }
        return false;
    }
    // column version over a block: out[j] = pred(F[:, j])
    void eval_col(const double* const* F, int B, unsigned char* out) const {
        for (int j = 0; j < B; j++) out[j] = 0;
        unsigned char m[1024];
        for (const auto& c : cl) {
            for (int j = 0; j < B; j++) m[j] = 1;
            for (const auto& x : c) {
                const double* col = F[x.feat];
                if (x.op == 0) { double th = x.v; for (int j = 0; j < B; j++) m[j] &= (col[j] > th); }
                else if (x.op == 1) { double th = x.v; for (int j = 0; j < B; j++) m[j] &= (col[j] >= th); }
                else if (x.op == 2) { double th = x.v; for (int j = 0; j < B; j++) m[j] &= (col[j] < th); }
                else if (x.op == 3) { double th = x.v; for (int j = 0; j < B; j++) m[j] &= (col[j] <= th); }
                else for (int j = 0; j < B; j++) m[j] &= cond1(x, col[j]);
            }
            for (int j = 0; j < B; j++) out[j] |= m[j];
        }
    }
};
static Pred read_pred(Reader& r) {
    Pred p;
    i64 nc = r.i();
    if (nc < 0) return p;
    p.present = true;
    p.cl.resize(nc);
    for (i64 a = 0; a < nc; a++) {
        i64 nk = r.i();
        p.cl[a].resize(nk);
        for (i64 b = 0; b < nk; b++) {
            Cond& c = p.cl[a][b];
            c.feat = (int)r.i(); c.op = (int)r.i();
            if (c.op == 4 || c.op == 5) c.set = r.ad();
            else if (c.op == 6) { c.lo = r.ad(); c.hi = r.ad(); }
            else c.v = r.d();
        }
    }
    return p;
}

// ------------------------------------------------------------------ instance
struct Sur { Pred p; std::vector<i64> price; i64 group; int fuel, kind, gk; double coef; };
struct Svc {
    int sid, fc, rounding, extrap, carry, mode;
    double limit, D, thr, q, omin2, omin, bmax;
    int zpool, zzpool, bpool, dspool, dlpool;     // pool ids
    const i64* zstart; int nz; const i64* zzone;
    const double* breaks; int K;
    std::vector<i64> price; int Z;
    const i64* das_s = nullptr; const i64* das_l = nullptr; int ndas = 0;
    Pred os2, os, inel;
    std::vector<Sur> sur;
    int ngk = 0;
    std::vector<int> gkfuel;
    int bkey = 0;  // billable-weight key (same billable function => same id)
};
struct FuelClass { i64 lo = 0; std::vector<i64> tbl; };
struct Inst {
    std::vector<std::vector<i64>> pi;
    std::vector<std::vector<double>> pd;
    std::vector<int> pkind;
    std::vector<Svc> s;
    std::vector<FuelClass> fuel;   // indexed by fuel class; empty tbl = absent (bp 0)
    i64 D0; std::vector<i64> month;
    int nbkey = 0;
    inline i64 bp(int fc, i64 day) const {
        if (fc < 0 || fc >= (int)fuel.size() || fuel[fc].tbl.empty()) return 0;
        const FuelClass& F = fuel[fc];
        i64 o = day - F.lo;
        if (o < 0) o = 0;
        if (o >= (i64)F.tbl.size()) o = (i64)F.tbl.size() - 1;
        return F.tbl[o];
    }
    inline i64 month_of(i64 day) const {
        i64 o = day - D0;
        if (o < 0) o = 0;
        if (o >= (i64)month.size()) o = (i64)month.size() - 1;
        return month[o];
    }
};
static Inst load_inst(const char* path) {
    Reader r(path, "RQI1");
    Inst I;
    i64 np_ = r.i();
    I.pi.resize(np_); I.pd.resize(np_); I.pkind.resize(np_);
    for (i64 k = 0; k < np_; k++) {
        I.pkind[k] = (int)r.i();
        if (I.pkind[k] == 0) I.pi[k] = r.ai(); else I.pd[k] = r.ad();
    }
    i64 ns = r.i();
    I.s.resize(ns);
    for (i64 k = 0; k < ns; k++) {
        Svc& c = I.s[k];
        c.sid = (int)r.i(); c.fc = (int)r.i(); c.rounding = (int)r.i(); c.extrap = (int)r.i();
        c.limit = r.d(); c.D = r.d(); c.thr = r.d(); c.q = r.d();
        c.carry = (int)r.i(); c.mode = (int)r.i();
        c.zpool = (int)r.i(); c.zzpool = (int)r.i(); c.bpool = (int)r.i(); c.dspool = (int)r.i(); c.dlpool = (int)r.i();
        if (c.sid != k) { fprintf(stderr, "sid order\n"); exit(2); }
        c.Z = (int)r.i(); c.K = (int)r.i(); c.price = r.ai();
        c.os2 = read_pred(r); c.omin2 = r.d();
        c.os = read_pred(r); c.omin = r.d();
        c.inel = read_pred(r);
        i64 nsur = r.i();
        std::map<std::pair<i64, int>, int> gk;
        for (i64 a = 0; a < nsur; a++) {
            Sur x; x.p = read_pred(r); x.price = r.ai(); x.group = r.i(); x.fuel = (int)r.i(); x.kind = (int)r.i(); x.coef = r.d();
            auto key = std::make_pair(x.group, x.fuel);
            auto it = gk.find(key);
            if (it == gk.end()) { int id = (int)gk.size(); gk[key] = id; x.gk = id; } else x.gk = it->second;
            c.sur.push_back(std::move(x));
        }
        c.ngk = (int)gk.size();
        c.gkfuel.assign(c.ngk, 0);
        for (auto& kv : gk) c.gkfuel[kv.second] = kv.first.second;
    }
    i64 nfc = r.i();
    for (i64 a = 0; a < nfc; a++) {
        i64 fc = r.i(); auto st = r.ai(); auto bp = r.ai();
        if (fc >= (i64)I.fuel.size()) I.fuel.resize(fc + 1);
        FuelClass& F = I.fuel[fc];
        F.lo = st.front();
        i64 hi = st.back();
        F.tbl.resize(hi - F.lo + 1);
        for (i64 d = F.lo; d <= hi; d++) {
            i64 i = (i64)(std::upper_bound(st.begin(), st.end(), d) - st.begin()) - 1;
            F.tbl[d - F.lo] = bp[std::max<i64>(i, 0)];
        }
    }
    I.D0 = r.i(); I.month = r.ai();
    // resolve pointers (after all vectors are final)
    std::vector<std::vector<double>> bkeys;
    for (auto& c : I.s) {
        c.zstart = I.pi[c.zpool].data(); c.nz = (int)I.pi[c.zpool].size(); c.zzone = I.pi[c.zzpool].data();
        if (I.pkind[c.bpool] == 1) c.breaks = I.pd[c.bpool].data();
        else { fprintf(stderr, "breaks must be float\n"); exit(2); }
        if ((int)I.pd[c.bpool].size() != c.K) { fprintf(stderr, "K mismatch\n"); exit(2); }
        c.bmax = c.breaks[c.K - 1];
        if (c.dspool >= 0) { c.das_s = I.pi[c.dspool].data(); c.das_l = I.pi[c.dlpool].data(); c.ndas = (int)I.pi[c.dspool].size(); }
        std::vector<double> bk = {(double)c.rounding, c.D, c.thr, c.q, (double)c.carry, (double)c.mode};
        int id = -1;
        for (size_t a = 0; a < bkeys.size(); a++) if (bkeys[a] == bk) { id = (int)a; break; }
        if (id < 0) { id = (int)bkeys.size(); bkeys.push_back(bk); }
        c.bkey = id;
    }
    I.nbkey = (int)bkeys.size();
    return I;
}

// ------------------------------------------------------------------ parcels
struct Parcels {
    std::vector<i64> key, day; std::vector<double> a, L, M, S, res;
    size_t n() const { return key.size(); }
};
static Parcels load_par(const char* path) {
    Reader r(path, "RQP1");
    Parcels P; r.i();
    P.key = r.ai(); P.a = r.ad(); P.L = r.ad(); P.M = r.ad(); P.S = r.ad(); P.res = r.ad(); P.day = r.ai();
    return P;
}
struct Truth { std::vector<i64> best, arg, b1, a1; bool has_b1 = false; };
static Truth load_tru(const char* path) {
    Reader r(path, "RQT1");
    Truth T; r.i(); T.best = r.ai(); T.arg = r.ai();
    if (r.i()) { T.has_b1 = true; T.b1 = r.ai(); T.a1 = r.ai(); }
    return T;
}

// per-query context: base features (computed inside every query)
struct Ctx { i64 key, day; double a, L, M, S, res, V, G, SUMD, zip, month; };
static inline Ctx make_ctx(const Inst& I, const Parcels& P, size_t i) {
    Ctx x;
    x.key = P.key[i]; x.day = P.day[i]; x.a = P.a[i]; x.L = P.L[i]; x.M = P.M[i]; x.S = P.S[i]; x.res = P.res[i];
    x.V = x.L * x.M * x.S;
    x.G = x.L + 2 * x.M + 2 * x.S;
    x.SUMD = x.L + x.M + x.S;
    x.zip = (double)(x.key % 100000);
    x.month = (double)I.month_of(x.day);
    return x;
}

// ------------------------------------------------------------------ exact evaluator (cost1)
static inline double billable1(const Svc& c, double a, double V) {
    if (c.rounding == 2) {  // erp
        double v = c.D > 0 ? V / c.D : 0.0;
        double x = c.mode == 1 ? std::max(a, v) : (c.mode == 2 ? a : v);
        if (c.carry == 0) return std::ceil(x / c.q - 1e-9) * c.q;
        return std::floor(x / c.q + 0.5) * c.q;
    }
    if (c.rounding == 1) {  // erp-style
        double aw = std::ceil(std::floor(a * 10 + 1e-9) / 10 - 1e-9);
        double vw0 = std::ceil(V / c.D * 1000 - 1e-9) / 1000;
        double vw = vw0 > 1 ? std::ceil(std::floor(vw0 * 10 + 1e-9) / 10 - 1e-9) : 1.0;
        if (!(V > c.thr)) vw = 0.0;
        return std::max(aw, vw);
    }
    double dim = V > c.thr ? V / c.D : 0.0;
    return std::max(a, dim);
}
static inline i64 floordiv(i128 n, i64 d) {
    i128 q = n / d;
    if ((n % d != 0) && ((n < 0) != (d < 0))) q -= 1;
    return (i64)q;
}
static inline i64 pyround(double x) { return (i64)std::nearbyint(x); }  // half-to-even, as Python round()

// z >= 0 (served); t = billable weight before minimum tiers
static inline i64 eval_core(const Inst& I, const Svc& c, const Ctx& x, i64 z, i64 das, double t) {
    double f[NF] = {x.L, x.M, x.S, x.G, x.V, x.a, t, (double)das, x.res, x.zip, (double)z, (double)x.day, x.month, x.SUMD};
    if (c.os2.present && c.os2.eval(f)) { t = std::max(t, c.omin2); f[F_T] = t; }
    if (c.os.present && c.os.eval(f)) { t = std::max(t, c.omin); f[F_T] = t; }
    if (c.inel.present && c.inel.eval(f)) return INF;
    bool over = false;
    if (c.extrap) over = t > c.limit;
    else if (t > c.bmax) return INF;
    int k = (int)(std::lower_bound(c.breaks, c.breaks + c.K, t - 1e-9) - c.breaks);
    if (k >= c.K) k = c.K - 1;
    const i64* row = c.price.data() + (size_t)z * c.K;
    i64 base = row[k];
    if (over) {
        i64 top = row[c.K - 1];
        if (top >= INF) return INF;
        base = (i64)std::ceil(std::min((double)top * t / c.bmax, 1e15) - 1e-6);
    }
    if (base >= INF) return INF;
    i64 sf = 0, snf = 0;
    double pct = 0.0;
    if (!c.sur.empty()) {
        i64 gmax[64]; bool gset[64];
        for (int g = 0; g < c.ngk; g++) gset[g] = false;
        for (const Sur& r : c.sur) {
            bool fired = r.p.eval(f);
            if (r.kind == 3) { if (fired) pct += r.coef; continue; }
            i64 amt;
            if (r.kind == 0) amt = fired ? r.price[z] : 0;
            else if (r.kind == 1) amt = fired ? pyround(r.coef * t) : 0;
            else amt = fired ? pyround(r.coef / 100.0 * (double)base) : 0;
            if (amt > (gset[r.gk] ? gmax[r.gk] : -1)) { gmax[r.gk] = amt; gset[r.gk] = true; }
        }
        for (int g = 0; g < c.ngk; g++) {
            if (!gset[g]) continue;
            if (c.gkfuel[g]) sf += gmax[g]; else snf += gmax[g];
        }
    }
    i64 bp = I.bp(c.fc, x.day);
    i64 cost = floordiv((i128)(base + sf) * (10000 + bp) + 5000, 10000) + snf;
    if (pct != 0.0) cost = cost + pyround((double)cost * pct / 100.0);
    return cost;
}
static inline i64 zone_idx(const Svc& c, i64 key) {
    return (i64)(std::upper_bound(c.zstart, c.zstart + c.nz, key) - c.zstart) - 1;
}
static inline i64 das_of(const Svc& c, i64 key) {
    if (!c.das_s) return 0;
    i64 j = (i64)(std::upper_bound(c.das_s, c.das_s + c.ndas, key) - c.das_s) - 1;
    return j >= 0 ? c.das_l[j] : 0;
}
// cost1(c, ..., t=None or t)
static inline i64 cost1(const Inst& I, const Svc& c, const Ctx& x, bool has_t, double t) {
    i64 i = zone_idx(c, x.key);
    if (i < 0) return INF;
    i64 z = c.zzone[i];
    if (z < 0) return INF;
    if (!has_t) t = billable1(c, x.a, x.V);
    i64 das = das_of(c, x.key);
    return eval_core(I, c, x, z, das, t);
}

// ------------------------------------------------------------------ B1: brute force (scalar, per service lookups = brute1)
struct Ans { i64 best, arg; };
static Ans brute1(const Inst& I, const Ctx& x) {
    i64 best = INF, arg = -1;
    for (const Svc& c : I.s) {
        i64 v = cost1(I, c, x, false, 0.0);
        if (v < best) { best = v; arg = c.sid; }
    }
    return {best, arg};
}

// B1s: brute force with lookups shared across services that share a zone chart / DAS list / billable function
struct Shared {
    std::vector<i64> zi, dl; std::vector<double> tb; std::vector<int> zst, dst, tst; int stamp = 0;
    explicit Shared(const Inst& I) : zi(I.pi.size()), dl(I.pi.size()), tb(I.nbkey), zst(I.pi.size(), 0), dst(I.pi.size(), 0), tst(I.nbkey, 0) {}
};
static Ans brute_shared(const Inst& I, const Ctx& x, Shared& sh) {
    int st = ++sh.stamp;
    i64 best = INF, arg = -1;
    for (const Svc& c : I.s) {
        if (sh.zst[c.zpool] != st) { sh.zst[c.zpool] = st; sh.zi[c.zpool] = zone_idx(c, x.key); }
        i64 i = sh.zi[c.zpool];
        if (i < 0) continue;
        i64 z = c.zzone[i];
        if (z < 0) continue;
        if (sh.tst[c.bkey] != st) { sh.tst[c.bkey] = st; sh.tb[c.bkey] = billable1(c, x.a, x.V); }
        i64 das = 0;
        if (c.das_s) {
            if (sh.dst[c.dspool] != st) { sh.dst[c.dspool] = st; sh.dl[c.dspool] = das_of(c, x.key); }
            das = sh.dl[c.dspool];
        }
        i64 v = eval_core(I, c, x, z, das, sh.tb[c.bkey]);
        if (v < best) { best = v; arg = c.sid; }
    }
    return {best, arg};
}

// ------------------------------------------------------------------ B1v: column-at-a-time brute (eval_service / brute_vec)
static const int BLK = 512;
static void brute_vec(const Inst& I, const Parcels& P, i64* best, i64* arg) {
    size_t N = P.n();
    std::vector<double> col[NF];
    for (int f = 0; f < NF; f++) col[f].resize(BLK);
    std::vector<std::vector<i64>> zi(I.pi.size()), dl(I.pi.size());
    std::vector<std::vector<double>> tb(I.nbkey);
    std::vector<std::vector<i64>> bpv(I.fuel.size() + 1);
    std::vector<i64> z(BLK), basev(BLK), sf(BLK), snf(BLK), cost(BLK);
    std::vector<double> t(BLK), pct(BLK);
    std::vector<unsigned char> elig(BLK), m(BLK), over(BLK);
    std::vector<std::vector<i64>> gmax(64, std::vector<i64>(BLK));
    std::vector<std::vector<unsigned char>> gset(64, std::vector<unsigned char>(BLK));
    for (size_t b0 = 0; b0 < N; b0 += BLK) {
        int B = (int)std::min<size_t>(BLK, N - b0);
        for (int j = 0; j < B; j++) {
            size_t i = b0 + j;
            double L = P.L[i], M = P.M[i], S = P.S[i];
            col[F_L][j] = L; col[F_M][j] = M; col[F_S][j] = S;
            col[F_G][j] = L + 2 * M + 2 * S; col[F_V][j] = L * M * S; col[F_A][j] = P.a[i]; col[F_RES][j] = P.res[i];
            col[F_ZIP][j] = (double)(P.key[i] % 100000); col[F_DAY][j] = (double)P.day[i];
            col[F_MONTH][j] = (double)I.month_of(P.day[i]); col[F_SUMD][j] = L + M + S;
            best[i] = INF; arg[i] = -1;
        }
        for (auto& v : zi) v.clear();
        for (auto& v : dl) v.clear();
        for (auto& v : tb) v.clear();
        for (auto& v : bpv) v.clear();
        const double* F[NF];
        for (int f = 0; f < NF; f++) F[f] = col[f].data();
        for (const Svc& c : I.s) {
            if (zi[c.zpool].empty()) { zi[c.zpool].resize(B); for (int j = 0; j < B; j++) zi[c.zpool][j] = zone_idx(c, P.key[b0 + j]); }
            if (tb[c.bkey].empty()) { tb[c.bkey].resize(B); for (int j = 0; j < B; j++) tb[c.bkey][j] = billable1(c, col[F_A][j], col[F_V][j]); }
            if (c.das_s && dl[c.dspool].empty()) { dl[c.dspool].resize(B); for (int j = 0; j < B; j++) dl[c.dspool][j] = das_of(c, P.key[b0 + j]); }
            int fcs = (c.fc >= 0 && c.fc < (int)I.fuel.size()) ? c.fc : (int)I.fuel.size();
            if (bpv[fcs].empty()) { bpv[fcs].resize(B); for (int j = 0; j < B; j++) bpv[fcs][j] = I.bp(c.fc, P.day[b0 + j]); }
            const i64* zz = zi[c.zpool].data();
            for (int j = 0; j < B; j++) { i64 ii = zz[j]; z[j] = ii < 0 ? -1 : c.zzone[ii]; elig[j] = z[j] >= 0; }
            const double* tt = tb[c.bkey].data();
            for (int j = 0; j < B; j++) t[j] = tt[j];
            col[F_T].assign(t.begin(), t.begin() + B);
            if (c.das_s) for (int j = 0; j < B; j++) col[F_DAS][j] = (double)dl[c.dspool][j];
            else for (int j = 0; j < B; j++) col[F_DAS][j] = 0.0;
            for (int j = 0; j < B; j++) col[F_ZONE][j] = (double)z[j];
            F[F_T] = col[F_T].data();
            if (c.os2.present) {
                c.os2.eval_col(F, B, m.data());
                double mn = c.omin2;
                for (int j = 0; j < B; j++) { double v = m[j] ? std::max(t[j], mn) : t[j]; t[j] = v; col[F_T][j] = v; }
            }
            if (c.os.present) {
                c.os.eval_col(F, B, m.data());
                double mn = c.omin;
                for (int j = 0; j < B; j++) { double v = m[j] ? std::max(t[j], mn) : t[j]; t[j] = v; col[F_T][j] = v; }
            }
            if (c.inel.present) { c.inel.eval_col(F, B, m.data()); for (int j = 0; j < B; j++) elig[j] &= !m[j]; }
            const double bmax = c.bmax;
            if (c.extrap) for (int j = 0; j < B; j++) over[j] = t[j] > c.limit;
            else for (int j = 0; j < B; j++) { over[j] = 0; elig[j] &= t[j] <= bmax; }
            for (int j = 0; j < B; j++) {
                int k = (int)(std::lower_bound(c.breaks, c.breaks + c.K, t[j] - 1e-9) - c.breaks);
                if (k > c.K - 1) k = c.K - 1;
                i64 zc = z[j] < 0 ? 0 : z[j];
                const i64* row = c.price.data() + (size_t)zc * c.K;
                i64 bse = row[k];
                if (over[j]) {
                    i64 top = row[c.K - 1];
                    bse = top < INF ? (i64)std::ceil(std::min((double)top * t[j] / bmax, 1e15) - 1e-6) : INF;
                }
                basev[j] = bse;
                elig[j] &= bse < INF;
                if (!elig[j]) basev[j] = 0;
            }
            for (int j = 0; j < B; j++) { sf[j] = 0; snf[j] = 0; pct[j] = 0.0; }
            if (!c.sur.empty()) {
                for (int g = 0; g < c.ngk; g++) for (int j = 0; j < B; j++) gset[g][j] = 0;
                for (const Sur& r : c.sur) {
                    r.p.eval_col(F, B, m.data());
                    if (r.kind == 3) { for (int j = 0; j < B; j++) if (m[j]) pct[j] += r.coef; continue; }
                    i64* gm = gmax[r.gk].data(); unsigned char* gs = gset[r.gk].data();
                    for (int j = 0; j < B; j++) {
                        i64 amt;
                        if (r.kind == 0) amt = m[j] ? r.price[z[j] < 0 ? 0 : z[j]] : 0;
                        else if (r.kind == 1) amt = m[j] ? pyround(r.coef * t[j]) : 0;
                        else amt = m[j] ? pyround(r.coef / 100.0 * (double)basev[j]) : 0;
                        if (amt > (gs[j] ? gm[j] : -1)) { gm[j] = amt; gs[j] = 1; }
                    }
                }
                for (int g = 0; g < c.ngk; g++) {
                    i64* acc = c.gkfuel[g] ? sf.data() : snf.data();
                    for (int j = 0; j < B; j++) if (gset[g][j]) acc[j] += gmax[g][j];
                }
            }
            const i64* bpp = bpv[fcs].data();
            for (int j = 0; j < B; j++) {
                i64 cst = floordiv((i128)(basev[j] + sf[j]) * (10000 + bpp[j]) + 5000, 10000) + snf[j];
                if (pct[j] != 0.0) cst = cst + pyround((double)cst * pct[j] / 100.0);
                cst = elig[j] ? cst : INF;
                size_t i = b0 + j;
                if (cst < best[i]) { best[i] = cst; arg[i] = c.sid; }
            }
        }
    }
}

// ------------------------------------------------------------------ RQ_base index
struct Group {
    std::vector<int> sids; std::vector<double> E; std::vector<int> loc;
    int nlz = 0, nE = 0, ng = 0, fc = 0, rep = 0;
    std::vector<int32_t> ORD; std::vector<i64> LB;  // [ls][e][r]
    std::vector<std::vector<i64>> lz;
};
struct Index {
    std::vector<i64> U; std::vector<int> sig_of; std::vector<std::vector<i64>> zvec;
    std::vector<Group> G;
};
static std::vector<i64> hull(const Svc& s, i64 z, const std::vector<double>& E) {
    size_t nE = E.size();
    std::vector<i64> lb(nE + 1, INF);
    if (z < 0) return lb;
    const i64* row = s.price.data() + (size_t)z * s.K;
    std::vector<i64> suf(s.K);
    i64 m = INF + 1;
    for (int k = s.K - 1; k >= 0; k--) { m = std::min(m, row[k]); suf[k] = m; }
    i64 top = row[s.K - 1];
    i64 tv = top < INF ? top : INF;
    for (size_t e = 0; e < nE; e++) {
        int k = (int)(std::lower_bound(s.breaks, s.breaks + s.K, E[e] - 1e-9) - s.breaks);
        if (k < s.K) lb[e] = suf[k];
        else if (s.extrap) lb[e] = tv;
    }
    if (s.extrap) {
        lb[nE] = tv;
        for (size_t e = 0; e < nE; e++) lb[e] = std::min(lb[e], tv);
    }
    return lb;
}
static Index build_index(const Inst& I) {
    Index X;
    size_t n = I.s.size();
    // signature partition (zones only)
    std::vector<int> pools;
    for (const Svc& c : I.s) pools.push_back(c.zpool);
    std::sort(pools.begin(), pools.end()); pools.erase(std::unique(pools.begin(), pools.end()), pools.end());
    std::vector<i64> U = {0};
    for (int p : pools) U.insert(U.end(), I.pi[p].begin(), I.pi[p].end());
    std::sort(U.begin(), U.end()); U.erase(std::unique(U.begin(), U.end()), U.end());
    // zone vector per elementary key interval, advancing a pointer per zone chart
    std::vector<i64> ptr(I.pi.size(), -1);
    std::vector<i64> prev, row(n);
    std::vector<i64> Uk; std::vector<std::vector<i64>> rows_kept;
    std::unordered_map<std::string, int> rid;
    std::vector<int> raw_sig;
    for (size_t u = 0; u < U.size(); u++) {
        for (int p : pools) {
            const auto& zs = I.pi[p];
            while (ptr[p] + 1 < (i64)zs.size() && zs[ptr[p] + 1] <= U[u]) ptr[p]++;
        }
        for (size_t k = 0; k < n; k++) { i64 i = ptr[I.s[k].zpool]; row[k] = i < 0 ? -1 : I.s[k].zzone[i]; }
        if (u > 0 && row == prev) continue;
        prev = row;
        Uk.push_back(U[u]);
        std::string key((const char*)row.data(), n * 8);
        auto it = rid.find(key);
        int id;
        if (it == rid.end()) { id = (int)rows_kept.size(); rid[key] = id; rows_kept.push_back(row); } else id = it->second;
        raw_sig.push_back(id);
    }
    // lexicographic signature ids (as np.unique(axis=0))
    std::vector<int> ordr(rows_kept.size());
    for (size_t a = 0; a < ordr.size(); a++) ordr[a] = (int)a;
    std::sort(ordr.begin(), ordr.end(), [&](int a, int b) { return rows_kept[a] < rows_kept[b]; });
    std::vector<int> rank(ordr.size());
    for (size_t a = 0; a < ordr.size(); a++) rank[ordr[a]] = (int)a;
    X.U = Uk;
    X.sig_of.resize(raw_sig.size());
    for (size_t a = 0; a < raw_sig.size(); a++) X.sig_of[a] = rank[raw_sig[a]];
    for (int a : ordr) X.zvec.push_back(rows_kept[a]);
    // groups by group_key = (divisor, dim_thr, rounding, fuel_class, erp_q, erp_carry, erp_mode), first-appearance order
    std::vector<std::vector<double>> keys;
    for (const Svc& c : I.s) {
        std::vector<double> k = {c.D, c.thr, (double)c.rounding, (double)c.fc, c.q, (double)c.carry, (double)c.mode};
        int gi = -1;
        for (size_t a = 0; a < keys.size(); a++) if (keys[a] == k) { gi = (int)a; break; }
        if (gi < 0) { gi = (int)keys.size(); keys.push_back(k); X.G.emplace_back(); }
        X.G[gi].sids.push_back(c.sid);
    }
    for (Group& g : X.G) {
        g.rep = g.sids[0]; g.fc = I.s[g.rep].fc; g.ng = (int)g.sids.size();
        for (int sid : g.sids) for (int k = 0; k < I.s[sid].K; k++) g.E.push_back(I.s[sid].breaks[k]);
        std::sort(g.E.begin(), g.E.end()); g.E.erase(std::unique(g.E.begin(), g.E.end()), g.E.end());
        g.nE = (int)g.E.size() + 1;
        // local signatures
        std::vector<std::vector<i64>> lrows(X.zvec.size());
        for (size_t sg = 0; sg < X.zvec.size(); sg++) for (int sid : g.sids) lrows[sg].push_back(X.zvec[sg][sid]);
        std::vector<std::vector<i64>> uniq = lrows;
        std::sort(uniq.begin(), uniq.end()); uniq.erase(std::unique(uniq.begin(), uniq.end()), uniq.end());
        g.lz = uniq; g.nlz = (int)uniq.size();
        g.loc.resize(X.zvec.size());
        for (size_t sg = 0; sg < X.zvec.size(); sg++) g.loc[sg] = (int)(std::lower_bound(uniq.begin(), uniq.end(), lrows[sg]) - uniq.begin());
        std::vector<i64> LB((size_t)g.nlz * g.nE * g.ng);
        for (int j = 0; j < g.ng; j++) {
            const Svc& s = I.s[g.sids[j]];
            std::map<i64, std::vector<i64>> cache;
            for (int ls = 0; ls < g.nlz; ls++) {
                i64 z = uniq[ls][j];
                auto it = cache.find(z);
                if (it == cache.end()) it = cache.emplace(z, hull(s, z, g.E)).first;
                for (int e = 0; e < g.nE; e++) LB[((size_t)ls * g.nE + e) * g.ng + j] = it->second[e];
            }
        }
        g.ORD.resize(LB.size()); g.LB.resize(LB.size());
        std::vector<int> o(g.ng);
        for (int ls = 0; ls < g.nlz; ls++) for (int e = 0; e < g.nE; e++) {
            size_t base = ((size_t)ls * g.nE + e) * g.ng;
            for (int j = 0; j < g.ng; j++) o[j] = j;
            std::sort(o.begin(), o.end(), [&](int a, int b) {
                if (LB[base + a] != LB[base + b]) return LB[base + a] < LB[base + b];
                return g.sids[a] < g.sids[b];
            });
            for (int j = 0; j < g.ng; j++) { g.ORD[base + j] = g.sids[o[j]]; g.LB[base + j] = LB[base + o[j]]; }
        }
    }
    return X;
}
static size_t index_bytes(const Index& X) {
    size_t b = X.U.size() * 8 + X.sig_of.size() * 4;
    for (const Group& g : X.G) b += g.ORD.size() * 4 + g.LB.size() * 8 + g.E.size() * 8 + g.loc.size() * 4;
    return b;
}
// compare with Python's exported RQIndex
static int check_index(const Index& X, const char* path) {
    Reader r(path, "RQX1");
    int bad = 0;
    auto U = r.ai(); auto sig = r.ai();
    if (U != X.U) { bad++; fprintf(stderr, "U differs (%zu vs %zu)\n", U.size(), X.U.size()); }
    if (sig.size() != X.sig_of.size()) bad++;
    else for (size_t a = 0; a < sig.size(); a++) if (sig[a] != X.sig_of[a]) { bad++; fprintf(stderr, "sig_of differs\n"); break; }
    i64 ng = r.i();
    if (ng != (i64)X.G.size()) { fprintf(stderr, "group count differs\n"); return bad + 1; }
    for (i64 gi = 0; gi < ng; gi++) {
        const Group& g = X.G[gi];
        auto sids = r.ai(); auto E = r.ad(); auto loc = r.ai();
        i64 a = r.i(), b = r.i(), c = r.i();
        auto ORD = r.ai(); auto LB = r.ai();
        if ((i64)g.sids.size() != (i64)sids.size() || !std::equal(sids.begin(), sids.end(), g.sids.begin())) { bad++; fprintf(stderr, "g%lld sids\n", gi); }
        if (E != g.E) { bad++; fprintf(stderr, "g%lld E\n", gi); }
        if (loc.size() != g.loc.size() || !std::equal(loc.begin(), loc.end(), g.loc.begin())) { bad++; fprintf(stderr, "g%lld loc\n", gi); }
        if (a != g.nlz || b != g.nE || c != g.ng) { bad++; fprintf(stderr, "g%lld shape\n", gi); continue; }
        if (!std::equal(ORD.begin(), ORD.end(), g.ORD.begin())) { bad++; fprintf(stderr, "g%lld ORD\n", gi); }
        if (LB != g.LB) { bad++; fprintf(stderr, "g%lld LB\n", gi); }
    }
    return bad;
}


// per-query DAS cache (DAS lists are shared by many services)
struct DasCache {
    int pool[8]; i64 val[8]; int n = 0;
    inline i64 get(const Svc& c, i64 key) {
        if (!c.das_s) return 0;
        for (int k = 0; k < n; k++) if (pool[k] == c.dspool) return val[k];
        i64 v = das_of(c, key);
        if (n < 8) { pool[n] = c.dspool; val[n] = v; n++; }
        return v;
    }
};
// exact cost of service sid when the parcel's signature sg is known: zone = zvec[sg][sid] (no zone search)
static inline i64 cost_sig(const Inst& I, const std::vector<i64>& zrow, const Svc& c, const Ctx& x, double t, DasCache& dc) {
    i64 z = zrow[c.sid];
    if (z < 0) return INF;
    return eval_core(I, c, x, z, dc.get(c, x.key), t);
}
static inline i64 scale(i64 lb, i64 bp) { return floordiv((i128)lb * (10000 + bp) + 5000, 10000); }

// RQ_base single query: best-first threshold algorithm (index.py RQIndex.query1)
struct Head { i64 val; size_t row; int p, gi; i64 bp; double t; bool live; };
static Ans rq_query1(const Inst& I, const Index& X, const Ctx& x, i64* nev_out) {
    i64 iu = (i64)(std::upper_bound(X.U.begin(), X.U.end(), x.key) - X.U.begin()) - 1;
    int sg = X.sig_of[iu];
    Head H[64]; int nh = 0;
    for (int gi = 0; gi < (int)X.G.size(); gi++) {
        const Group& g = X.G[gi];
        double t = billable1(I.s[g.rep], x.a, x.V);
        int e = (int)(std::lower_bound(g.E.begin(), g.E.end(), t - 1e-9) - g.E.begin());
        size_t row = ((size_t)g.loc[sg] * g.nE + e) * g.ng;
        i64 bp = I.bp(g.fc, x.day);
        i64 lb = g.LB[row];
        if (lb < INF) H[nh++] = {scale(lb, bp), row, 0, gi, bp, t, true};
    }
    i64 best = INF, arg = -1, nev = 0;
    int live = nh;
    const std::vector<i64>& zrow = X.zvec[sg];
    DasCache dc;
    while (live > 0) {
        int h = -1;
        for (int a = 0; a < nh; a++) if (H[a].live && (h < 0 || H[a].val < H[h].val)) h = a;
        if (H[h].val > best) break;
        Head& hd = H[h];
        const Group& g = X.G[hd.gi];
        int sid = g.ORD[hd.row + hd.p];
        i64 v = cost_sig(I, zrow, I.s[sid], x, hd.t, dc);
        nev++;
        if (v < best || (v == best && sid < arg)) { best = v; arg = sid; }
        hd.p++;
        if (hd.p < g.ng && g.LB[hd.row + hd.p] < INF) hd.val = scale(g.LB[hd.row + hd.p], hd.bp);
        else { hd.live = false; live--; }
    }
    if (nev_out) *nev_out += nev;
    return {best, arg};
}
// RQ_base batch: round-robin threshold algorithm (index.py RQIndex.query_batch), parcel by parcel
static Ans rq_batch1(const Inst& I, const Index& X, const Ctx& x, i64* nev_out) {
    i64 iu = (i64)(std::upper_bound(X.U.begin(), X.U.end(), x.key) - X.U.begin()) - 1;
    int sg = X.sig_of[iu];
    int m = (int)X.G.size();
    size_t row[64]; i64 bp[64]; double t[64]; bool act[64];
    int maxr = 0;
    for (int gi = 0; gi < m; gi++) {
        const Group& g = X.G[gi];
        t[gi] = billable1(I.s[g.rep], x.a, x.V);
        int e = (int)(std::lower_bound(g.E.begin(), g.E.end(), t[gi] - 1e-9) - g.E.begin());
        row[gi] = ((size_t)g.loc[sg] * g.nE + e) * g.ng;
        bp[gi] = I.bp(g.fc, x.day);
        act[gi] = true;
        maxr = std::max(maxr, g.ng);
    }
    i64 best = INF, arg = -1, nev = 0;
    const std::vector<i64>& zrow = X.zvec[sg];
    DasCache dc;
    for (int r = 0; r < maxr; r++) {
        bool any = false;
        for (int gi = 0; gi < m; gi++) {
            if (!act[gi]) continue;
            const Group& g = X.G[gi];
            if (r >= g.ng) { act[gi] = false; continue; }
            i64 lb = g.LB[row[gi] + r];
            if (!(lb < INF) || scale(lb, bp[gi]) > best) { act[gi] = false; continue; }
            any = true;
            int sid = g.ORD[row[gi] + r];
            i64 v = cost_sig(I, zrow, I.s[sid], x, t[gi], dc);  // t of the group = billable weight of every member
            nev++;
            if (v < best || (v == best && sid < arg)) { best = v; arg = sid; }
        }
        if (!any) break;
    }
    if (nev_out) *nev_out += nev;
    return {best, arg};
}

// ------------------------------------------------------------------ timing helpers
#ifdef __APPLE__
static double tick_ns() {
    mach_timebase_info_data_t tb; mach_timebase_info(&tb);
    return (double)tb.numer / tb.denom;
}
static inline uint64_t now() { return mach_absolute_time(); }
static inline uint64_t cpu_ns() { return clock_gettime_nsec_np(CLOCK_THREAD_CPUTIME_ID); }
#else
static double tick_ns() { return 1.0; }
static inline uint64_t now() { timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (uint64_t)t.tv_sec * 1000000000ull + t.tv_nsec; }
static inline uint64_t cpu_ns() { timespec t; clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t); return (uint64_t)t.tv_sec * 1000000000ull + t.tv_nsec; }
#endif
static double pctl(std::vector<double> v, double p) {  // numpy default (linear)
    std::sort(v.begin(), v.end());
    double r = p / 100.0 * (v.size() - 1);
    size_t lo = (size_t)std::floor(r), hi = (size_t)std::ceil(r);
    return v[lo] + (v[hi] - v[lo]) * (r - lo);
}
static double mean(const std::vector<double>& v) { double s = 0; for (double x : v) s += x; return s / v.size(); }

int main(int argc, char** argv) {
    if (argc < 3) { fprintf(stderr, "usage: rq verify|e1|e2 ...\n"); return 1; }
    std::string mode = argv[1];
#ifdef __APPLE__
    // benchmarking: ask the scheduler for the user-interactive QoS class (prefers performance cores)
    if (getenv("RQ_QOS_UI")) pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0);
#endif
    Inst I = load_inst(argv[2]);
    uint64_t t0 = now();
    Index X = build_index(I);
    double tk = tick_ns();
    double build_s = (now() - t0) * tk * 1e-9;
    size_t ng = X.G.size();
    if (ng > 64) { fprintf(stderr, "too many groups\n"); return 2; }
    for (const Svc& c : I.s) if (c.ngk > 64) { fprintf(stderr, "too many surcharge groups\n"); return 2; }
    size_t nlz = 0, nel = 0;
    for (const Group& g : X.G) { nlz += g.nlz; nel += g.nE; }
    if (mode == "verify") {
        int bad_ix = check_index(X, argv[3]);
        printf("{\"index\": {\"n\": %zu, \"build_s\": %.6f, \"bytes\": %zu, \"U\": %zu, \"signatures\": %zu, \"local_signatures\": %zu, \"groups\": %zu, \"elem_intervals\": %zu, \"differs_from_python\": %d}}\n",
               I.s.size(), build_s, index_bytes(X), X.U.size(), X.zvec.size(), nlz, ng, nel, bad_ix);
        Shared sh(I);
        for (int a = 4; a + 1 < argc; a += 2) {
            Parcels P = load_par(argv[a]); Truth T = load_tru(argv[a + 1]);
            size_t N = P.n();
            std::vector<i64> vb(N), va(N);
            brute_vec(I, P, vb.data(), va.data());
            i64 mm[5] = {0, 0, 0, 0, 0}, mb1[5] = {0, 0, 0, 0, 0}, nev1 = 0, nevb = 0, maxdiff = 0;
            long first_bad = -1;
            for (size_t i = 0; i < N; i++) {
                Ctx x = make_ctx(I, P, i);
                Ans r[5] = {brute1(I, x), brute_shared(I, x, sh), {vb[i], va[i]}, rq_query1(I, X, x, &nev1), rq_batch1(I, X, x, &nevb)};
                for (int k = 0; k < 5; k++) {
                    bool bad = r[k].best != T.best[i] || r[k].arg != T.arg[i];
                    if (bad) { mm[k]++; if (first_bad < 0) first_bad = (long)i; maxdiff = std::max<i64>(maxdiff, std::llabs(r[k].best - T.best[i])); }
                    if (T.has_b1 && (r[k].best != T.b1[i] || r[k].arg != T.a1[i])) mb1[k]++;
                }
            }
            printf("{\"par\": \"%s\", \"N\": %zu, \"mismatch_vs_py_brute_vec\": {\"B1\": %lld, \"B1s\": %lld, \"B1v\": %lld, \"RQ_q1\": %lld, \"RQ_batch\": %lld}, "
                   "\"has_py_brute1\": %d, \"mismatch_vs_py_brute1\": {\"B1\": %lld, \"B1s\": %lld, \"B1v\": %lld, \"RQ_q1\": %lld, \"RQ_batch\": %lld}, "
                   "\"max_abs_cost_diff\": %lld, \"first_bad\": %ld, \"evals_q1\": %.4f, \"evals_batch\": %.4f}\n",
                   argv[a], N, mm[0], mm[1], mm[2], mm[3], mm[4], (int)T.has_b1, mb1[0], mb1[1], mb1[2], mb1[3], mb1[4],
                   maxdiff, first_bad, (double)nev1 / N, (double)nevb / N);
            if (first_bad >= 0) {
                size_t i = first_bad; Ctx x = make_ctx(I, P, i);
                Ans b = brute1(I, x), q = rq_query1(I, X, x, nullptr);
                fprintf(stderr, "first bad %zu: truth (%lld,%lld) B1 (%lld,%lld) RQ (%lld,%lld) key=%lld a=%.17g L=%.17g M=%.17g S=%.17g res=%g day=%lld\n",
                        i, T.best[i], T.arg[i], b.best, b.arg, q.best, q.arg, x.key, x.a, x.L, x.M, x.S, x.res, x.day);
            }
            fflush(stdout);
        }
        return 0;
    }
    if (mode == "e1") {  // per-query single-shot latency, warm (one untimed pass first)
        Parcels P = load_par(argv[3]);
        size_t q = P.n();
        Shared sh(I);
        volatile i64 sink = 0;
        // timer overhead
        std::vector<double> ov(q);
        for (size_t i = 0; i < q; i++) { uint64_t a = now(); uint64_t b = now(); ov[i] = (b - a) * tk; }
        const char* names[3] = {"B1", "B1s", "RQ_q1"};
        printf("{\"n\": %zu, \"q\": %zu, \"tick_ns\": %.4f, \"timer_overhead_ns_p50\": %.2f, \"build_s\": %.6f, \"index_bytes\": %zu, \"interleaved\": 1", I.s.size(), q, tk, pctl(ov, 50), build_s, index_bytes(X));
        auto run = [&](int m, size_t i) -> Ans {
            Ctx x = make_ctx(I, P, i);
            if (m == 0) return brute1(I, x);
            if (m == 1) return brute_shared(I, x, sh);
            return rq_query1(I, X, x, nullptr);
        };
        for (int m = 0; m < 3; m++) for (size_t i = 0; i < q; i++) sink += run(m, i).best;   // warm-up pass
        // calibration: thread-CPU delta of an empty body measured exactly like a query (two clock pairs)
        std::vector<double> cal(q);
        for (size_t i = 0; i < q; i++) { uint64_t c0 = cpu_ns(); uint64_t a = now(); uint64_t b = now(); uint64_t c1 = cpu_ns(); cal[i] = (double)(c1 - c0); (void)a; (void)b; }
        double cpu_ovh = pctl(cal, 50);
        std::vector<double> lat[3], cl[3];
        for (int m = 0; m < 3; m++) { lat[m].resize(q); cl[m].resize(q); }
        for (size_t i = 0; i < q; i++)   // methods interleaved per query: same machine state for all three
            for (int m = 0; m < 3; m++) {
                uint64_t c0 = cpu_ns(); uint64_t a = now(); Ans r = run(m, i); uint64_t b = now(); uint64_t c1 = cpu_ns();
                sink += r.arg; lat[m][i] = (b - a) * tk; cl[m][i] = (double)(c1 - c0) - cpu_ovh;
            }
        double stream[3] = {1e300, 1e300, 1e300}, scpu[3] = {1e300, 1e300, 1e300};
        for (int rep = 0; rep < 3; rep++)
            for (int m = 0; m < 3; m++) {
                uint64_t c0 = cpu_ns(); uint64_t s0 = now();
                for (size_t i = 0; i < q; i++) sink += run(m, i).arg;
                stream[m] = std::min(stream[m], (now() - s0) * tk / q);
                scpu[m] = std::min(scpu[m], (double)(cpu_ns() - c0) / q);
            }
        printf(", \"cpu_overhead_ns\": %.2f", cpu_ovh);
        for (int m = 0; m < 3; m++)
            printf(", \"%s\": {\"p50_ns\": %.2f, \"p95_ns\": %.2f, \"p99_ns\": %.2f, \"mean_ns\": %.2f, \"cpu_p50_ns\": %.2f, \"cpu_p95_ns\": %.2f, \"cpu_p99_ns\": %.2f, \"cpu_mean_ns\": %.2f, \"stream_mean_ns\": %.2f, \"stream_cpu_mean_ns\": %.2f}", names[m],
                   pctl(lat[m], 50), pctl(lat[m], 95), pctl(lat[m], 99), mean(lat[m]), pctl(cl[m], 50), pctl(cl[m], 95), pctl(cl[m], 99), mean(cl[m]), stream[m], scpu[m]);
        printf(", \"sink\": %lld}\n", (long long)(sink & 1));
        return 0;
    }
    if (mode == "e2") {
        Parcels P = load_par(argv[3]);
        int reps = argc > 4 ? atoi(argv[4]) : 3;
        if (getenv("RQ_E2_N")) {  // time on the first RQ_E2_N parcels only
            size_t n2 = (size_t)atol(getenv("RQ_E2_N"));
            if (n2 < P.n()) {
                P.key.resize(n2); P.day.resize(n2); P.a.resize(n2); P.L.resize(n2); P.M.resize(n2); P.S.resize(n2); P.res.resize(n2);
            }
        }
        std::string skip = getenv("RQ_E2_SKIP") ? getenv("RQ_E2_SKIP") : "";  // e.g. "B1"
        size_t N = P.n();
        std::vector<i64> b(N), a(N);
        Shared sh(I);
        const char* names[5] = {"B1", "B1s", "B1v", "RQ_batch", "RQ_q1loop"};
        printf("{\"N\": %zu, \"reps\": %d, \"build_s\": %.6f", N, reps, build_s);
        i64 nev[5] = {0, 0, 0, 0, 0};
        auto run = [&](int m) {
            nev[m] = 0;
            if (m == 2) { brute_vec(I, P, b.data(), a.data()); return; }
            for (size_t i = 0; i < N; i++) {
                Ctx x = make_ctx(I, P, i);
                Ans r = m == 0 ? brute1(I, x) : m == 1 ? brute_shared(I, x, sh) : m == 3 ? rq_batch1(I, X, x, &nev[m]) : rq_query1(I, X, x, &nev[m]);
                b[i] = r.best; a[i] = r.arg;
            }
        };
        std::vector<double> ts[5], tc[5]; i64 cs[5];
        bool on[5];
        for (int m = 0; m < 5; m++) on[m] = ("," + skip + ",").find("," + std::string(names[m]) + ",") == std::string::npos;
        for (int m = 0; m < 5; m++) if (on[m]) run(m);  // warm-up
        for (int k = 0; k < reps; k++)        // methods interleaved within every repetition
            for (int m = 0; m < 5; m++) {
                if (!on[m]) continue;
                uint64_t c0 = cpu_ns(); uint64_t s0 = now(); run(m); ts[m].push_back((now() - s0) * tk * 1e-9); tc[m].push_back((cpu_ns() - c0) * 1e-9);
                cs[m] = 0;
                for (size_t i = 0; i < N; i++) cs[m] += (b[i] & 0xffff) * 31 + a[i];
            }
        printf(", \"interleaved\": 1");
        for (int m = 0; m < 5; m++)
            if (on[m]) printf(", \"%s\": {\"s_median\": %.6f, \"s_min\": %.6f, \"cpu_s_median\": %.6f, \"cpu_s_min\": %.6f, \"evals_per_parcel\": %.4f, \"checksum\": %lld}", names[m],
                   pctl(ts[m], 50), *std::min_element(ts[m].begin(), ts[m].end()), pctl(tc[m], 50), *std::min_element(tc[m].begin(), tc[m].end()), (double)nev[m] / N, (long long)cs[m]);
        printf("}\n");
        return 0;
    }
    fprintf(stderr, "unknown mode\n");
    return 1;
}
