# Design Document: Modular Python Test Automation System

**Project:** Automated test system for an 8-channel optical module (simulated instruments)
**Language:** Python 3.11+
**Status:** v1.1, implemented (milestones 1–11 of §16). Changes made during implementation are listed in §20.

---

## Contents

1. [Purpose and scope](#1-purpose-and-scope)
2. [Understanding the data](#2-understanding-the-data)
3. [Requirements traceability](#3-requirements-traceability)
4. [Architecture overview](#4-architecture-overview)
5. [The simulated test bench](#5-the-simulated-test-bench)
6. [Instrument drivers and communication](#6-instrument-drivers-and-communication)
7. [Configuration](#7-configuration)
8. [Test sequence flow](#8-test-sequence-flow)
9. [Error handling and data integrity](#9-error-handling-and-data-integrity)
10. [Data storage and processing](#10-data-storage-and-processing)
11. [Output file formats](#11-output-file-formats)
12. [Reproducibility](#12-reproducibility)
13. [Testing approach](#13-testing-approach)
14. [Extensibility](#14-extensibility)
15. [Technology choices and alternatives considered](#15-technology-choices-and-alternatives-considered)
16. [Build plan](#16-build-plan)
17. [Risks and mitigations](#17-risks-and-mitigations)
18. [Open decisions](#18-open-decisions)
19. [AI assistance disclosure](#19-ai-assistance-disclosure)
20. [Changes made during implementation](#20-changes-made-during-implementation)

---

## 1. Purpose and scope

### Goal
Build a Python system that:
1. Talks to two instruments (a temperature controller and an optical power meter),
2. Runs a fixed test recipe on an 8-channel optical module,
3. Processes the measurements and decides pass/fail,
4. Saves raw data and a summary for every run.

Physical instruments are not available, so the instruments are **simulated**, but in a way that the test software cannot tell apart from real hardware.

### Design principles
- **The test code doesn't know it's talking to a simulator.** Swapping in real hardware means changing an address in the config file, not code.
- **Every number lives in configuration.** Setpoints, limits, timing, noise and fault rates are all adjustable without touching code.
- **Nothing is ever silently lost or replaced.** Every attempt at every reading is recorded with its status.
- **Problems are recorded at the smallest level they actually affect.** One bad channel affects one row, and one lost message affects one round.
- **Every run can be replayed exactly** from its saved seed.

### In scope
- Simulated temperature controller (TEC) and 8-input optical power meter (OPM)
- **Any number of instruments of each type** (e.g. two 8-input meters for a 16-channel module), each simulated independently
- A shared "virtual device" so temperature physically affects optical power
- Automated test sequence, data processing, CSV reporting
- Configurable everything, with validation
- Automated tests, including forced-failure scenarios
- Support for more than one temperature condition per run

### Out of scope (for now)
- User interface (command line only)
- Docker / containers
- Database (CSV files are the record; see section 10)
- Testing multiple devices in a single run (the design doesn't block it; see section 14)
- Publishing to GitHub (to be decided later)

---

## 2. Understanding the data

This section explains what is being simulated and why the simulation behaves the way it does.

### 2.1 The device under test
An **8-channel optical module** is a small component, the kind used in data-centre networking, that sends data as light. It has 8 independent laser outputs ("channels" or "lanes"). The test confirms each channel is bright enough and that readings are trustworthy.

All 8 lasers sit inside one small package, so **they share one temperature**.

### 2.2 The temperature controller (TEC)
- A thermoelectric cooler is a small heater/cooler plate the module sits on. There is **one TEC for the whole module**, not one per channel.
- **Why it matters:** laser brightness changes with temperature. Locking the temperature first makes optical measurements comparable and repeatable.
- **Realistic behaviour:**
  - It doesn't jump to the setpoint instantly. It approaches it gradually and settles, typically over tens of seconds.
  - Once settled, the reading wobbles slightly (a few hundredths of a degree).
  - Sometimes it settles slightly off target (a fault we can simulate).

### 2.3 The optical power meter (OPM)
- Measures how bright the light is. There is **one meter with 8 inputs**, one per channel, not 8 meters.
- A multi-input meter measures all inputs at the same instant in its own hardware. Software asks once and gets 8 numbers back.

### 2.4 The unit: dBm
Optical power is reported in **dBm**, a logarithmic scale relative to 1 milliwatt:

| dBm | Milliwatts | Meaning |
|---|---|---|
| +3 | ~2.0 mW | about **twice** the nominal brightness |
| 0 | 1.0 mW | nominal |
| −3 | ~0.5 mW | about **half** the nominal brightness |
| −10 | 0.1 mW | one tenth |

So the acceptance rule **"average between −3 and +3 dBm"** means: *the channel is somewhere between half and double its expected brightness.*

### 2.5 What realistic readings look like
Each reading is made of four parts:

```
reading = nominal power                (0 dBm)
        + channel offset               (each channel is slightly different, e.g. −0.40 or +0.20)
        + temperature effect           (lasers dim as they get hotter)
        + random noise                 (small flicker, e.g. ±0.05 dB)
```

- **Channel offsets:** real channels are never identical. Giving each one a small fixed offset makes the data look like a real device instead of 8 copies of the same random numbers.
- **Temperature effect (the device link):** about −0.1 dB per °C above the reference temperature (configurable). If the TEC drifts, **all 8 channels drift together**, which is exactly why the temperature check comes first.
- **Noise depends on how long the meter looks.** Longer averaging time gives a steadier reading. Noise shrinks with the square root of averaging time (4× longer gives half the noise). Changing the averaging time in config visibly changes the standard deviation in the results.

### 2.6 What going wrong looks like
Not every bad reading is the same kind of bad. The system distinguishes:

| Kind | Example | What it means | Status |
|---|---|---|---|
| **No answer** | meter stays silent | message lost (communication problem) | `TIMEOUT` |
| **Garbled answer** | `-0.#2`, cut-off reply | message damaged in transit (communication problem) | `GARBLED` |
| **Impossible value** | `+45 dBm`, `ERR`, not-a-number | instrument answered clearly, but with nonsense (measurement problem) | `INVALID` |
| **Real but out of spec** | `−4.2 dBm` | the device really is dim | `VALID` (fails limits) |

The last row matters most: **invalid and failing are different things.** An out-of-spec reading is real data and must never be retried until a "better" number appears.

### 2.7 Why the error rate matters so much
The rule is "all 50 readings on a channel must be valid." With 400 readings per run, small error rates compound quickly:

| Chance a single reading is unusable | Chance one channel is fully clean (50 readings) | Chance all 8 channels clean (400 readings) |
|---|---|---|
| 1% | ~60% | ~2% |
| 0.5% | ~78% | ~13% |
| 0.1% | ~95% | ~67% |

With a 1% failure rate, almost every device would fail. That's a consequence of the rules, not a bug. Two design choices follow:
- Communication problems (timeouts, garbled replies) get **one retry**, which makes them almost never fatal (0.5% × 0.5% ≈ 1 in 40,000).
- Impossible values are **not** retried, and their default rate is 0.1%, so by default **roughly two in three runs pass** and you see a realistic mix of outcomes.

All rates are configurable.

### 2.8 Averaging dBm correctly
dBm is logarithmic, so the physically correct average converts each reading to milliwatts, averages those, and converts back:

```
average_dBm = 10 × log10( mean( 10^(reading_dBm / 10) ) )
```

For variations as small as ours (±0.1 dB), this differs from a plain average of dBm values by under 0.001 dB. We still use the correct method and document why. Min, max and standard deviation are reported in dB, as is normal practice.

### 2.9 What the timeframe means
With the default timing (100 ms per reading, all 8 channels at once), 50 readings take about **5 seconds**.
- Over **seconds**, the standard deviation mostly reflects **meter noise**.
- Over **minutes**, slow temperature drift creeps in and the standard deviation starts reflecting **device stability**.

So the averaging time and interval are saved with every result. Without them, a standard deviation can't be interpreted.

---

## 3. Requirements traceability

How each requirement in the brief is met:

| Brief requirement | Where it is addressed |
|---|---|
| A. Simulated TEC and OPM: init, configure, read, close | §5, §6 |
| A. Small random variation in OPM | §2.5, §5.2 |
| A. Occasional invalid readings / communication failures | §2.6, §5.4 |
| B. Steps 1–8 of the test sequence | §8 |
| B. Nominal power around 0 dBm | §2.5, §7 |
| C. Average, min, max, std dev, valid/invalid counts | §10.3 |
| C. Acceptance criteria and pass/fail rules | §10.4 |
| D. Errors don't terminate the test | §9 |
| D. Failed measurements recorded with status | §9.1, §11.1 |
| D. Data preserved for troubleshooting | §9.4, §10.1 (`raw_response` column, streaming writes) |
| D. Failures distinguished from valid values | §2.6, §9.1 |
| D. Connections always closed, including on errors | §9.5 |
| E. Raw measurement CSV (all required columns) | §11.1 |
| E. Summary CSV (all required columns) | §11.2 |
| E. Separate results directory per run | §10.1 |
| Design: modularity, separation, configurability, extensibility | §4, §7, §14 |
| Deliverables: README, samples, design explanation, testing approach, AI disclosure | §13, §14, §16, §19 |
| Optional: config files (YAML) | §7 |
| Optional: automated unit tests | §13 |
| Optional: visualisation | §16 (stretch) |

---

## 4. Architecture overview

### 4.1 The big picture

```
┌──────────────────────────┐
│  config/default.yaml     │   all values: setpoints, limits, timing, noise, fault rates
└────────────┬─────────────┘
             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ TEST PROGRAM (Python)                                                │
│                                                                      │
│  Config loader ─► Test runner (the recipe)                           │
│  + validation        │                                               │
│                      ├─► TEC driver ──┐                              │
│                      ├─► OPM driver ──┤  same interface for real     │
│                      │                │  and simulated hardware      │
│                      ▼                ▼                              │
│               Raw recorder      Transport (PyVISA over network,      │
│              (writes each       or in-process for fast tests)        │
│               row instantly)          │                              │
│                      │                │                              │
│                      ▼                │                              │
│               Analysis + report       │                              │
└──────────────────────┬────────────────┼──────────────────────────────┘
                       │                │  text commands, e.g. "TEMP?", "POW:ALL?"
                       ▼                ▼
          results/<run-id>/     ┌──────────────────────────────────────┐
          raw_measurements.csv  │ SIMULATED TEST BENCH (separate prog.) │
          summary.csv           │                                      │
          run_info.json         │  TEC simulator  (port 5101) ──┐      │
          config_used.yaml      │                               ▼      │
          run.log               │                     Virtual device   │
                                │                (temperature, 8 lasers)│
                                │                               ▲      │
                                │  OPM simulator  (port 5102) ──┘      │
                                │  Sim control    (port 5100)  clock,  │
                                │                              faults  │
                                └──────────────────────────────────────┘
```

The diagram shows the default setup: one TEC and one 8-input meter. Any number of instruments of each type can be configured. Each gets its own simulator and its own port, and all of them attach to the same virtual device (§6.7).

### 4.2 Layers and their single responsibilities

| Layer | Folder | Responsibility | Knows about |
|---|---|---|---|
| **Core** | `core/` | The contract every instrument follows, how a reply is judged, **roles** (what any temperature controller / power meter must do), the transport (send a line, get a line), the registry | no specific instrument |
| **Instruments** | `instruments/<name>/` | Everything about **one** instrument: its command words, config settings, driver and simulator | only itself and `core/` (its simulator also uses `simulation/` helpers) |
| **Simulation** | `simulation/` | The bench as a whole: virtual device (the physics), simulated clock, seeds, fault injection, ports, the bench program | no specific instrument (it finds simulators through the registry) |
| **Test sequence** | `sequence/` | The recipe: settle, verify, measure, retry, clean up; sets up real or simulated instruments | **roles only**, never a specific instrument |
| **Data** | `data/` | Record each attempt the instant it happens; compute stats and pass/fail from the raw file; write reports | measurements and limits |
| **Config** | `config.py` | Load the YAML, validate it, give clear errors | each instrument's settings model, through the registry |

The **test sequence never talks to a simulator directly**, and **no simulator imports test-sequence code**. A driver and its simulator share only their instrument's `commands.py`, which is exactly the "contract" a real instrument's manual provides.

**Dependency direction:** `sequence/` → roles in `core/` ← `instruments/<name>/`. The sequence and the instruments both depend on the roles, never on each other. That is why a new instrument folder needs no changes anywhere else.

### 4.3 Project layout

```
xscape-test-automation/
├── README.md
├── pyproject.toml
├── config/
│   ├── default.yaml              # values from the brief
│   └── examples/
│       ├── two_temperatures.yaml # 25 °C then 40 °C
│       ├── weak_channel.yaml     # channel 5 too dim → demonstrates FAIL
│       ├── flaky_meter.yaml      # high fault rates → demonstrates error handling
│       ├── tec_wont_settle.yaml  # TEC settles 0.8 °C hot → temperature verification FAIL
│       └── two_meters_16ch.yaml  # 16-channel module read by two 8-input meters
├── docs/
│   ├── DESIGN.md                 # this document
│   └── TESTING.md                # how the system was verified
├── src/ate/
│   ├── cli.py                    # `ate run`, `ate bench`, `ate reprocess`, `ate validate`
│   ├── config.py                 # config models + validation + `extends`
│   ├── logging_setup.py          # run.log + terminal output
│   ├── core/                     # shared machinery: knows no specific instrument
│   │   ├── settings.py           # settings every instrument has (name, address, timeouts)
│   │   ├── instrument.py         # the common Instrument contract + reply classification
│   │   ├── roles.py              # what ANY temperature controller / power meter must do
│   │   ├── transport.py          # PyVISA transport
│   │   └── registry.py           # finds every instrument folder automatically
│   ├── instruments/              # one folder per instrument, nothing else
│   │   ├── tec/
│   │   │   ├── __init__.py       # declares INSTRUMENT_TYPES (how the registry finds it)
│   │   │   ├── commands.py       # its command words (shared by driver + simulator)
│   │   │   ├── settings.py       # its config settings
│   │   │   ├── driver.py         # talks to it
│   │   │   └── simulator.py      # pretends to be it
│   │   └── opm/                  # same five files
│   ├── simulation/               # the bench as a whole: knows no specific instrument
│   │   ├── device.py             # virtual device (temperature + lasers)
│   │   ├── clock.py              # simulated clock
│   │   ├── rng.py                # seeded random sequences per purpose
│   │   ├── faults.py             # fault injection (random + forced)
│   │   ├── simulator.py          # what every simulator has in common (kill, dispatch)
│   │   ├── loopback.py           # in-process transport (fast tests)
│   │   ├── bench.py              # runs simulators on their ports + control port
│   │   └── control.py            # ports, control client, background bench process
│   ├── sequence/
│   │   ├── runner.py             # the test recipe + retry policy
│   │   ├── session.py            # sets up real / network-simulated / in-process instruments
│   │   ├── clock.py              # how the runner waits (real or simulated time)
│   │   └── stability.py          # temperature stability rule (shared with analysis)
│   └── data/
│       ├── models.py             # Measurement, Status
│       ├── recorder.py           # streaming raw CSV writer
│       ├── analysis.py           # statistics + pass/fail
│       └── report.py             # summary CSV, run_info.json, terminal table, reprocess
├── tests/                        # mirrors src/ate/: each part tested where it lives
│   ├── helpers.py                # shared helpers: build a config, run, read results, fake instrument
│   ├── conftest.py               # shared pytest fixtures (free network ports)
│   ├── core/
│   ├── instruments/tec/, opm/
│   ├── simulation/
│   ├── sequence/
│   ├── data/
│   ├── config/
│   └── system/                   # only place the whole program is tested together
└── sample_results/               # committed example runs
```

---

## 5. The simulated test bench

### 5.1 Why a separate "bench" program
Real instruments are separate boxes on a network. They receive text commands and reply. Our simulators do the same:
- They run as a **separate program** (the bench), each instrument on its **own port**, so each has its own conversation and its own failures.
- The bench starts **one simulator for every instrument listed in the config**: one TEC and one meter by default, or two meters, or any other combination. In simulation mode, ports are assigned automatically from a starting port so they can never clash. The ports actually used are recorded in `run_info.json`.
- Inside the bench, both instruments share **one virtual device**. This is the device link: the TEC heats it, the meter reads its light, and the light depends on its temperature. In the real world the physics also lives in the device, not in the instruments.
- Timeouts are **real**: the simulator actually stays silent, and the driver's real timeout fires.

The bench runs on a single asyncio event loop, so the TEC and OPM can never act on the shared device at the same moment. No locks are needed, and the behaviour stays predictable.

**Starting it:** `ate run` starts the bench automatically in the background and always shuts it down afterward. For debugging, `ate bench` starts it on its own in a separate terminal.

### 5.2 Virtual device model

**Optical power for channel *c* at device temperature *T*:**

```
P(c, T) = nominal_power_dbm
        + channel_offset_db[c]
        + temp_coefficient_db_per_c × (T − reference_temp_c)
        + noise

noise ~ Normal(0, σ)   where   σ = noise_db_at_100ms × sqrt(100 / averaging_time_ms)
```

Defaults: nominal 0 dBm, offsets within ±0.4 dB, −0.1 dB/°C, reference 40 °C, σ = 0.05 dB at 100 ms.

**Device temperature** follows the TEC (see 5.3).

### 5.3 TEC simulator behaviour

**Settling model.** After a new setpoint, temperature approaches it exponentially:

```
T(t) = setpoint + steady_state_error + (T_start − setpoint) × e^(−t / time_constant)
reading = T(t) + jitter        jitter ~ Normal(0, jitter_c)
```

Defaults: start at 25 °C (ambient), time constant 8 s, jitter 0.02 °C, steady-state error 0 °C. From 25 °C to within 0.5 °C of 40 °C takes about 8 × ln(15 / 0.5) ≈ 27 simulated seconds.

Setting `steady_state_error_c: 0.8` simulates a TEC that "settles" in the wrong place. This fault can't be fixed by retrying, so it exercises the temperature-fail path.

**Command table (TEC)**, defined once in `instruments/tec/commands.py` and used by both the driver and the simulator:

| Command | Reply | Meaning |
|---|---|---|
| `*IDN?` | `XSIM,TEC-1000,tec_main,1.0` | identify (the third field is the instrument's name) |
| `*RST` | `OK` | reset to defaults |
| `OUTP ON` / `OUTP OFF` | `OK` | enable/disable heating and cooling |
| `OUTP?` | `ON` | is heating/cooling on? |
| `TEMP <value>` | `OK` | set setpoint (°C) |
| `TEMP:SET?` | `40.000` | read back setpoint |
| `TEMP?` | `39.982` | read actual temperature |
| bad value, e.g. `TEMP hot` | `ERR:BAD_VALUE` | |
| *(anything else)* | `ERR:UNKNOWN_COMMAND` | |

Real TECs require the output to be switched on before they heat or cool, and the driver always switches it **off** on close, so the device isn't left heated.

### 5.4 OPM simulator behaviour

**Command table (OPM)**, defined once in `instruments/opm/commands.py`:

| Command | Reply | Meaning |
|---|---|---|
| `*IDN?` | `XSIM,OPM-8,opm_a,1.0` | identify |
| `*RST` | `OK` | reset |
| `UNIT DBM` | `OK` | report in dBm (other units: `ERR:UNSUPPORTED_UNIT`) |
| `AVG <ms>` | `OK` | set averaging time |
| `AVG?` | `100` | read averaging time |
| `POW? <n>` | `-0.120` | read one input (bad input number: `ERR:BAD_CHANNEL`) |
| `POW:ALL?` | `1:-0.120,2:0.031,3:-0.412,…,8:0.020` | read all 8 at the same instant |

An input with no fibre wired to it reads a dark level of about −75 dBm.

**Each value in `POW:ALL?` is labelled with its channel.** If one value is damaged, the others are still unambiguously tied to the right channel.

A `POW:ALL?` reading uses one device temperature and one moment in time for all 8 channels. Each channel draws noise from **its own random sequence** (§12).

### 5.5 Fault injection
Each fault is decided independently, using a random sequence reserved for faults, so changing a fault rate never changes the noise values.

| Fault | Applies to | Effect | Default rate |
|---|---|---|---|
| `timeout_rate` | TEC, OPM (whole reply) | simulator sends nothing | 0.5% |
| `garbled_rate` | TEC, OPM (whole reply) | reply is damaged so it can't be read (for `POW:ALL?`, no channel labels survive) | 0.5% |
| `invalid_rate` | TEC | reply is `ERR` or physically impossible (e.g. 999) | 0.1% |
| `channel_garbled_rate` | OPM, per channel | one value in `POW:ALL?` is corrupted, label intact | 0.2% |
| `channel_invalid_rate` | OPM, per channel | one value is `ERR` or out of meter range (e.g. +45) | 0.1% |

Faults apply to **measurement queries** (`TEMP?`, `POW?`, `POW:ALL?`). Setup commands (`TEMP 40`, `AVG 100`, …) are reliable in simulation, though the drivers still retry them and fail clearly if they never answer.

The simulator **never sends a late reply**. A late reply would be picked up as the answer to the *next* question and silently shift every reading after it. As a second layer of protection, the driver clears any leftover input before every retry.

### 5.6 Simulated clock and time compression
- The bench keeps its own **simulated clock**. Device temperature, settling and drift all follow this clock, not the real one.
- A reading advances the simulated clock by its averaging time. Waits in the test sequence advance it by the wait length.
- `time_scale` controls how long the program *actually* waits: `1.0` = real time, `0.1` = 10× faster, `0` = instant. **The data is identical at any time scale**, so a 10-minute stability test can run in a second.
- Waits go through a **Clock** object in the test program. With real hardware it's a normal sleep. With the simulator it also tells the bench's **control port** (5100) to advance its clock. The control port isn't an instrument. Drivers never use it, and it doesn't exist with real hardware.
- The control port also lets automated tests **force a specific fault** (e.g. "next OPM reply times out"), so error scenarios are tested deliberately rather than waiting for luck.
- `time_scale` is limited to 0–1 (instant up to real time), so simulated delays can never cause timeouts by themselves.

**Control port commands:**

| Command | Reply | Purpose |
|---|---|---|
| `SIM:PING` | `OK` | readiness check |
| `SIM:TIME?` | `37.400000` | simulated seconds since start |
| `SIM:ADVANCE <s>` | `OK` | move the simulated clock forward |
| `SIM:SEED?` | `42` | the bench's seed (used with `--external-bench`) |
| `SIM:FAULT <instrument> <kind> [channel]` | `OK` | force the next matching fault |
| `SIM:KILL <instrument> [after_n_queries]` | `OK` | make an instrument "die", now or later |
| `SIM:PORTS?` | `tec_main=5101,opm_a=5102` | ports in use |
| `SIM:SHUTDOWN` | `OK` | stop the bench |

`ate run --external-bench` connects to a bench started separately with `ate bench` instead of starting its own. It takes the seed from that bench.

### 5.7 Fast in-process mode
For automated tests, the simulators can run **inside the test program** with no network. An in-process transport (`simulation/loopback.py`) calls the simulator directly. A simulated timeout returns immediately as a timeout error instead of waiting. The drivers and test runner can't tell the difference, and full runs take a fraction of a second.

---

## 6. Instrument drivers and communication

### 6.1 Common instrument interface
Every instrument driver, simulated or real, extends `Instrument` (`core/instrument.py`) and so provides:

| Method | Purpose |
|---|---|
| `connect()` | open the connection, confirm identity (`*IDN?`) |
| `configure(app_config)` | apply instrument settings from the run's config (e.g. the meter's averaging time) |
| `close()` | leave the instrument safe (e.g. TEC output off), then disconnect. Safe to call twice |
| `identity` | the instrument's ID string, recorded in `run_info.json` |

Drivers also act as context managers (`with ... as tec:`), so closing happens automatically.

Role-specific methods sit on top (defined in `core/roles.py`, implemented in each instrument's `driver.py`):
- **TemperatureController:** `set_temperature(c)`, `read_temperature() → Measurement`, `output(on)`
- **PowerMeter:** `read_input(n) → Measurement`, `read_all() → ReadAllResult`

### 6.2 Every read returns a Measurement, never a bare number

A `Measurement` (`data/models.py`) is one attempt at one reading, exactly as it will appear as a row in the raw CSV (§11.1):

```
Measurement
  instrument, instrument_type, channel, instrument_input,
  value (or None), unit,
  status: VALID | INVALID | TIMEOUT | GARBLED | NOT_MEASURED,
  raw_response (exact text received, or None),
  attempt, timestamp_utc, sim_elapsed_s,
  condition, phase, measurement_index, settle_attempt, note
```

The driver fills in what it knows (instrument, value, status, raw reply). The test sequence adds the context (condition, phase, index, attempt).

A driver **never raises an error for a bad measurement**. It returns a Measurement with the right status. Errors are raised only for things the test can't continue past (e.g. the instrument can't be reached at all).

### 6.3 Classifying a reply

```
no reply within timeout                        → TIMEOUT    (retryable)
reply doesn't match the expected format        → GARBLED    (retryable)
reply is "ERR", NaN, or outside instrument range → INVALID  (not retryable; value kept)
otherwise                                      → VALID      (even if outside test limits)
```

Instrument range comes from config (meter: −60 to +10 dBm; TEC: −10 to 100 °C). This is different from the **test limits** (−3 to +3 dBm). A reading outside the *range* is impossible, and a reading outside the *limits* is a real failing result.

### 6.4 Parsing `POW:ALL?`
1. No reply → all 8 channels `TIMEOUT`, so retry the whole `POW:ALL?`.
2. No recognisable `n:value` fields at all → all 8 `GARBLED`, so retry the whole command.
3. Otherwise, each channel is judged on its own:
   - field present and a sensible number → `VALID`
   - field present, impossible value → `INVALID` (no retry)
   - field missing or corrupted → `GARBLED` for **that channel only**, re-read with `POW? n`

Nothing good is ever thrown away because of something bad next to it.

### 6.5 Transport
- **Network:** PyVISA with the pure-Python `pyvisa-py` backend, addresses like `TCPIP::127.0.0.1::5102::SOCKET`. The same code works with real network-attached instruments.
- PyVISA is used in **one class only** (`VisaTransport` in `core/transport.py`), and only six of its features: opening a connection, `query`, telling a timeout from other errors, `clear`, and `close`. Everything else depends on the program's own "send a line, get a line" interface, so the communication method could be swapped by changing one file.
- When a reply times out, the transport checks whether the instrument is still there at all. If nothing is listening any more, it reports a **lost connection** rather than a timeout.
- PyVISA opens network connections lazily, so an address with nothing behind it only fails on the first command. Since `connect()` always starts with `*IDN?`, that still surfaces immediately as a clear error.
- **In-process:** direct calls to the simulator (§5.7).
- The driver's **timeout must exceed the averaging time**, enforced by config validation (§7.2).

### 6.6 Registry
Config refers to instruments by a **type name** (`sim_tec`, `sim_opm_8ch`). Each instrument folder declares its type in its `__init__.py`:

```python
INSTRUMENT_TYPES = [InstrumentType(name="sim_tec", config_model=TecSettings,
                                   driver=TecDriver, simulator=TecSimulator)]
```

The registry (`core/registry.py`) looks through every folder under `instruments/` and collects these. **Adding an instrument means adding a folder; nothing else is edited.** A test proves this by dropping a new folder in and checking it's found.

**Roles** (`core/roles.py`) define what *any* instrument of a kind must do, whatever its brand:

| Role | Must provide | Gets for free |
|---|---|---|
| `TemperatureController` | `set_temperature()`, `output()`, `read_temperature()` | switching the output off on close |
| `PowerMeter` | `read_input()`, `read_all()` | input → device-channel mapping, building per-channel Measurements |

The test sequence only ever asks for "the temperature controllers" and "the power meters" by role. It never names a specific driver.

### 6.7 Multiple instruments of the same type
Instruments are a **named list** in the config, not fixed slots. The test runner never asks for "the TEC" or "the meter". It asks for **all instruments with a given role**.

**Names.** Every instrument has a unique name (`tec_main`, `opm_a`, `opm_b`). The name appears in every raw data row, in the log and in `run_info.json`, and it is used to derive that instrument's random sequences (§12). Adding a second meter therefore never changes the first meter's data.

**Channel mapping (power meters).** Each meter lists which **device channels** its inputs are connected to:

```yaml
- name: opm_a
  type: sim_opm_8ch
  device_channels: [1, 2, 3, 4, 5, 6, 7, 8]          # input 1 → device ch 1, …
- name: opm_b
  type: sim_opm_8ch
  device_channels: [9, 10, 11, 12, 13, 14, 15, 16]   # input 1 → device ch 9, …
```

Raw data records both the **device channel** (what the test cares about) and the **meter input** (what you'd check when troubleshooting a cable). Pass/fail is always reported per device channel.

**Multiple TECs.** Every instrument with the `temperature_control` role receives the condition's setpoint, unless the condition names specific TECs. Temperature verification for a condition passes only if **every** TEC involved settles. Each TEC's settling is recorded separately.

**Reading order.** In each measurement round, the meters are read one after another in config order. Each meter's 8 values are simultaneous within that meter; values from different meters are a few milliseconds apart, and their own timestamps show this. Reading meters in parallel would be safe, since each has its own connection, but it is not done by default to keep runs fully reproducible and the log easy to follow.

**Independence.** One meter failing doesn't affect another. If `opm_b` becomes unreachable, its remaining readings are marked `NOT_MEASURED` and `opm_a` carries on (§9.2).

---

## 7. Configuration

### 7.1 Default configuration (values from the brief)

```yaml
run:
  device_serial: "SN-0001"
  results_dir: results
  seed: null                       # null = pick one; the seed used is always saved

simulation:
  enabled: true
  mode: network                    # network | in_process
  time_scale: 0.0                  # 1.0 real time, 0 instant
  control_port: 5100
  first_instrument_port: 5101      # "auto" addresses are assigned upward from here
  device:
    channels: 8
    nominal_power_dbm: 0.0
    channel_offsets_db: [0.10, -0.25, 0.05, -0.40, 0.15, -0.10, 0.20, -0.05]
    temp_coefficient_db_per_c: -0.1
    reference_temp_c: 40.0
    noise_db_at_100ms: 0.05
    ambient_temp_c: 25.0

instruments:                       # a list: add as many of each type as needed
  - name: tec_main
    type: sim_tec                  # registry type → driver + simulator + role
    address: auto                  # simulation: port assigned by the bench
                                   # real hardware: e.g. "TCPIP::192.168.1.20::5025::SOCKET"
    timeout_ms: 1000
    max_comm_retries: 1
    retry_delay_ms: 100
    range_c: [-10, 100]
    simulation:
      time_constant_s: 8.0
      jitter_c: 0.02
      steady_state_error_c: 0.0
      faults: { timeout_rate: 0.005, garbled_rate: 0.005, invalid_rate: 0.001 }

  - name: opm_a
    type: sim_opm_8ch
    address: auto
    device_channels: [1, 2, 3, 4, 5, 6, 7, 8]   # meter input n → device channel
    timeout_ms: 1000
    max_comm_retries: 1
    retry_delay_ms: 100
    range_dbm: [-60, 10]
    simulation:
      faults:
        timeout_rate: 0.005
        garbled_rate: 0.005
        channel_garbled_rate: 0.002
        channel_invalid_rate: 0.001

sequence:
  conditions:                      # one or more temperature conditions, run in order
    - setpoint_c: 40.0
  temperature:
    tolerance_c: 0.5
    poll_interval_s: 1.0
    stable_for_s: 10               # must stay in tolerance this long
    max_drift_c: 0.1               # ...and change by no more than this across that window
    max_wait_s: 120                # per settling attempt
    max_settle_attempts: 2         # 1 retry
    continue_on_fail: true         # still measure light for troubleshooting
    monitor_during_measurement: true
  measurement:
    readings_per_channel: 50
    read_mode: all                 # all | per_channel
    averaging_time_ms: 100
    interval_ms: 100

limits:
  avg_power_dbm: { min: -3.0, max: 3.0 }
  require_all_valid: true
  retry_warning_fraction: 0.05     # flag channels needing retries on >5% of readings
  temp_drift_warning: true         # flag if monitored temp leaves tolerance during measurement
```

### 7.2 Validation (before anything starts)
The run refuses to start, with a plain explanation, if:
- `limits.avg_power_dbm.min ≥ max`, or a tolerance is ≤ 0
- `readings_per_channel < 1`
- `channel_offsets_db` length ≠ `channels`
- `interval_ms < averaging_time_ms` ("the meter would be asked for a new reading before the last one finished")
- `timeout_ms ≤ averaging_time_ms` ("every reading would time out")
- `stable_for_s > max_wait_s`
- any fault rate is outside 0–1
- `time_scale` is outside 0–1
- a setting name is unknown (a typo), anywhere, including inside an instrument's own settings
- two instruments share a name, or two explicit addresses are the same
- an instrument's `type` isn't in the registry
- the sequence needs a role that no instrument provides (e.g. no `power_meter`)
- a device channel is mapped to **two** meters, or to **none**
- a meter lists more `device_channels` than it has inputs
- `address: auto` is used with `simulation.enabled: false` (real hardware needs a real address)

Each instrument's own settings (e.g. `range_c`, `device_channels`, its `simulation:` block) are checked by the settings model in that instrument's folder (`instruments/<name>/settings.py`). The config loader finds the right model through the registry, using the `type:` field.

It **warns** (but runs) if the estimated real duration exceeds 5 minutes.

The exact configuration used is copied into each results folder.

---

## 8. Test sequence flow

### 8.1 Overview

```
 1. Load + validate config ──── invalid → stop with explanation (nothing created)
 2. Create results folder, write run_info.json (status: RUNNING)
 3. Start bench (if simulating) with one simulator per configured instrument;
    connect every instrument, confirm identities, configure
 4. For each temperature condition:
      a. Set temperature on every TEC involved, switch outputs on
      b. Settle + verify each TEC (up to 2 attempts each)
      c. Take 50 readings of every device channel across all meters
         (+ background temperature monitoring)
 5. ALWAYS (even after errors or Ctrl+C):
      switch every TEC output off, close every instrument, stop bench
 6. Analyse raw CSV → summary.csv
 7. Update run_info.json (status: COMPLETE / COMPLETE_WITH_ERRORS / ABORTED, overall result)
 8. Print a short result table to the terminal
```

### 8.2 Step 4b: settle and verify temperature

```
for attempt in 1..max_settle_attempts:
    send setpoint, switch output on; start an empty window
    until max_wait_s elapses:
        r = tec.read_temperature()        # TIMEOUT/GARBLED retried once inside
        record r (phase = "settle", settle_attempt = attempt)
        if r is VALID and |r − setpoint| ≤ tolerance:
            add r to the window (keeps the last stable_for_s worth of readings)
            if window is full and max(window) − min(window) ≤ max_drift_c: → PASS, stop
        else:
            empty the window              # an INVALID reading can't confirm stability
        wait poll_interval
    → this attempt failed; log it
→ FAIL after all attempts
```

**Why the drift rule:** a controller that is still heating passes *through* the tolerance band and can spend over 10 s inside it on the way. Being in tolerance isn't enough; the temperature must also have stopped moving. (Testing found this. See TESTING.md §8.)

The same stability class is used live by the runner and afterwards by the analysis, which replays the recorded settle readings. The summary can never disagree with the decision the test made.

- The **verification result** for the condition is PASS/FAIL, plus the mean temperature across the stable window.
- On FAIL with `continue_on_fail: true`, the light readings are still taken (useful for troubleshooting, e.g. "power was low *because* it was hot"). The device can't pass.

### 8.3 Step 4c: optical measurements (`read_mode: all`)

```
for i in 1..50:
    for each meter (in config order):
        readings = meter.read_all()
            # whole-message TIMEOUT/GARBLED → retried once (both attempts recorded)
            # single-channel GARBLED → re-read just that input with POW? n
            # INVALID → recorded, not retried
        map meter inputs → device channels
        record each reading immediately
    if monitoring: record read_temperature() from every TEC (phase = "monitor")
    wait (interval − averaging time)
```

A meter that has become unreachable is skipped for the rest of the condition (its readings are `NOT_MEASURED`). The other meters carry on.

In `read_mode: per_channel`, the loop is channel by channel (50 × ch1, then 50 × ch2 …). Comparing the two modes with the device link on shows drift between channels in per-channel mode that doesn't appear in all-at-once mode.

### 8.4 Background temperature monitoring
One TEC reading per measurement round, logged with `phase = monitor`. This happens in the same loop, not a separate thread, so it stays reproducible. The summary reports the min/max temperature during measurement and flags any excursion outside tolerance. With the device link on, temperature wobble can be matched to brightness wobble in the raw data.

---

## 9. Error handling and data integrity

### 9.1 Statuses

| Status | Meaning | Value column | Retried? |
|---|---|---|---|
| `VALID` | sensible reading (may still fail limits) | number | never |
| `INVALID` | instrument answered with an impossible value | number if parseable, else blank; raw text kept | no |
| `TIMEOUT` | no reply | blank | yes, once |
| `GARBLED` | reply couldn't be understood | blank; raw text kept | yes, once |
| `NOT_MEASURED` | planned reading never attempted (instrument unreachable or run aborted) | blank | n/a |

### 9.2 Retry policy (summary)

| Situation | Action |
|---|---|
| Whole reply timed out or garbled | retry the same command once, after `retry_delay_ms` |
| One channel garbled inside a good `POW:ALL?` | re-read only that channel once (`POW? n`) |
| Impossible value | record as `INVALID`, no retry |
| Out-of-spec value | record as `VALID`, never retried |
| Temperature doesn't settle within `max_wait_s` | one more full settling attempt |
| A **meter** becomes unreachable (connection refused or dropped) | mark that meter's remaining planned readings `NOT_MEASURED`; **other meters carry on**; run status `COMPLETE_WITH_ERRORS` |
| A **TEC** becomes unreachable, or **every** meter is unreachable | stop measuring, mark remaining planned readings `NOT_MEASURED`, clean up, produce summary from what exists, run status `ABORTED` |

Every attempt is a separate row with its `attempt` number. For "all 50 readings must be valid," the **final attempt** of each reading counts. The summary also reports how many retries were used, and flags channels that needed retries on more than 5% of readings, since a pass that needed many retries usually signals a flaky connection.

### 9.3 The guiding rule
> Problems are recorded at the smallest level they actually affect. One bad channel affects one row, one lost message affects one round, and nothing good is discarded because of something bad next to it.

### 9.4 Preserving data for troubleshooting
- The raw CSV is written **row by row as readings happen**, and flushed after every row. If the program crashes at reading 237, the first 236 are already safely in the file.
- The **exact text the instrument sent** is kept in `raw_response`, including garbage.
- `run_info.json` is written as `RUNNING` at the start and updated at the end, so an interrupted run is obvious.
- Results folders are never overwritten.

### 9.5 Guaranteed cleanup
- Instruments are opened inside a context that **always** closes them, whether the run finishes, hits an error, or is stopped with Ctrl+C.
- TEC output is switched off before disconnecting.
- The auto-started bench is always stopped. If a previous bench is still running and holding the ports, the program says so clearly rather than crashing.
- `close()` is safe to call more than once, and errors during cleanup are logged without hiding the original error.

---

## 10. Data storage and processing

### 10.1 Results folder per run

```
results/20261123-143005_SN-0001_a3f9/
├── raw_measurements.csv    # every attempt, written live
├── summary.csv             # computed after the run from raw_measurements.csv
├── run_info.json           # run ID, serial, seed, timings, instrument IDs, status, overall result
├── config_used.yaml        # exact configuration of this run
├── run.log                 # human-readable log
└── bench.log               # simulator bench output (network mode only)
```

The run ID is the folder name: timestamp, serial number and a short random suffix, so it's unique even for two runs in the same second.

### 10.2 Why CSV and not a database
A run produces about 400 optical readings plus a few hundred temperature readings. That's a tiny volume for any storage method, so speed isn't the deciding factor. CSV is chosen because:
- the brief requires CSV output,
- appending one line at a time is the simplest crash-safe record,
- anyone can open it in a spreadsheet.

A database could later index runs for history and search. It would be built *from* these files, not replace them.

### 10.3 Processing
Processing **always reads from the raw CSV**, during a live run and when re-processing an old one. There is one code path, and the summary can never disagree with the raw data.

Per channel, per temperature condition, using **valid readings only**:
- average power (dBm), computed in milliwatts and converted back (§2.8)
- minimum, maximum (dBm)
- standard deviation (dB, sample standard deviation)
- counts: valid, invalid, timeout, garbled, not measured; retries used

If a channel has no valid readings, its statistics are blank and it fails.

`ate reprocess results/<run>` re-runs the analysis on saved data, e.g. with different limits, without re-testing.

### 10.4 Pass/fail rules
- **Channel passes** if: all 50 readings' final attempts are `VALID` **and** average power is within −3 to +3 dBm.
- **Temperature verification passes** if the stability check succeeds within the allowed attempts.
- **Device passes** only if every condition's temperature verification passes **and** every channel passes in every condition.
- Warnings (retry rate, temperature drift) are reported but don't change pass/fail by default.

---

## 11. Output file formats

### 11.1 `raw_measurements.csv` (one row per attempt)

| Column | Example | Notes |
|---|---|---|
| `run_id` | `20261123-143005_SN-0001_a3f9` | |
| `device_serial` | `SN-0001` | |
| `timestamp_utc` | `2026-11-23T14:30:07.512Z` | real time, stamped by the test program |
| `sim_elapsed_s` | `37.400` | simulated time since start (blank with real hardware) |
| `condition` | `40C` | which temperature condition (its `name`, or the setpoint) |
| `phase` | `settle` / `measure` / `monitor` | |
| `instrument` | `opm_a` / `tec_main` | instrument **name** from config |
| `instrument_type` | `sim_opm_8ch` / `sim_tec` | |
| `channel` | `11` | **device** channel; blank for TEC |
| `instrument_input` | `3` | which meter input the reading came from; blank for TEC |
| `measurement_index` | `17` | 1–50 for optical readings; poll number for temperature |
| `settle_attempt` | `1` / `2` | which settling attempt a `settle` row belongs to |
| `attempt` | `1` / `2` | communication retry number |
| `value` | `-0.112` | blank if no usable value |
| `unit` | `dBm` / `C` | |
| `status` | `VALID` / `INVALID` / `TIMEOUT` / `GARBLED` / `NOT_MEASURED` | |
| `raw_response` | `3:ERR` | exact text received |
| `note` | `re-read of single channel`, `channel missing from reply`, `run aborted: …` | optional explanation |

### 11.2 `summary.csv` (one row per channel per condition)

| Column | Notes |
|---|---|
| `run_id`, `device_serial` | |
| `condition`, `setpoint_c` | |
| `temp_verification` | PASS / FAIL (PASS only if every TEC involved settled; per-TEC detail in `run_info.json`) |
| `temp_settle_attempts` | 1 or 2 |
| `temp_mean_c`, `temp_min_during_meas_c`, `temp_max_during_meas_c` | |
| `channel` | device channel |
| `instrument`, `instrument_input` | which meter and input measured this channel |
| `readings_expected` | 50 |
| `valid_count`, `invalid_count`, `timeout_count`, `garbled_count`, `not_measured_count` | final-attempt counts |
| `retries_used` | |
| `avg_dbm`, `min_dbm`, `max_dbm`, `std_db` | valid readings only |
| `averaging_time_ms`, `interval_ms` | context needed to interpret `std_db` |
| `channel_result` | PASS / FAIL |
| `fail_reasons` | e.g. `invalid_readings(1/50);avg_below_min` |
| `warnings` | e.g. `high_retry_rate(4)`, `temp_drift_during_measurement` |
| `overall_result` | PASS / FAIL (same on every row) |

### 11.3 `run_info.json`
Run ID, serial number, seed, start and end times, status (`RUNNING` / `COMPLETE` / `COMPLETE_WITH_ERRORS` / `ABORTED` and reason), and for every instrument its name, type, address or port actually used, and identity string. Also per-TEC settling results, software version and overall result.

---

## 12. Reproducibility

- Computers generate "random" numbers from a formula seeded with a starting number. **Same seed, same numbers.**
- The config has one main `seed`. If blank, one is chosen and **always saved** in `run_info.json` and `config_used.yaml`.
- Separate random sequences are derived from the main seed for each purpose, keyed by **instrument name**, e.g. `"42:tec_main:noise"`, `"42:opm_a:input3:noise"`, `"42:opm_a:faults"`. So:
  - a retry on channel 2 can't shift the numbers used for channel 5,
  - changing a fault rate doesn't change the noise values,
  - instruments don't affect each other's numbers, and **adding a second meter leaves the first meter's data unchanged**.
- Settling and drift follow the **simulated** clock, never the real one.
- **Result:** re-running with a saved seed and config reproduces every value, status and pass/fail decision exactly. Only the real timestamps differ.

---

## 13. Testing approach

Full detail and results are in [TESTING.md](TESTING.md). In summary: 192 automated tests, about 8 seconds.

### 13.1 The test layout mirrors the code
Each part is tested on its own, next to where it lives. Only `tests/system/` tests the whole program together. A new instrument gets its own `tests/instruments/<name>/` folder.

| Tests | Part tested | Main checks |
|---|---|---|
| `tests/core/` | `core/` | reply classification; shared instrument lifecycle; roles; registry (including a new folder being found automatically); PyVISA over a real socket |
| `tests/instruments/tec/`, `opm/` | each instrument folder | driver commands and reply handling; simulator against its physics model |
| `tests/simulation/` | `simulation/` | device link; fault injection; clock and seeds; shared simulator behaviour; bench control commands and ports |
| `tests/sequence/` | `sequence/` | stability rule; retry policy |
| `tests/data/` | `data/` | dBm/mW math, statistics, inclusive limits; live recording; reports; re-processing |
| `tests/config/` | `config.py` | every rule in §7.2 |
| `tests/system/` | everything | full runs; failure scenarios; network mode; CLI; statistical check |

### 13.2 Techniques
- **Forced faults.** Random faults are switched off, and one specific fault is injected through the bench (directly in-process, or via the control port over the network). Every error path is tested on purpose, not by luck.
- **Seeds.** Whole raw data files are compared between runs, and between in-process and network modes.
- **A scripted fake instrument** (a transport that replies from a list) tests drivers without any simulator.
- **A tiny real socket server** tests the PyVISA transport against a real network connection.

### 13.3 Forced-failure scenarios

| Scenario | Expected outcome |
|---|---|
| One `POW:ALL?` times out | 8 `TIMEOUT` rows (attempt 1), 8 rows from retry (attempt 2); channels unaffected if retry succeeds |
| Two timeouts in a row | 8 `TIMEOUT` final results; all channels fail validity |
| Whole reply garbled | same as timeout, but status `GARBLED` and raw text preserved |
| One channel garbled | only that channel re-read with `POW? n`; other 7 untouched |
| One channel `ERR` / +45 dBm | `INVALID`, no retry, value/raw text kept; that channel fails |
| Weak channel (offset −4 dB) | all readings `VALID`, channel fails on average power |
| TEC never settles (steady-state error 0.8 °C) | 2 settling attempts, verification FAIL, light still measured, device FAIL |
| Every meter killed mid-run | readings so far preserved, rest `NOT_MEASURED`, status `ABORTED`, summary still written, TEC output off |
| TEC killed mid-run | status `ABORTED` with reason; every planned reading accounted for (measured or `NOT_MEASURED`) |
| Ctrl+C mid-run | readings so far preserved; status `ABORTED`; TEC output off; bench stopped |
| Ports already in use | clear error before starting |
| Invalid config | clear error, no results folder created |
| Two meters; `opm_b` killed mid-run | `opm_b` channels' remaining readings `NOT_MEASURED` and FAIL; `opm_a` channels complete normally; status `COMPLETE_WITH_ERRORS` |
| Device channel mapped to two meters, or to none | config rejected with a message naming the channel |

### 13.4 Statistical sanity check
200 seeded runs in in-process mode with the default fault rates. Predicted device pass rate: about 66% (0.999⁴⁰⁰ ≈ 67% for impossible values, × about 0.99 for communication failures whose retry also fails; §2.7). **Measured: 63%**, within normal statistical spread. This confirms the simulator and the analysis agree with the math.

---

## 14. Extensibility

### 14.1 Adding another instrument of an existing type
Config only. Add another entry to the `instruments` list with a new name (and, for a meter, its `device_channels`). The bench starts a simulator for it automatically, and the runner picks it up by role. See `config/examples/two_meters_16ch.yaml`.

### 14.2 Adding a third instrument type
**Example: an optical switch**, used when the meter has a single input and a switch routes one channel at a time.
1. Create `instruments/switch/` with the same five files as `tec/`: command words (`ROUT <n>`, `ROUT?`), settings, driver, simulator, and an `__init__.py` declaring `INSTRUMENT_TYPES`. The registry finds it automatically, and the bench starts its simulator on its own port automatically.
2. A switch is a new *kind* of instrument, so add a `Switch` role to `core/roles.py` (what any switch must do: `route(n)`).
3. Add a measurement mode that uses it (`read_mode: switched`) in the runner.
4. Add it to `instruments:` in the config, and its tests in `tests/instruments/switch/`.

The TEC and OPM folders, recorder, analysis, report and bench code don't change.

**Other examples:** a laser bias current source, or a wavelength meter. Each follows the same steps.

### 14.3 Adding a new test condition
- **Another temperature:** config only. Add an entry to `sequence.conditions` (see `config/examples/two_temperatures.yaml`). Results are reported per condition.
- **A new kind of condition** (e.g. bias current): add the field to the condition definition and one step in the runner that applies it. The measurement and reporting code is reused.

### 14.4 A different brand of instrument
A different meter uses different command words for the same actions. Copy `instruments/opm/` to a new folder and change its command words, settings, driver and simulator. The driver still extends the `PowerMeter` role, so it provides the same `read_all()` / `read_input()` methods. **Nothing outside the new folder changes**: no edits to the sequence, registry, recorder or analysis.

### 14.5 Real hardware
Set `simulation.enabled: false` and put the real instruments' addresses in the config. Drivers, sequence, recorder and analysis are unchanged.

### 14.6 Multiple devices
Nothing is global: serial number, results folder, bench and instruments all belong to a run. Testing several devices means running several runs (in sequence, or in parallel with separate benches on separate ports), each with its own run ID.

---

## 15. Technology choices and alternatives considered

### 15.1 Chosen

| Need | Choice | Why |
|---|---|---|
| Instrument communication | **PyVISA** + **pyvisa-py** | Industry standard for lab instruments, real timeouts, same code for real hardware (network, USB, GPIB or serial, by address). `pyvisa-py` avoids installing vendor drivers. Used in one class only (§6.5). |
| Config validation | **pydantic** + **PyYAML** | Clear, typed validation errors |
| Simulator networking | Python **asyncio** (standard library) | One event loop, no locks, deterministic |
| Statistics | Python **statistics** / **math** (standard library) | 400 numbers don't need numpy |
| Tests | **pytest** | Standard |

### 15.2 Considered and not used (or used only in a limited way)

| Option | Decision | Reason |
|---|---|---|
| **PyVISA-sim** (YAML-defined fake instruments) | Not used for simulation | Fixed question/answer pairs. Can't model settling, noise, offsets or random faults. |
| **sinstruments** (network instrument simulator library) | Not used | Saves only the simple networking part. We need full control of timeouts and corrupted replies, and want to avoid a small third-party dependency. |
| **PyMeasure / QCoDeS** (full lab frameworks) | Studied for ideas, not used | Would supply the architecture, which is the main thing being assessed |
| **Docker** | Not used | Adds installation burden for the reviewer. Separate processes on separate ports already give independent instruments. |
| **Database** | Not used | Data volume is tiny and CSV is required. A database could be added later for run history. |
| **Web UI** | Not used | Not assessed. The CLI and config files cover all functionality. The architecture allows a UI later as another user of the same core. |
| **Threads per channel** | Not used | One instrument connection handles one conversation at a time. Threads would break reproducibility. Simultaneous reading is done by the meter (`POW:ALL?`). |

---

## 16. Build plan

Each step is a commit (or a small group of commits) and leaves the project working.

| # | Milestone | Content |
|---|---|---|
| 1 | Skeleton | `pyproject.toml`, folder layout, `.gitignore`, README outline, this design doc |
| 2 | Config | config models (named instrument list, roles, channel mapping), validation, `default.yaml`, validation unit tests |
| 3 | Data models | `Measurement`, `Status`, recorder (streaming CSV), unit tests |
| 4 | Virtual device + clock | device model, simulated clock, seeded random sequences, simulator tests |
| 5 | TEC simulator + driver | command table, settling model, faults, in-process transport, tests |
| 6 | OPM simulator + driver | labelled `POW:ALL?`, per-channel faults, parsing, tests |
| 7 | Analysis + report | statistics, pass/fail, summary CSV, `run_info.json`, `reprocess` |
| 8 | Test runner | settle/verify, measurement loop, retries, monitoring, cleanup, CLI `run` |
| 9 | Network bench | asyncio bench (one simulator per configured instrument, automatic ports), PyVISA transport, control port, auto-start/stop, CLI `bench`, two-meter example config |
| 10 | Scenario tests | all forced-failure scenarios (§13.3), statistical check (§13.4) |
| 11 | Docs + samples | README (install/run/architecture), `TESTING.md`, example configs, `sample_results/` |
| 11b | Restructure | one folder per instrument, `core/` and roles, automatic registry, tests mirroring the code (§20) |
| 12 | Stretch | plot of readings per channel with temperature overlay; CI workflow running tests |

**Status:** milestones 1–11b are done. Milestone 12 has not been started. The repository and its milestone commits have not been created yet.

---

## 17. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Timeout shorter than averaging time, so every reading "fails" | Config validation refuses to start (§7.2) |
| Late reply shifts every later reading onto the wrong question | Simulator never replies late; driver clears leftover input before retries (§5.5) |
| Leftover bench holding ports after a crash | Always stopped in cleanup; clear "port in use" message (§9.5) |
| Reproducibility silently broken | Separate seeded sequences per purpose, simulated clock, tests that the same seed gives identical output (§12, §13.2) |
| Default fault rates make almost every device fail | Rates chosen from the math in §2.7; documented; configurable |
| Retrying until a "good" value appears hides real faults | Only communication problems are retried; every attempt logged; retry-rate warning (§9.2) |
| Confusing "invalid" with "out of spec" | Separate statuses and separate checks: instrument range vs test limits (§6.3) |
| Reviewer can't install it | Pure-Python dependencies only; `pip install .` and one `ate run` command. (Editable `pip install -e .` can fail in iCloud-synced folders on macOS; see README troubleshooting.) |
| Adding an instrument quietly requires edits elsewhere | One folder per instrument with automatic discovery; a test drops in a new folder and checks it's found with no other change (§6.6) |
| Channel mapping mistakes with several meters (a channel measured twice, or not at all) | Validation requires every device channel to be mapped to exactly one meter input (§7.2); raw data records both device channel and meter input |
| Scope creep | Build order in §16 keeps every stage complete; stretch items last |

---

## 18. Open decisions

These were implemented with the recommended default. Each is a config setting, so it can be changed without code:

1. **Temperature retry meaning:** *one extra full settling attempt* (`max_settle_attempts: 2`). Communication problems on TEC reads also retry once, and impossible values don't.
2. **Continue after temperature failure:** **yes** (`continue_on_fail: true`). Light readings are still taken for troubleshooting, and the device is marked FAIL. With `false`, they're recorded as `NOT_MEASURED` with the reason.
3. **Background temperature monitoring:** **on** (`monitor_during_measurement: true`), one TEC reading per measurement round.
4. **Multiple temperature conditions:** **supported** (`sequence.conditions`; default list has only 40 °C), with `config/examples/two_temperatures.yaml`.
5. **Repository hosting:** GitHub decision deferred.

---

## 19. AI assistance disclosure

*(Draft, to be finalised in the README.)*

AI assistance (Claude, by Anthropic) was used to discuss and refine the design, research existing Python instrument-simulation libraries, draft documentation, and help write and review code. All design decisions were discussed and made by the author, who reviewed, ran and tested the code.

---

## 20. Changes made during implementation

| Change | Reason |
|---|---|
| Added `max_drift_c` (0.1 °C) to the stability rule | A heating controller passes through the tolerance band and was wrongly "verified" on the way. Found by the forced-failure test (TESTING.md §8). |
| Limits compared at 0.000001 dB resolution | −3 dBm converted to mW and back is −3.0000000000000004, which wrongly failed an exactly-at-limit average |
| Added `settle_attempt` column to the raw CSV | The analysis needs it to replay each settling attempt separately |
| Fault injection limited to measurement queries | Keeps setup simple. Drivers still handle setup timeouts. |
| `time_scale` limited to 0–1 | Slower-than-real time could make simulated delays exceed timeouts |
| Added `ate validate` and `ate run --external-bench`, and `extends:` in config files | Convenience: check configs without running; run against a manually started bench; example configs override only what differs |
| Retry policy lives in the runner (no separate `retry.py`) | It needs the recorder and the clock, which the runner already holds. Drivers make single attempts and report a status. |
| **Restructured: one folder per instrument** (`instruments/tec/`, `instruments/opm/`), shared machinery moved to `core/`, registry finds folders automatically | Previously one instrument was spread over four places (driver file, simulator file, settings in `config.py`, a registry line). Now adding an instrument touches one folder. Behaviour unchanged: the same seed reproduces the earlier sample data byte for byte. |
| **Roles** (`core/roles.py`): `TemperatureController`, `PowerMeter` | The sequence used to look for the specific TEC/OPM driver classes. It now asks for any instrument filling a role, which makes "a different brand of meter" a one-folder change. |
| Simulator base class in `simulation/simulator.py`, not `core/` | It depends on the simulated clock and fault injector. Putting it in `core/` would make the shared machinery depend on the simulation. |
| **Tests reorganised to mirror the code** (`tests/core/`, `tests/instruments/tec/`, …, `tests/system/`); 107 → 192 tests | Each part is tested where it lives; only `tests/system/` tests the whole program. Added tests for the registry, roles, PyVISA over a real socket, the bench's control commands and the simulator base. |
