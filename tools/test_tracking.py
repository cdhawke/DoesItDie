"""Simulates DoesItDie's DoT tracking (extracted from the addon) through scripted fights, with game APIs stubbed.

    pip install lupa
    python tools/test_tracking.py

Covers cast outcomes (dodge/parry/miss vs. auto-attacks), recasts, first-tick waiting and combo point counting.
Each scenario prints what the marker would count, and PASS/FAIL against the expectation.
"""
import os
from lupa import LuaRuntime

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "DoesItDie", "DoesItDie.lua")
src = open(SRC, encoding="utf-8").read()


def chunk(start_marker, end_marker):
    start = src.index(start_marker)
    return src[start:src.index(end_marker, start)]


HARNESS = """
now = 100
log = {}
db = { ticks = {}, waitFirstTick = "off" }
local dotsByTarget = {}
local function isSecret(v) return false end
local function trace(msg) table.insert(log, string.format("%.2f %s", now, msg)) end
function GetTime() return now end
-- The target and the mobs behind visible nameplates (unit -> mob key).
local targetKey, plates = "mob", {}
local function unitKey(unit) if unit == "target" then return targetKey end return plates[unit] end
function UnitExists(unit) return unitKey(unit) ~= nil end
local castCount = 0
local SPELLS = {
    [1] = { "Corruption", "Corrupts the target, causing 40 Shadow damage over 12 sec." },
    [2] = { "Rake", "Rake the target for 19 damage and an additional 39 damage over 9 sec.  Awards 1 combo point." },
    [3] = { "Claw", "Claw the enemy, causing 27 additional damage.  Awards 1 combo point." },
    [4] = { "Rip", "Finishing move that causes damage over time. 1 point : 42 damage over 12 sec. 2 points: 71 damage over 12 sec. 5 points: 138 damage over 12 sec." },
    [5] = { "Rend", "Wounds the target causing them to bleed for 45 damage over 9 sec." },
    [6] = { "Bane of Agony", "Afflicts the target with agony, causing 72 Shadow damage over 24 sec.  This damage is dealt slowly at first, and builds up as the Bane reaches its full duration." },
    [7] = { "Serpent Sting", "Stings the target, causing 10 Nature damage over 15 sec.  Only one Sting per Hunter can be active on any one target." },
}
local function spellNameAndDescription(id) return SPELLS[id][1], SPELLS[id][2] end
""" + chunk("local UPDATE_INTERVAL", "-- User options") + chunk(
    "local function schoolFromWords", "---------------------------------------------------------------------------\n-- Display") + """
local api = {}
function api.reset(waitMode)
    for k in pairs(dotsByTarget) do dotsByTarget[k] = nil end
    for k in pairs(db.ticks) do db.ticks[k] = nil end
    db.waitFirstTick = waitMode or "off"
    lastBuilder, lastDotCast, lastOutcome = nil, nil, nil
    for k in pairs(sentTargets) do sentTargets[k] = nil end
    resetComboCount()
    targetKey, plates = "mob", {}
    log = {}
end
function api.learn(spellID, perTick) db.ticks[spellID] = perTick end
function api.learned(spellID) return db.ticks[spellID] end
-- Pressing the button (UNIT_SPELLCAST_SENT) and the server confirming it (UNIT_SPELLCAST_SUCCEEDED).
function api.send(spellID) castCount = castCount + 1; local guid = "cast-" .. castCount; onCastSent(spellID, guid); return guid end
function api.succeed(spellID, guid) return onPlayerCast(spellID, guid) end
function api.cast(spellID) return api.succeed(spellID, api.send(spellID)) end
function api.target(key) targetKey = key; resetComboCount() end
function api.plate(unit, key) plates[unit] = key end
-- Hits and avoids on the target, or on another unit (e.g. a nameplate).
function api.hit(amount, school, flag, unit) return onUnitCombat(unit or "target", "WOUND", flag or "", amount, school or 1) end
function api.avoid(action, school, unit) onUnitCombat(unit or "target", action, "", 0, school or 1) end
function api.advance(seconds) now = now + seconds; housekeep(now) end
function api.marker() local damage, count = targetRemainingDamage(); return damage .. " dmg from " .. count .. " DoT(s)" end
function api.markerOn(key) local total = 0; for _, e in ipairs(dotBreakdown(key)) do total = total + e.damage end; return math.floor(total + 0.5) .. " dmg" end
function api.counted() return countedPoints end
function api.log() return table.concat(log, "\\n") end
return api
"""

