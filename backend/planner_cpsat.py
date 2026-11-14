"""Shift-plan proposals with OR-Tools CP-SAT (be-hav, optional module).

`plan` proposes which verified, qualified worker could take each remaining task of the shift and
in what order, keeping every worker's projected estimated HAV exposure (UK HSE points method)
below a conservative cap where that is feasible.

What this module never does:
- it never approves, assigns, reassigns or stores anything: it only reads and returns proposals
  (`status: "proposal"`, `requires_approval: True`); a supervisor decides;
- it never invents a vibration value: a task whose tool has no configured `a_hv_ms2` is left
  unassigned with a stated reason;
- it never treats an unknown or unverified qualification as held;
- it never presents a figure below the action or limit value as "safe".

Points are scaled to integers (1 unit = 0.01 point, rounded up) for the solver.
"""
from __future__ import annotations

import math
from typing import Optional

from ortools.sat.python import cp_model

from . import config, exposure, tasks, tool_profiles

LABEL = "Estimated HAV exposure — screening/planning, not certified measurement"
SCALE = 100                     # solver units per exposure point
SOLVE_SECONDS = 5.0             # per solve; a timeout is reported, never hidden

_NOT_MEASURED = ("not measured: this worker wears no band in this shift, so current exposure is "
                 "taken as 0 for planning and the real figure may be higher. A supervisor must "
                 "check this worker's own exposure before any assignment.")


