# Design

How the system is built and why. Installation and usage are in the [README](../README.md); verification is in [TESTING.md](TESTING.md).

1. [What is being tested](#1-what-is-being-tested)
2. [Architecture](#2-architecture)
3. [The simulated bench](#3-the-simulated-bench)
4. [Error handling and data integrity](#4-error-handling-and-data-integrity)
5. [Processing and pass/fail](#5-processing-and-passfail)
6. [Adding a third instrument or a new test condition](#6-adding-a-third-instrument-or-a-new-test-condition)
7. [Technology choices and alternatives](#7-technology-choices-and-alternatives)

**Principles:** the test code can't tell a simulator from real hardware; every number lives in configuration; nothing is silently lost or replaced; problems are recorded at the smallest level they affect; every run can be replayed from its seed.

---

## 1. What is being tested

An **8-channel optical module** has 8 lasers in one package, so they share one temperature. A **TEC** (temperature controller) heats or cools the whole module; it approaches a setpoint gradually and then wobbles slightly. An **OPM** (optical power meter) with 8 inputs measures every channel at the same instant.

**dBm.** Optical power is logarithmic, relative to 1 mW: +3 dBm ≈ 2 mW, 0 dBm = 1 mW, −3 dBm ≈ 0.5 mW. So "average between −3 and +3 dBm" means *each channel is between half and double its expected brightness.* Because the scale is logarithmic, the average is taken in milliwatts and converted back. For variations this small (±0.1 dB) it differs from a plain dBm average by under 0.001 dB, but it is the physically correct method.

**A simulated reading** is made of four parts:

```
reading = nominal power (0 dBm) + channel offset (fixed, e.g. −0.40) + temperature effect (−0.1 dB/°C) + noise
```

The temperature effect is the **device link**: if the TEC drifts, all 8 channels drift together, which is why temperature is verified first. Noise shrinks with longer meter averaging (4× longer gives half the noise).

**Not every bad reading is the same kind of bad:**

| Kind | Example | Status |
|---|---|---|
| No answer | meter stays silent | `TIMEOUT` (communication problem, retried) |
| Garbled answer | `-0.#2` | `GARBLED` (communication problem, retried) |
| Impossible value | `+45 dBm`, `ERR` | `INVALID` (instrument answered with nonsense, not retried) |
| Real but out of spec | `−4.2 dBm` | `VALID`, fails the limits |

The last row matters most: **invalid and failing are different things.** A real out-of-spec reading is never retried until a "better" number appears.

**Why error rates matter.** The brief requires all 50 readings of every channel to be valid: 400 readings per run. At a 1% fault rate only about 2% of devices would pass, at 0.1% about 67%. So communication problems get **one retry**, which makes them almost never fatal (0.5% × 0.5% ≈ 1 in 40,000), and impossible values default to 0.1%, so about two in three runs pass and every outcome can be seen. All rates are configurable.

---

## 2. Architecture

```
config/*.yaml ─► Test runner ─► Drivers (TEC, OPM) ─► Transport (PyVISA) ─► Simulated bench
                     │                                                      ├─ TEC simulator  :5101
                     ▼                                                      ├─ OPM simulator  :5102
   raw_measurements.csv (written live) ─► analysis ─► summary.csv, charts  ├─ control port   :5100
                                                                            └─ virtual device
```

| Layer | Folder | Responsibility | Knows about |
|---|---|---|---|
| Core | `core/` | The instrument contract, how a reply is judged, **roles**, the transport, the registry | no specific instrument |
| Instruments | `instruments/<name>/` | Everything about one instrument: command words, settings, driver, simulator | only itself |
| Simulation | `simulation/` | Virtual device, simulated clock, seeds, faults, the bench program | no specific instrument |
| Sequence | `sequence/` | The recipe: settle, verify, measure, retry, clean up | **roles only** |
| Data | `data/` | Raw recording, statistics, pass/fail, reports, charts | measurements and limits |
| Config | `config.py` | Load and validate the YAML, with clear errors | each instrument's settings, via the registry |

**Roles** (`core/roles.py`) say what *any* instrument of a kind must do: a `TemperatureController` sets and reads temperature and switches its output; a `PowerMeter` reads one input or all of them. The runner asks for "the power meters", never for a specific driver. Dependencies point inward: `sequence/` → roles ← `instruments/<name>/`. That is why a new instrument folder needs no edits elsewhere.

**Every read returns a `Measurement`, never a bare number**: value (or blank), unit, status, the exact raw reply, attempt number and context. A driver never raises an error for a bad reading; it returns the right status. Errors are raised only when the test can't continue (an instrument can't be reached).

**Classifying a reply:** no reply in time → `TIMEOUT`; wrong format → `GARBLED`; `ERR`, NaN or outside the instrument's *range* (meter −60 to +10 dBm) → `INVALID`, value kept; otherwise `VALID`, even if outside the test *limits*. Each value in a `POW:ALL?` reply is labelled with its channel (`1:-0.120,2:0.031,…`), so one damaged value can't be confused with its neighbours: it alone is marked `GARBLED` and re-read with `POW? n`.

**Several instruments of a type** are a named list in the config. Each meter lists the device channels its inputs are wired to, so two 8-input meters can test a 16-channel module (`config/examples/two_meters_16ch.yaml`). Raw data records both the device channel and the meter input. With several TECs, a condition is verified only if every TEC settles.

---

## 3. The simulated bench

The simulators run as a **separate program** (the bench), each instrument on its own network port, speaking text commands like a real instrument (`TEMP?`, `POW:ALL?`). Command words are defined once per instrument in `commands.py`, shared by its driver and simulator, like a real instrument's manual. Timeouts are real: the simulator stays silent and the driver's timeout fires.

All simulators share **one virtual device**: the TEC heats it, the meter reads its light, and the light depends on its temperature, as in the real world. The TEC approaches its setpoint exponentially (time constant 8 s, so about 27 s from 25 °C to within 0.5 °C of 40 °C); a configurable steady-state error simulates a controller that settles in the wrong place.

**Faults** are injected at configurable rates: whole-reply timeouts and garbling (0.5% each), and per-channel garbling (0.2%) and impossible values (0.1%). The simulator never sends a late reply, because a late reply would be taken as the answer to the next question and shift every reading after it; the driver also clears leftover input before every retry.

**Simulated time.** Settling and drift follow the bench's own clock. `time_scale` sets how long the program really waits (1 = real time, 0 = instant), and **the data is identical at any time scale**. A control port (5100, never used by drivers, absent with real hardware) advances that clock and lets tests force a specific fault ("the next meter reply times out") or kill an instrument.

**Reproducibility.** Separate random sequences are derived from one seed for each purpose and instrument (`"42:opm_a:input3:noise"`), so a retry on one channel can't shift another's numbers and adding a second meter leaves the first meter's data unchanged. The seed is always saved; re-running with it reproduces every value, status and verdict.

**Where the bench runs** (`simulation.mode`), with identical data in every mode:
- `network` (default): a background program on this computer, started and stopped by `ate run`. `ate bench` starts one separately for debugging.
- `docker`: the same bench in a Docker container, with its ports opened to this computer only. The container runs the current `ate` code, so code changes need no image rebuild. Docker is checked before anything is created, and the container is removed afterwards.
- `in_process`: inside the test program with no network, so the automated tests run in seconds.

---

## 4. Error handling and data integrity

| Status | Meaning | Retried? |
|---|---|---|
| `VALID` | sensible reading (may still fail limits) | never |
| `INVALID` | impossible value; raw text kept | no |
| `TIMEOUT` | no reply | once |
| `GARBLED` | reply not understood; raw text kept | once |
| `NOT_MEASURED` | planned but never attempted (instrument lost, run aborted) | n/a |

| Situation | Action |
|---|---|
| Whole reply timed out or garbled | retry the same command once |
| One channel garbled inside a good `POW:ALL?` | re-read only that channel |
| Temperature doesn't settle in time | one more full settling attempt |
| One meter lost | its remaining readings `NOT_MEASURED`; other meters carry on; status `COMPLETE_WITH_ERRORS` |
| TEC lost, or every meter lost | stop, mark the rest `NOT_MEASURED`, clean up, summarise what exists; status `ABORTED` |

Every attempt is its own row; the **final** attempt of each reading counts. Channels that needed retries on more than 5% of readings get a warning, since a pass that needed many retries suggests a flaky connection.

**Nothing is lost:** the raw CSV is written and flushed row by row, so a crash at reading 237 keeps the first 236; the instrument's exact reply is kept even when it's garbage; `run_info.json` says `RUNNING` until the end, so an interrupted run is obvious; results folders are never reused.

**Cleanup always happens**, after errors and Ctrl+C too: TEC output switched off, every connection closed, the bench (or its container) stopped. Bad settings, a busy port or missing Docker are reported before any results folder is created.

---

## 5. Processing and pass/fail

The analysis **always reads from the raw CSV**, live and when re-processing an old run, so the summary can never disagree with the raw data. Per channel and condition, from valid readings only: average (in mW, shown in dBm), min, max, standard deviation, and counts by status.

- **Temperature is verified** when readings stay within ±0.5 °C for 10 s **and** change by no more than 0.1 °C over that window. The second rule was added after testing showed a controller still heating could pass *through* the band and be wrongly verified (TESTING.md §5).
- **A channel passes** if every reading is valid (or no more than `max_bad_readings`, default 0) and its average is within −3 to +3 dBm, inclusive.
- **The device passes** only if temperature is verified and every channel passes, in every condition.
- If temperature fails, the light is still measured by default for troubleshooting, but the device can't pass.

`ate reprocess` re-judges a saved run, for example with new limits, writing a new summary beside the original. Each run also gets three charts per condition: power per channel (box and whisker against the limits), temperature over time against the target band, and a grid showing what happened to every reading. Charts are optional (matplotlib) and drawn after the data is saved, so they can never cost a run its results.

**Why CSV rather than a database:** the brief requires CSV, a run is only a few hundred rows, appending one line at a time is the simplest crash-safe record, and anyone can open it. A database could later index runs for history, built from these files.

---

## 6. Adding a third instrument or a new test condition

**Another instrument of an existing type** (a second meter or TEC): config only. Add it to `instruments:` with a new name; the bench starts a simulator for it and the runner picks it up by role.

**A new type of instrument**, for example an optical switch that routes one channel at a time to a single-input meter:
1. Create `instruments/switch/` with the same files as `tec/`: command words, settings, driver, simulator, and an `__init__.py` declaring its type. The registry finds it and the bench gives its simulator a port automatically.
2. Add a `Switch` role to `core/roles.py` (what any switch must do: `route(n)`).
3. Add a measurement mode that uses it (`read_mode: switched`) in the runner.
4. Add it to the config, and its tests in `tests/instruments/switch/`.

The TEC and meter folders, recorder, analysis, reports and bench don't change. A test proves the registry finds a new folder with no other edits.

**A different brand of meter:** copy `instruments/opm/`, change its command words. It still fills the `PowerMeter` role, so nothing outside the new folder changes.

**A new test condition:** another temperature is config only (add to `sequence.conditions`; see `config/examples/two_temperatures.yaml`). A new kind of condition, such as laser bias current, needs a field in the condition settings and one step in the runner; measurement and reporting are reused.

**Real hardware:** set `simulation.enabled: false` and enter the instruments' addresses. Nothing else changes.

**Several modules:** nothing is global (serial, results folder, bench and instruments all belong to a run), so several modules can be tested as separate runs, one after another or side by side on separate ports.

---

## 7. Technology choices and alternatives

| Need | Choice | Why |
|---|---|---|
| Instrument communication | **PyVISA** + `pyvisa-py` | The standard for lab instruments; real timeouts; the same code drives real hardware. Used in one class only, so it could be swapped in one file. |
| Settings | **pydantic** + **PyYAML** | Typed validation with clear errors; unknown settings are rejected, not ignored |
| Simulator networking | **asyncio** | One event loop, no locks, predictable |
| Statistics | standard library | 400 numbers don't need numpy |
| Tests / charts | **pytest** / **matplotlib** (optional) | Standard |

| Considered | Decision | Reason |
|---|---|---|
| PyVISA-sim | Not used | Fixed question/answer pairs; can't model settling, noise or random faults |
| PyMeasure / QCoDeS | Ideas only | They would supply the architecture, which is what's being assessed |
| Threads per channel | Not used | One connection holds one conversation at a time, and threads would break reproducibility. The meter reads all channels at once itself (`POW:ALL?`). |
| Database | Not used | Tiny data volume, and CSV is required |
| Docker | Optional mode only | Not needed: separate processes already give independent instruments. A second container for the test program was tried and removed (it only helps without Python installed). One container per instrument would need the shared virtual device split into its own network service, so it is left as future work. |