sim = LuaRuntime(unpack_returned_tuples=True).execute(HARNESS)
failures = 0


def check(label, actual, expected):
    global failures
    ok = str(actual) == str(expected)
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label:<64} {actual}" + ("" if ok else f"   (expected {expected})"))


# Corruption: 4 ticks of 10, learned so it shows straight away.
sim.reset(); sim.learn(1, 10); sim.cast(1)
check("Corruption lands: shown immediately", sim.marker(), "40 dmg from 1 DoT(s)")
sim.advance(3); sim.hit(10, 32)
check("  after first tick", sim.marker(), "30 dmg from 1 DoT(s)")

sim.reset(); sim.learn(1, 10); sim.cast(1); sim.advance(0.1); sim.avoid("RESIST", 32)
check("Corruption resisted: dropped", sim.marker(), "0 dmg from 0 DoT(s)")
check("  log says dropped", "right after Corruption; dropped" in sim.log(), True)
sim.advance(2.9); sim.hit(10, 32)
check("  a same-school hit at tick time doesn't bring it back", sim.marker(), "0 dmg from 0 DoT(s)")

sim.reset(); sim.learn(2, 13); sim.cast(2); sim.advance(0.05); sim.hit(19, 1); sim.advance(0.25); sim.avoid("DODGE")
check("Rake hit, then an auto-attack dodged: still shown", sim.marker(), "39 dmg from 1 DoT(s)")
check("  Rake's combo point still counted", sim.counted(), 1)

sim.reset(); sim.learn(2, 13); sim.cast(2); sim.advance(0.05); sim.avoid("PARRY")
check("Rake parried: dropped", sim.marker(), "0 dmg from 0 DoT(s)")
check("  combo point taken back", sim.counted(), 0)

sim.reset(); sim.learn(1, 10); sim.avoid("MISS", 32); sim.advance(0.1); sim.cast(1)
check("Miss arrives just BEFORE the cast event: dropped", sim.marker(), "0 dmg from 0 DoT(s)")

# Replay of log 123506.4: first Corruption of the session (nothing learned). At its first tick a Shadow Bolt
# (26) and the real tick (10) land in the same instant, bolt first. Previously 26 was learned as the tick size.
sim.reset(); sim.cast(1); sim.advance(3.0); sim.hit(26, 32); sim.hit(10, 32)
check("Replay: Shadow Bolt + tick in the same instant: the 10 is the tick", sim.marker(), "30 dmg from 1 DoT(s)")
check("  log shows the swap", "SWAP Corruption tick: 26 was another hit, 10 is the tick" in sim.log(), True)
sim.reset(); sim.learn(1, 10); sim.cast(1); sim.advance(3.0); sim.hit(10, 32); sim.hit(26, 32)
check("  tick first, bolt second: no swap", sim.marker(), "30 dmg from 1 DoT(s)")

# Replay of the rest of that log: Corruption had learned 29 (from Shadow Bolts), so every real tick of 10-11
# was rejected and each cast was dropped as "never ticked". Two on-rhythm ticks now relearn it.
sim.reset(); sim.learn(1, 29); sim.cast(1)
sim.advance(3.0); sim.hit(11, 32)
check("Replay: learned 29, first real tick (11) still rejected", sim.marker(), "116 dmg from 1 DoT(s)")
sim.advance(3.0); sim.hit(10, 32)
check("  second on-rhythm tick (10): relearned, not dropped", sim.marker(), "21 dmg from 1 DoT(s)")
check("  and the saved tick size is fixed", sim.learned(1), 10.5)
sim.advance(3.0); sim.hit(11, 32)
check("  next tick matches normally", sim.marker(), "11 dmg from 1 DoT(s)")