def _number(value) -> Optional[float]:
    """A finite float, or None. Never a guess."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _scaled_up(points_value: float) -> int:
    """Points -> integer solver units, rounded up (conservative)."""
    return int(math.ceil(points_value * SCALE - 1e-9))


def _pts(units: int) -> float:
    return round(units / SCALE, 2)


def _fmt(value: float) -> str:
    return f"{value:.1f}"


# ---------------------------------------------------------------- inputs

def _score_task(task: dict) -> tuple[Optional[int], str]:
    """(scaled additional points or None when it cannot be scored, reason)."""
    title = task.get("title") or task["task_id"]
    minutes = _number(task.get("expected_trigger_minutes")) or 0.0
    tool_id = task.get("tool_id")
    if minutes <= 0:
        return 0, (f"Task '{title}' has no planned powered-tool trigger time, so it adds 0 points "
                   "on this estimate.")
    if not tool_id:
        return None, (f"Task '{title}' plans {_fmt(minutes)} min of trigger time but names no "
                      "tool, so it cannot be scored. A person must say which tool will be used; "
                      "no value is assumed.")
    profile = tool_profiles.get(tool_id)
    a_hv = _number(profile.a_hv_ms2) if profile else None
    if a_hv is None:
        what = f"{profile.name} has no vibration value configured" if profile \
            else f"tool '{tool_id}' has no tool profile"
        return None, (f"Task '{title}' cannot be scored: {what}. A person must enter the "
                      "manufacturer's declared or measured vibration value; no value is assumed.")
    added = exposure.points(a_hv, minutes * 60.0)
    return _scaled_up(added), (f"Task '{title}': {profile.name} at {a_hv:g} m/s² for "
                               f"{_fmt(minutes)} min adds an estimated {_fmt(added)} points "
                               "(2 × a² × hours).")


def _worker_block(worker: dict) -> Optional[str]:
    """Why this worker can take no task at all, or None."""
    name = worker["name"]
    if worker.get("available") is False:
        return f"{name}: recorded as not available for this shift."
    if worker["qualifications"] is None:
        return (f"{name}: qualifications are unknown, so no task is proposed. A supervisor must "
                "record and verify them first.")
    if not worker["qualifications_verified"]:
        return (f"{name}: qualifications are not verified, so no task is proposed. A supervisor "
                "must verify them first.")
    return None


def _current_exposure(shift_id: str, workers: list[dict]) -> dict[str, dict]:
    """Per worker: scaled current points, the figure shown, and how it was obtained."""
    wearer = config.current_shift()["worker_id"]
    result = exposure.compute(shift_id) or {}               # pure read: never recompute here
    total = _number(result.get("total_points_exact"))
    if total is None:
        total = _number(result.get("total_points"))
    out: dict[str, dict] = {}
    for worker in workers:
        wid = worker["worker_id"]
        if wid != wearer:
            out[wid] = {"units": 0, "points": 0.0, "basis": "not_measured", "note": _NOT_MEASURED}
        elif total is None:
            out[wid] = {"units": 0, "points": None, "basis": "unavailable", "note": (
                "current exposure estimate is unavailable for the band wearer: planned with 0, so "
                "projected figures count planned tasks only and the real figure may be higher.")}
        else:
            note = "estimated from this shift's tool-use records for the band wearer."
            if (result.get("status") or "ok") != "ok":
                note += (" The figure is partial: some tool use is unconfirmed or has no points "
                         "yet, so the real exposure may be higher.")
            if result.get("contains_simulated"):
                note += " Includes simulated (replay/synthetic) data: not a live recording."
            out[wid] = {"units": _scaled_up(max(0.0, total)), "points": round(total, 2),
                        "basis": "estimated", "note": note}
    return out


def _prepare(shift_id: str) -> dict:
    all_tasks = tasks.list_tasks(shift_id, include_done=True)
    by_id = {t["task_id"]: t for t in all_tasks}
    done = {t["task_id"] for t in all_tasks if t["status"] == "done"}
    pending = [t for t in all_tasks if t["status"] != "done"]
    workers = tasks.list_workers()

    blocked_workers, usable = [], []
    for worker in workers:
        reason = _worker_block(worker)
        if reason:
            blocked_workers.append({"worker_id": worker["worker_id"],
                                    "worker_name": worker["name"], "reason": reason})
        else:
            usable.append(worker)

    planned: dict[str, dict] = {}
    unassigned: dict[str, str] = {}
    for task in pending:
        tid = task["task_id"]
        units, reason = _score_task(task)
        if units is None:
            unassigned[tid] = reason
            continue
        required = task.get("required_qualification")
        eligible = [w["worker_id"] for w in usable
                    if not required or required in w["qualifications"]]
        if not eligible:
            need = f"the required qualification '{required}'" if required \
                else "verified qualifications"
            unassigned[tid] = (f"Task '{task.get('title') or tid}': no available worker with "
                               f"{need} verified. Unknown or unverified qualifications are not "
                               "treated as held.")
            continue
        planned[tid] = {"task": task, "units": units, "score_reason": reason,
                        "eligible": eligible}

    # A task cannot be ordered after a prerequisite that is not in the plan.
    changed = True
    while changed:
        changed = False
        for tid in list(planned):
            for dep in planned[tid]["task"]["dependencies"]:
                if dep in done or dep in planned:
                    continue
                title = planned[tid]["task"].get("title") or tid
                why = "is not a known task of this shift" if dep not in by_id \
                    else "is itself left unassigned"
                unassigned[tid] = (f"Task '{title}' depends on '{dep}', which {why}; it cannot "
                                   "be ordered until that is resolved.")
                del planned[tid]
                changed = True
                break

    return {"planned": planned, "by_id": by_id, "workers": workers, "usable": usable,
            "blocked_workers": blocked_workers,
            "unassigned": [{"task_id": tid, "task_title": by_id[tid].get("title"),
                            "tool_id": by_id[tid].get("tool_id"), "reason": reason}
                           for tid, reason in unassigned.items()],
            "current": _current_exposure(shift_id, workers)}


# ---------------------------------------------------------------- model

def _solve(prep: dict, cap_units: int, relax: bool, excluded: list[dict]) -> Optional[dict]:
    """One plan, or None when infeasible. `relax=False` keeps every worker strictly below the cap."""
    planned = prep["planned"]
    tids = sorted(planned, key=lambda t: (planned[t]["task"]["priority"], t))
    wids = [w["worker_id"] for w in prep["usable"]]
    n = len(tids)
    current = {w: prep["current"][w]["units"] for w in wids}
    load_ub = max(current.values(), default=0) + sum(planned[t]["units"] for t in tids)

    model = cp_model.CpModel()
    x = {(t, w): model.NewBoolVar(f"x_{i}_{j}")
         for i, t in enumerate(tids) for j, w in enumerate(wids) if w in planned[t]["eligible"]}
    for t in tids:
        model.AddExactlyOne(x[t, w] for w in planned[t]["eligible"])
    pos = {t: model.NewIntVar(0, max(0, n - 1), f"pos_{i}") for i, t in enumerate(tids)}
    if n > 1:
        model.AddAllDifferent(list(pos.values()))
    for t in tids:
        for dep in planned[t]["task"]["dependencies"]:
            if dep in pos:
                model.Add(pos[dep] < pos[t])

    loads, excesses = {}, []
    for j, w in enumerate(wids):
        load = model.NewIntVar(0, load_ub, f"load_{j}")
        model.Add(load == current[w] + sum(planned[t]["units"] * x[t, w]
                                           for t in tids if (t, w) in x))
        loads[w] = load
        if relax:
            excess = model.NewIntVar(0, load_ub, f"excess_{j}")
            model.Add(excess >= load - (cap_units - 1))
            excesses.append(excess)
        else:
            model.Add(load <= cap_units - 1)             # strictly below the cap
    max_load = model.NewIntVar(0, load_ub, "max_load")
    for load in loads.values():
        model.Add(max_load >= load)

    for previous in excluded:                            # alternative: a different assignment
        same = [x[t, w] for t, w in previous.items() if (t, w) in x]
        if len(same) == len(previous) and same:
            model.Add(sum(same) <= len(same) - 1)

    max_priority = max((planned[t]["task"]["priority"] for t in tids), default=1)
    weight = {t: max_priority - planned[t]["task"]["priority"] + 1 for t in tids}
    delay = sum(weight[t] * pos[t] for t in tids)
    delay_ub = sum(weight[t] * max(0, n - 1) for t in tids)

    def run() -> tuple[int, cp_model.CpSolver]:
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = SOLVE_SECONDS
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
        return solver.Solve(model), solver

    ok = (cp_model.OPTIMAL, cp_model.FEASIBLE)
    proven = True
    if relax:
        total_excess = sum(excesses)
        model.Minimize(total_excess)
        status, solver = run()
        if status not in ok:
            return None
        proven = proven and status == cp_model.OPTIMAL
        model.Add(total_excess <= int(round(solver.ObjectiveValue())))
    # total projected exposure is the same for every assignment (a task adds the same points
    # whoever does it), so the search is: lowest maximum first, then priority-weighted delay.
    model.Minimize(max_load * (delay_ub + 1) + delay)
    status, solver = run()
    if status not in ok:
        return None
    proven = proven and status == cp_model.OPTIMAL

    assignment = {t: next(w for w in planned[t]["eligible"] if solver.Value(x[t, w]))
                  for t in tids}
    return {"assignment": assignment,
            "position": {t: solver.Value(pos[t]) for t in tids},
            "load": {w: solver.Value(loads[w]) for w in wids},
            "weighted_delay": sum(weight[t] * solver.Value(pos[t]) for t in tids),
            "proven_optimal": proven}


# ---------------------------------------------------------------- output

def _plan_payload(index: int, prep: dict, solution: dict, cap: float, cap_units: int,
                  relaxed: bool) -> dict:
    planned, by_worker = prep["planned"], {w["worker_id"]: w for w in prep["workers"]}
    action, limit = config.ACTION_POINTS, config.LIMIT_POINTS
    order = sorted(solution["assignment"], key=lambda t: solution["position"][t])
    reasons: list[str] = []

    assignments = []
    for t in order:
        info, wid = planned[t], solution["assignment"][t]
        task, worker = info["task"], by_worker[wid]
        required = task.get("required_qualification")
        why = [info["score_reason"],
               (f"{worker['name']} holds the required qualification '{required}' (verified)."
                if required else
                f"{worker['name']} has verified qualifications; this task lists no required "
                "qualification.")]
        deps = [d for d in task["dependencies"] if d in solution["position"]]
        if deps:
            why.append("Ordered after its prerequisite(s): " + ", ".join(deps) + ".")
        assignments.append({
            "task_id": t, "task_title": task.get("title"), "tool_id": task.get("tool_id"),
            "worker_id": wid, "worker_name": worker["name"],
            "position": solution["position"][t] + 1, "priority": task["priority"],
            "additional_points": _pts(info["units"]), "reasons": why,
            "requires_approval": True})

    workers_out, total_units, max_units = [], 0, 0
    for worker in prep["workers"]:
        wid = worker["worker_id"]
        cur = prep["current"][wid]
        usable = wid in solution["load"]
        load = solution["load"].get(wid, cur["units"])
        added = load - cur["units"]
        total_units += load
        max_units = max(max_units, load)
        projected = load / SCALE
        notes = [f"Current exposure: {cur['note']}"]
        if not usable:
            notes.append("No task is proposed for this worker (see blocked_workers).")
        if projected >= limit:
            notes.append(f"The projected estimate reaches the exposure limit value ({limit:g} "
                         "points). The plan needs supervisor review before proceeding.")
        elif projected >= action:
            notes.append(f"The projected estimate is at or above the exposure action value "
                         f"({action:g} points). Supervisor review is needed and exposure should "
                         "be reduced as low as reasonably practicable.")
        else:
            notes.append(f"The projected estimate is below the exposure action value ({action:g} "
                         "points) on this estimate only. Exposure should still be kept as low as "
                         "reasonably practicable.")
        below_cap = load <= cap_units - 1
        excess = max(0, load - (cap_units - 1))
        if not below_cap:
            notes.append(f"The projected estimate is not below the planning cap ({cap:g} points)"
                         + (f": about {_fmt(excess / SCALE)} points over." if added else
                            " before any planned task is added."))
        workers_out.append({
            "worker_id": wid, "worker_name": worker["name"],
            "current_points": cur["points"], "current_basis": cur["basis"],
            "current_note": cur["note"],
            "added_points": _pts(added), "projected_points": _pts(load),
            "projected_level": exposure.level(projected),
            "below_cap": below_cap, "cap_excess_points": _pts(excess),
            "task_ids": [t for t in order if solution["assignment"][t] == wid],
            "notes": notes})

    if relaxed:
        over = [w for w in workers_out if not w["below_cap"]]
        reasons.append(
            f"No plan keeps every worker's projected estimate below the cap of {cap:g} points, "
            "so the cap was relaxed and this plan minimises the total excess instead. "
            + ", ".join(f"{w['worker_name']}: projected {_fmt(w['projected_points'])} points"
                        for w in over)
            + ". A supervisor must review tool choice, work method and task duration before "
              "any of this work proceeds.")
    else:
        reasons.append(f"Every worker's projected estimate stays below the planning cap of "
                       f"{cap:g} points on this estimate. That is a planning figure, not a "
                       "statement that the exposure is acceptable: keep it as low as reasonably "
                       "practicable.")
    reasons.append("Among such plans the highest per-worker projected estimate is minimised "
                   "first, then priority-weighted task delay. Total projected exposure is the "
                   "same whoever does a task, so reassignment spreads exposure; it does not "
                   "reduce it.")
    if any(prep["current"][w["worker_id"]]["basis"] != "estimated" for w in prep["workers"]
           if w["worker_id"] in solution["load"]):
        reasons.append("Current exposure is only estimated for the band wearer. For every other "
                       "worker it is not measured and taken as 0, so their projected figures "
                       "count planned tasks only.")
    if not solution["proven_optimal"]:
        reasons.append("The solver stopped at its time limit: this plan is feasible but not "
                       "proven to be the best one.")
    if not assignments:
        reasons.append("No task can be planned at the moment (see unassigned).")
    reasons.append("This is a proposal. Nothing is approved, assigned or stored automatically: "
                   "a supervisor must decide.")

    return {
        "plan_index": index, "label": LABEL, "status": "proposal", "requires_approval": True,
        "exposure_cap": cap, "cap_relaxed": relaxed,
        "workers": workers_out, "assignments": assignments, "order": order,
        "unassigned": [dict(u) for u in prep["unassigned"]],
        "max_projected_points": _pts(max_units),
        "total_projected_points": _pts(total_units),
        "weighted_delay": solution["weighted_delay"],
        "proven_optimal": solution["proven_optimal"],
        "reasons": reasons,
    }


def plan(shift_id: str, exposure_cap: float = 100.0, max_plans: int = 3) -> dict:
    """Up to `max_plans` alternative shift-plan proposals. Read-only: nothing is stored or published.

    `exposure_cap` is the per-worker planning cap in points (default: the action value); every
    worker's projected estimate must stay strictly below it. If no such plan exists the cap is
    relaxed, the excess is minimised, and the result says so (`cap_relaxed: True`).
    """
    cap = _number(exposure_cap)
    if cap is None or cap <= 0:
        raise ValueError("exposure_cap must be a finite number of points greater than 0")
    if isinstance(max_plans, bool) or not isinstance(max_plans, int) or max_plans < 1:
        raise ValueError("max_plans must be an integer of at least 1")
    cap_units = int(math.floor(cap * SCALE + 1e-9))

    prep = _prepare(shift_id)
    reasons: list[str] = []
    plans: list[dict] = []
    relaxed = False

    if prep["planned"]:
        excluded: list[dict] = []
        first = _solve(prep, cap_units, relax=False, excluded=excluded)
        if first is None:
            relaxed = True
            first = _solve(prep, cap_units, relax=True, excluded=excluded)
        solution = first
        while solution is not None:
            plans.append(_plan_payload(len(plans) + 1, prep, solution, cap, cap_units, relaxed))
            excluded.append(solution["assignment"])
            if len(plans) >= max_plans:
                break
            solution = _solve(prep, cap_units, relax=relaxed, excluded=excluded)
        if relaxed:
            reasons.append(
                f"No feasible plan keeps every worker's projected estimate below the cap of "
                f"{cap:g} points. The cap was relaxed: the plans minimise the excess and state "
                "it. They need supervisor review before any work proceeds.")
        if len(plans) < max_plans:
            reasons.append(f"Only {len(plans)} distinct assignment(s) exist under these "
                           "constraints.")
    else:
        reasons.append("No remaining task can be planned: every remaining task is either "
                       "unscorable, has no verified qualified worker, or there is none left.")

    if prep["unassigned"]:
        reasons.append(f"{len(prep['unassigned'])} task(s) are left unassigned with a stated "
                       "reason; no vibration value or qualification is assumed.")
    reasons.append("Worker availability is not recorded in the shift data: every listed worker "
                   "is taken as present unless marked otherwise. A supervisor must confirm.")
    reasons.append("These are proposals. Nothing is approved, assigned or stored automatically: "
                   "a supervisor must decide.")

    return {
        "label": LABEL, "status": "proposal", "requires_approval": True,
        "shift_id": shift_id, "exposure_cap": cap,
        "action_points": config.ACTION_POINTS, "limit_points": config.LIMIT_POINTS,
        "feasible_under_cap": bool(plans) and not relaxed,
        "cap_relaxed": relaxed,
        "plans": plans,
        "unassigned": [dict(u) for u in prep["unassigned"]],
        "blocked_workers": prep["blocked_workers"],
        "reasons": reasons,
    }
