import math, random

BASE = 1500
exp_ = lambda ra, rb: 1 / (1 + 10 ** ((rb - ra) / 400))
k = lambda n: 250 / (n + 5) ** 0.4
def mov(w, l, use=True):
    if not use: return 1
    return min(1.25, max(0.75, 0.75 + 0.5 * ((w - l) / max(w, 1))))

print("== curve / K / MOV ==")
print("100pt edge win prob:", round(exp_(1600, 1500), 4))
print("K at 0/5/30/100 matches:", [round(k(n), 1) for n in (0, 5, 30, 100)])
print("MOV 11-9:", round(mov(11, 9), 3), "11-5:", round(mov(11, 5), 3), "11-0:", round(mov(11, 0), 3))

print("\n== zero-sum, equal experience doubles ==")
S = {p: {'r': 1500, 'n': 10} for p in 'ABCD'}
EA = exp_(1500, 1500); m = mov(11, 7)
d = {}
for p in 'AB': d[p] = k(S[p]['n']) * m * (1 - EA)
for p in 'CD': d[p] = k(S[p]['n']) * m * (0 - (1 - EA))
print({p: round(v, 2) for p, v in d.items()}, "sum:", round(sum(d.values()), 10))

print("\n== unequal experience, same winning team ==")
S = {'A': 0, 'B': 60, 'C': 30, 'D': 30}
d = {}
for p in 'AB': d[p] = k(S[p]) * mov(11, 9) * 0.5
for p in 'CD': d[p] = k(S[p]) * mov(11, 9) * -0.5
print({p: round(v, 2) for p, v in d.items()})
print("rookie/veteran swing ratio:", round(d['A'] / d['B'], 2))

# ---- convergence: do ratings recover known true skill? ----
print("\n== convergence simulation (singles, 400 matches) ==")
random.seed(7)
true = {'A': 1800, 'B': 1650, 'C': 1500, 'D': 1350, 'E': 1200}
R = {p: 1500 for p in true}; N = {p: 0 for p in true}
names = list(true)
for _ in range(400):
    a, b = random.sample(names, 2)
    pa = exp_(true[a], true[b])
    a_won = random.random() < pa
    hi, lo = (11, random.choice([0,3,5,7,9]))
    EA = exp_(R[a], R[b]); m = mov(hi, lo)
    da = k(N[a]) * m * ((1 if a_won else 0) - EA)
    db = k(N[b]) * m * ((0 if a_won else 1) - (1 - EA))
    R[a] += da; R[b] += db; N[a] += 1; N[b] += 1
for p in sorted(R, key=lambda x: -R[x]):
    print(f"  {p}: true {true[p]}  ->  elo {round(R[p])}")
order_ok = sorted(R, key=lambda x: -R[x]) == sorted(true, key=lambda x: -true[x])
print("recovered correct ranking order:", order_ok)

# ---- serve-first z-test verification ----
print("\n== serve z-test sanity ==")
def normcdf_as(x):  # the Abramowitz-Stegun impl used in the frontend
    t = 1 / (1 + 0.2316419 * x); dd = 0.3989423 * math.exp(-x * x / 2)
    return 1 - dd * t * (1.330274 * t**4 - 1.821256 * t**3 + 1.781478 * t*t - 0.356538 * t + 0.319382)
for x in (0, 0.5, 1.0, 1.645, 1.96, 2.576, 3.0):
    print(f"  z={x:<6} approx={normcdf_as(x):.6f}  exact={0.5*(1+math.erf(x/math.sqrt(2))):.6f}")

def ztest(w, n):
    p = w / n; se = math.sqrt(0.25 / n); z = (p - 0.5) / se
    return p, z, 2 * (1 - normcdf_as(abs(z)))
for w, n in [(6, 10), (18, 30), (60, 100), (55, 100), (120, 200)]:
    p, z, pv = ztest(w, n)
    print(f"  {w}/{n} = {p:.1%}  z={z:.2f}  p={pv:.4f}  {'SIGNIFICANT' if pv < 0.05 else 'not significant'}")
print("\nMoE at n=30:", round(100 * 1.96 * math.sqrt(0.25 / 30), 1), "pts")
print("MoE at n=100:", round(100 * 1.96 * math.sqrt(0.25 / 100), 1), "pts")