sim.reset(); sim.learn(1, 10); sim.cast(1)
sim.advance(1.0); sim.hit(26, 32); sim.advance(1.0); sim.hit(27, 32)
check("Two off-rhythm Shadow Bolts don't trigger a relearn", sim.learned(1), 10)

# Replay of log 166015.8: Bane of Agony, 72 over 24s, back-loaded ticks 3,3,3,3,6,6,6,6,9,9,9,9 every 2s.
# Previously it learned 3, estimated half the damage, rejected the 6s, relearned, and dropped the 9s.
sim.reset(); sim.cast(6)
check("Replay: Bane of Agony estimate at cast is the full 72", sim.marker(), "72 dmg from 1 DoT(s)")
remaining = []
for amount in (3, 3, 3, 3, 6, 6, 6, 6, 9, 9, 9, 9):
    sim.advance(2.0); sim.hit(amount, 32)
    remaining.append(sim.marker().split()[0])
check("  remaining after each tick follows the ramp", remaining,
      ["69", "66", "63", "60", "54", "48", "42", "36", "27", "18", "9", "0"])
log = sim.log()
check("  all 12 ticks matched", log.count("TICK Bane of Agony"), 12)
check("  no relearn or unmatched hits", ("RELEARN" in log) or ("not matched" in log), False)
check("  learned the average tick (6)", float(sim.learned(6)), 6.0)
sim.advance(3.0); sim.cast(6)
check("  next cast starts from the learned average: 72", sim.marker(), "72 dmg from 1 DoT(s)")

# Replay of the in-game miss (log 99223.7): 2-point Rip missed, white hits of 20-22 kept landing near tick
# times. Previously a white hit at +2.6s "proved" the Rip and brought it back.
sim.reset(); sim.learn("4x2", 15.2)
sim.cast(3); sim.advance(0.05); sim.hit(20, 1); sim.advance(1); sim.cast(3); sim.advance(0.05); sim.hit(20, 1)
sim.advance(1); sim.cast(4); sim.avoid("MISS")
for t, amount in ((0.8, 20), (1.8, 20), (1.0, 21), (0.9, 22), (1.8, 20)):
    sim.advance(t); sim.hit(amount, 1)
check("Replay: missed Rip stays gone through white hits", sim.marker(), "0 dmg from 0 DoT(s)")

# Replay of a landed 3-point Rip (log 99200.3): ticks 21,21,20,20 every ~2s, with white hits of the SAME size
# in between. All four ticks must be matched and no white hit.
sim.reset()
for _ in range(3):
    sim.cast(3); sim.advance(0.05); sim.hit(20, 1); sim.advance(1)
start = 0.0
sim.cast(4)
events = [(0.9, 21), (1.3, 20), (1.9, 21), (2.5, 21), (3.5, 20), (3.9, 21), (4.3, 22), (5.5, 22), (6.0, 20),
          (6.8, 46), (8.0, 20)]
for at, amount in events:
    sim.advance(at - start); start = at; sim.hit(amount, 1)
lines = sim.log().split("\n")
cast_at = float(next(line for line in lines if "CAST Rip" in line).split()[0])
ticks = [round(float(line.split()[0]) - cast_at, 1) for line in lines if "TICK Rip" in line]
check("Replay: landed 3-point Rip matches exactly its 4 ticks", len(ticks), 4)
check("  and they're the right ones (seconds after the cast)", ticks, [1.9, 3.9, 6.0, 8.0])

# Rend (3 ticks of 15 every 3s) with white hits of ~20 and white crits of ~40 in between.
sim.reset(); sim.learn(5, 15); sim.cast(5)
sim.advance(2.95); sim.hit(20, 1)
check("Rend: white hit (20) at tick time isn't a tick (learned 15)", sim.marker(), "45 dmg from 1 DoT(s)")
sim.advance(0.05); sim.hit(15, 1)
check("  the real tick (15) is", sim.marker(), "30 dmg from 1 DoT(s)")
sim.advance(0.15); sim.hit(15, 1)
check("  a second 15 right after the tick isn't another tick", sim.marker(), "30 dmg from 1 DoT(s)")
sim.advance(0.85); sim.hit(15, 1)
check("  a 15 off the 3s rhythm (+1s) isn't a tick", sim.marker(), "30 dmg from 1 DoT(s)")
sim.advance(1.95); sim.hit(41, 1, "CRITICAL")
check("  white crit (41) at tick time isn't a tick crit", sim.marker(), "30 dmg from 1 DoT(s)")
sim.advance(0.05); sim.hit(30, 1, "CRITICAL")
check("  tick crit (30 = 2x15) is", sim.marker(), "15 dmg from 1 DoT(s)")
check("  and doesn't raise the expected tick size", "expecting 15.0 for this tick" in sim.log().split("\n")[-1], True)

sim.reset(); sim.learn(1, 10); sim.avoid("MISS"); sim.advance(1.0); sim.cast(1)
check("Old miss (1s before cast): ignored, shown", sim.marker(), "40 dmg from 1 DoT(s)")

sim.reset(); sim.learn(1, 10); sim.cast(1); sim.advance(3); sim.hit(10, 32); sim.advance(1)
sim.cast(1); sim.advance(0.1); sim.avoid("RESIST", 32)
check("Corruption ticking, recast resisted: earlier one kept", sim.marker(), "30 dmg from 1 DoT(s)")

sim.reset(); sim.cast(3); sim.advance(0.05); sim.hit(27, 1); sim.advance(1); sim.cast(3); sim.advance(0.05); sim.avoid("DODGE")
sim.advance(1); sim.cast(3); sim.advance(0.05); sim.hit(27, 1)
check("Claw hit, Claw dodged, Claw hit: 2 combo points", sim.counted(), 2)
sim.advance(1); sim.cast(4)
check("  Rip uses the counted 2 points", "2 combo points (counted)" in sim.log(), True)

sim.reset("unsure"); sim.cast(1)
check("'When unsure', never seen tick: waits", sim.marker(), "0 dmg from 0 DoT(s)")
sim.advance(3); sim.hit(10, 32)
check("  first tick: shown with the real tick size", sim.marker(), "30 dmg from 1 DoT(s)")

# Replay of log 253548.7: Serpent Sting (10 over 15s, first cast of the session) with the arrow's travel time:
# the first tick came 3.7s after the cast, not 3s, with Arcane Shot and auto shots around it. Previously the
# late tick went unmatched and the sting was dropped as "never ticked" at +6.5s.
sim.reset(); sim.cast(7)
sim.advance(2.2); sim.hit(28, 64); sim.hit(15, 1)
sim.advance(1.5); sim.hit(4, 8)
check("Replay: Serpent Sting's first tick 3.7s after the cast is matched", "TICK Serpent Sting 4" in sim.log(), True)
sim.advance(0.5); sim.hit(13, 1); sim.advance(2.1); sim.hit(14, 1)
check("  not dropped as never ticked", "DROP" in sim.log(), False)
remaining = []
for amount in (3, 3, 3, 4):  # at 6.7, 9.7, 12.7, 15.7s
    sim.advance(3.0 if remaining else 0.4)
    sim.hit(amount, 8)
    remaining.append(sim.marker().split()[0])
check("  the other four ticks keep the landing's rhythm", sim.log().count("TICK Serpent Sting"), 5)
check("  and count down to zero", remaining[-1], "0")
sim.advance(1.0)
check("  expires after the fifth tick", "EXPIRE Serpent Sting" in sim.log(), True)

sim.reset(); sim.learn(7, 3); sim.cast(7); sim.advance(3.7)
check("Serpent Sting in flight: no tick lost before the first one lands", sim.marker(), "15 dmg from 1 DoT(s)")
sim.advance(3.5); sim.advance(3.0)
check("  never ticks at all: still dropped", "DROP Serpent Sting" in sim.log(), True)

sim.reset(); sim.learn(1, 10); sim.cast(1); sim.advance(3.8); sim.hit(10, 32)
check("Corruption (no travel): a tick 0.8s late still doesn't count", "TICK Corruption" in sim.log(), False)

# Player feedback: tab-dotting. An instant DoT's cast event comes a round trip after the button press; a player
# who tabs straight away has a new target by then.
sim.reset(); sim.target("A"); guid = sim.send(6); sim.target("B"); sim.succeed(6, guid)
check("Agony sent on A, tabbed to B before the cast event: on A", sim.markerOn("A"), "72 dmg")
check("  and not on B", sim.marker(), "0 dmg from 0 DoT(s)")
check("  log names the mob", "on A, the target when sent" in sim.log(), True)

# Player feedback: "it forgets previous targets" when chain pulling. Ticks on a mob that's neither the target
# nor on a visible nameplate never reach the addon; that isn't proof the DoT didn't land.
sim.reset(); sim.learn(1, 10); sim.target("A"); sim.cast(1); sim.advance(0.5); sim.target("B"); sim.advance(9)
check("Corruption on A, tabbed away, A's ticks unseen: still tracked", sim.markerOn("A"), "10 dmg")
check("  not dropped as never ticked", "DROP" in sim.log(), False)
sim.target("A")
check("  back on A: shown", sim.marker(), "10 dmg from 1 DoT(s)")
sim.advance(3)
check("  and expires on time", sim.markerOn("A"), "0 dmg")

sim.reset(); sim.learn(1, 10); sim.target("A"); sim.cast(1); sim.target("B"); sim.plate("nameplate1", "A"); sim.advance(7)
check("Same, but A's nameplate is up and no tick came: dropped", "DROP Corruption" in sim.log(), True)

sim.reset(); sim.learn(1, 10); sim.target("A"); sim.cast(1); sim.target("B"); sim.plate("nameplate1", "A")
sim.advance(3); sim.hit(10, 32, "", "nameplate1")
check("Ticks on A's nameplate still count after tabbing away", sim.markerOn("A"), "30 dmg")

# Player feedback: Agony sometimes not recognized. The pet's (or a groupmate's) melee avoids right after the cast
# were taken as the spell's outcome.
sim.reset(); sim.learn(6, 6); sim.cast(6); sim.advance(0.1); sim.avoid("DODGE")
check("Agony, pet's melee dodged right after: ignored", sim.marker(), "72 dmg from 1 DoT(s)")
sim.advance(0.1); sim.avoid("RESIST", 32)
check("  the spell's own resist right after still drops it", sim.marker(), "0 dmg from 0 DoT(s)")

sim.reset(); sim.learn(6, 6); sim.cast(6); sim.advance(0.05); sim.hit(30, 1); sim.advance(0.1); sim.avoid("RESIST", 32)
check("Agony, pet's melee hit, then the spell's resist: dropped", sim.marker(), "0 dmg from 0 DoT(s)")

sim.reset(); sim.learn(6, 6); sim.cast(6); sim.advance(0.1); sim.avoid("MISS", 1)
check("Agony, a Physical miss right after: waits for its first tick", sim.marker(), "0 dmg from 0 DoT(s)")
sim.advance(1.9)
check("  first tick shows it (and flashes)", sim.hit(3, 32), "mob")
check("  with the ramp's remaining damage", sim.marker(), "69 dmg from 1 DoT(s)")

sim.reset(); sim.learn(6, 6); sim.cast(6); sim.advance(0.1); sim.avoid("MISS", 1); sim.advance(5)
check("  it never ticks: dropped", "DROP Bane of Agony" in sim.log(), True)

sim.reset(); sim.learn(6, 6); sim.plate("nameplate2", "other"); sim.cast(6); sim.advance(0.1)
sim.avoid("RESIST", 32, "nameplate2")
check("A resist on another mob doesn't touch the target's Agony", sim.marker(), "72 dmg from 1 DoT(s)")

sim.reset(); sim.learn(5, 15); sim.cast(5); sim.advance(0.05); sim.avoid("PARRY")
check("Rend (Physical) parried: still dropped straight away", sim.marker(), "0 dmg from 0 DoT(s)")

print(f"\n{'all passed' if not failures else str(failures) + ' FAILED'}")
if failures:
    print("\nlog of last scenario:\n" + sim.log())
